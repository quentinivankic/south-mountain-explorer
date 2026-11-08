import Foundation

/// A job placed in the sequence with its chosen entry and exit (indices into
/// the job's boundary nodes).
struct PlanPick {
    let job: Int
    let s: Int
    let t: Int
}

/// Exact result of walking a sequence: which trails it finished (including
/// incidentally, by walking over them) and how much new ground it covers.
struct PlanEval {
    var finished: [Int]
    var newMeters: Double
    /// Every step walked, repeats included.
    var totalMeters: Double
    var score: Double {
        RouteScore.value(newMeters: newMeters, totalMeters: totalMeters, finished: finished.count)
    }
    var efficiency: Double { RouteScore.efficiency(newMeters: newMeters, totalMeters: totalMeters) }
}

struct PlanState {
    var order: [Int]
    var picks: [PlanPick]
    var secs: Double
    var gain: Double
    var eval: PlanEval
}

/// Greedy insertion and local improvement ("Plan a Hike", Task 5).
extension RoutePlanEngine {
    /// Sequence-solve cap per polished seed (deterministic early exit; no clock).
    static let maxWork = 1500
    static let maxPasses = 25
    /// Pending trails tried per insert / replace move (cheapest solo cost first).
    static let pendingFanout = Int.max
    /// Trails tried as "first stop" seeds on big instances.
    static let forcedSeeds = 30
    private static let gainLambdas: [Double] = [0, 3, 12, 50, 200]

    /// Seeds polished by local search.
    static let polishedSeeds = 6

    func run() -> RoutePlan? { runTop(1).first }
    /// The best plan, then the best plan of each other finished-trail set the
    /// search met (seeds and polished results), best first, at most `count`.
    /// The first entry is always what `run()` returns. The detour search
    /// (`EdgeWalkSearch`) can turn a plan that ranks lower here into the best
    /// route, so Plan a Hike polishes several of these, not just the first.
    func runTop(_ count: Int) -> [RoutePlan] {
        guard count > 0, let (best, pool) = search() else { return [] }
        var bestPerSet: [[Int]: PlanState] = [:]
        for s in pool where !s.eval.finished.isEmpty {
            let set = s.eval.finished.sorted()
            if let b = bestPerSet[set], !better(s, b) { continue }
            bestPerSet[set] = s
        }
        let firstSet = best.eval.finished.sorted()
        let others = bestPerSet.filter { $0.key != firstSet }.map(\.value).sorted { a, b in
            if better(a, b) { return true }
            if better(b, a) { return false }
            return a.order.lexicographicallyPrecedes(b.order)
        }
        return ([best] + others.prefix(count - 1)).compactMap { assemble($0) }
    }
    /// Best polished state plus every seed and polished state (for `runTop`).
    private func search() -> (PlanState, [PlanState])? {
        if PlanCancellation.isCancelled { return nil }
        prepare()
        guard !jobs.isEmpty, let empty = makeState(order: []) else { return nil }
        // Seeds: three greedy scorings, plus "this trail first, then greedy"
        // for every trail (greedy alone can lock onto one big trail and miss
        // two cheaper ones that fit together).
        var seeds: [PlanState] = []
        for mode in 0..<3 { seeds.append(greedyFill(empty, mode: mode)) }
        for j in promising(jobs.indices.map { $0 }).prefix(Self.forcedSeeds) {
            if PlanCancellation.isCancelled { return nil }
            if let first = makeState(order: [j]) {
                // The trail alone is a seed too: with repeat walking costing
                // value, a short single-trail hike often beats a filled one.
                seeds.append(first)
                seeds.append(greedyFill(first, mode: 0))
            }
        }
        var unique: [PlanState] = []
        var seen = Set<[Int]>()
        for s in seeds where seen.insert(s.order).inserted { unique.append(s) }
        let ranked = unique.indices.sorted { a, b in
            if better(unique[a], unique[b]) { return true }
            if better(unique[b], unique[a]) { return false }
            return a < b
        }
        var best: PlanState?
        var pool = unique
        for i in ranked.prefix(Self.polishedSeeds) {
            if PlanCancellation.isCancelled { return nil }
            let polished = localSearch(unique[i])
            pool.append(polished)
            if best == nil || better(polished, best!) { best = polished }
        }
        guard let state = best, !state.eval.finished.isEmpty else { return nil }
        return (state, pool)
    }

    /// Jobs that can run alone, cheapest per finished trail first (ties by index).
    func promising(_ ids: [Int]) -> [Int] {
        ids.filter { jobs[$0].soloSecs <= limit + Self.eps }.sorted { a, b in
            let ra = jobs[a].soloSecs / Double(jobs[a].members.count)
            let rb = jobs[b].soloSecs / Double(jobs[b].members.count)
            return ra != rb ? ra < rb : a < b
        }
    }

    // MARK: - Objective

    /// Higher `RouteScore` first (new miles, a bonus per whole trail finished,
    /// a cost per repeat mile), then higher efficiency, then less time.
    /// A small tolerance keeps float noise from reshuffling equal plans.
    func better(_ a: PlanState, _ b: PlanState) -> Bool {
        // A hike must finish at least one whole trail; the empty walk (score
        // zero) must never beat a real one that scores below zero.
        if a.eval.finished.isEmpty != b.eval.finished.isEmpty { return !a.eval.finished.isEmpty }
        return RouteScore.isBetter(
            score: a.eval.score, efficiency: a.eval.efficiency, seconds: a.secs,
            than: b.eval.score, efficiency: b.eval.efficiency, seconds: b.secs)
    }

    // MARK: - Sequence costs

    func totals(_ picks: [PlanPick]) -> (secs: Double, gain: Double)? {
        var secs = 0.0, gain = 0.0
        var prev = startK
        for p in picks {
            let job = jobs[p.job]
            guard let cw = job.cover[p.s][p.t] else { return nil }
            let kin = job.keyIndex[p.s]
            let c = conn(prev, kin)
            guard c.isFinite else { return nil }
            secs += c + cw.seconds
            gain += connGain(prev, kin) + cw.gainFt
            prev = job.keyIndex[p.t]
        }
        if let f = finishK {
            let c = conn(prev, f)
            guard c.isFinite else { return nil }
            secs += c
            gain += connGain(prev, f)
        }
        return (secs, gain)
    }

    /// Best entry / exit for every job of a fixed order, minimising
    /// seconds + lambda * gain. Dynamic programming over boundary nodes.
    func optimize(order: [Int], lambda: Double) -> [PlanPick]? {
        if order.isEmpty { return [] }
        var prevNodes = [startK]
        var prevCost = [0.0]
        var arrFrom: [[Int]] = []     // per layer: entry -> previous exit index
        var exFrom: [[Int]] = []      // per layer: exit -> entry
        for j in order {
            let job = jobs[j]
            let m = job.nodes.count
            var arr = [Double](repeating: .infinity, count: m)
            var from = [Int](repeating: -1, count: m)
            for (pi, pk) in prevNodes.enumerated() where prevCost[pi].isFinite {
                for s in 0..<m {
                    let k = job.keyIndex[s]
                    let c = conn(pk, k)
                    guard c.isFinite else { continue }
                    let total = prevCost[pi] + c + lambda * connGain(pk, k)
                    if total < arr[s] { arr[s] = total; from[s] = pi }
                }
            }
            var ex = [Double](repeating: .infinity, count: m)
            var entry = [Int](repeating: -1, count: m)
            for s in 0..<m where arr[s].isFinite {
                for t in 0..<m {
                    guard let cw = job.cover[s][t] else { continue }
                    let total = arr[s] + cw.seconds + lambda * cw.gainFt
                    if total < ex[t] { ex[t] = total; entry[t] = s }
                }
            }
            arrFrom.append(from)
            exFrom.append(entry)
            prevNodes = job.keyIndex
            prevCost = ex
        }
        var bestT = -1
        var bestCost = Double.infinity
        for (t, c) in prevCost.enumerated() where c.isFinite {
            var total = c
            if let f = finishK {
                let cc = conn(prevNodes[t], f)
                guard cc.isFinite else { continue }
                total += cc + lambda * connGain(prevNodes[t], f)
            }
            if total < bestCost { bestCost = total; bestT = t }
        }
        guard bestT >= 0 else { return nil }
        var picks = [PlanPick?](repeating: nil, count: order.count)
        var t = bestT
        for layer in stride(from: order.count - 1, through: 0, by: -1) {
            let s = exFrom[layer][t]
            guard s >= 0 else { return nil }
            picks[layer] = PlanPick(job: order[layer], s: s, t: t)
            t = arrFrom[layer][s]
        }
        return picks.compactMap { $0 }
    }

    /// The best feasible variant choice for a fixed order, scored exactly.
    func makeState(order: [Int]) -> PlanState? {
        work += 1
        var best: (picks: [PlanPick], secs: Double, gain: Double)?
        let lambdas = maxGain == nil ? [0.0] : Self.gainLambdas
        for lam in lambdas {
            guard let picks = optimize(order: order, lambda: lam), let t = totals(picks) else { continue }
            guard t.secs <= limit + Self.eps, maxGain.map({ t.gain <= $0 + Self.eps }) ?? true else { continue }
            if best == nil || t.secs < best!.secs - 1e-9 || (abs(t.secs - best!.secs) <= 1e-9 && t.gain < best!.gain) {
                best = (picks, t.secs, t.gain)
            }
            if lam == 0 { break }   // fastest and within the gain limit: optimal
        }
        guard let b = best else { return nil }
        return PlanState(order: order, picks: b.picks, secs: b.secs, gain: b.gain, eval: evaluate(b.picks))
    }

    // MARK: - Exact evaluation

    private func mark(_ steps: [RouteStep], _ newMeters: inout Double, _ total: inout Double) {
        for st in steps { total += net.edges[st.edgeId].lengthMeters }
        for st in steps where stamp[st.edgeId] != gen {
            stamp[st.edgeId] = gen
            if isNewEdge[st.edgeId] { newMeters += net.edges[st.edgeId].lengthMeters }
        }
    }

    /// Walks the sequence and records every edge touched.
    func evaluate(_ picks: [PlanPick]) -> PlanEval {
        evaluations += 1
        gen += 1
        var newM = 0.0, total = 0.0
        var prev = startK
        for p in picks {
            let job = jobs[p.job]
            mark(leg(prev, job.keyIndex[p.s]), &newM, &total)
            if let cw = job.cover[p.s][p.t] { mark(cw.steps, &newM, &total) }
            prev = job.keyIndex[p.t]
        }
        if let f = finishK { mark(leg(prev, f), &newM, &total) }
        let done = (0..<singleCount).filter { j in jobs[j].edgeIds.allSatisfy { stamp[$0] == gen } }
        return PlanEval(finished: done, newMeters: newM, totalMeters: total)
    }

    // MARK: - Greedy insertion

    private func neighbours(_ pos: Int, _ picks: [PlanPick]) -> (prev: Int, next: Int?) {
        let prev = pos == 0 ? startK : jobs[picks[pos - 1].job].keyIndex[picks[pos - 1].t]
        let next: Int? = pos == picks.count ? finishK : jobs[picks[pos].job].keyIndex[picks[pos].s]
        return (prev, next)
    }

    /// Least extra seconds to insert `job` at `pos` with the neighbours'
    /// entries / exits fixed (gain ignored). nil when it cannot connect.
    func marginalSeconds(_ job: PlanJob, at pos: Int, in picks: [PlanPick]) -> Double? {
        let (prev, next) = neighbours(pos, picks)
        let direct = next.map { conn(prev, $0) } ?? 0
        guard direct.isFinite else { return nil }
        let column = next ?? keyNodes.count
        var best = Double.infinity
        for s in 0..<job.nodes.count {
            let a = conn(prev, job.keyIndex[s])
            if a.isFinite { best = min(best, a + job.tailSecs[s][column]) }
        }
        return best.isFinite ? best - direct : nil
    }

    /// `mode` varies the score so the seeds explore differently:
    /// 0 value per second (new miles plus the per-trail bonus), 1 cheapest
    /// extra time first, 2 most new ground per second.
    func greedyFill(_ start: PlanState, mode: Int) -> PlanState {
        var cur = start
        var blocked = Set<Int>()
        while !PlanCancellation.isCancelled {
            let inOrder = Set(cur.order)
            let inOrderMembers = Set(cur.order.flatMap { jobs[$0].members })
            let done = Set(cur.eval.finished)
            var best: (score: Double, dt: Double, job: Int, pos: Int)?
            for job in jobs where !inOrder.contains(job.index) && !blocked.contains(job.index) {
                let weight = Double(job.members.filter { !done.contains($0) }.count)
                if weight == 0 || job.soloSecs > limit + Self.eps { continue }
                if job.isCluster, job.members.contains(where: { inOrderMembers.contains($0) }) { continue }
                for pos in 0...cur.picks.count {
                    var dt = Double.infinity
                    if maxGain == nil {
                        dt = marginalSeconds(job, at: pos, in: cur.picks) ?? .infinity
                    } else {
                        dt = marginalWithGain(job, at: pos, in: cur) ?? .infinity
                    }
                    guard dt.isFinite, cur.secs + dt <= limit + Self.eps else { continue }
                    let score: Double
                    switch mode {
                    case 1: score = weight / max(dt, 1)
                    case 2: score = (job.newMiles + 0.01) / max(dt, 1)
                    default:
                        score = (job.newMiles + RouteScore.bonusPerFinishedTrailMiles * weight) / max(dt, 1)
                    }
                    if best == nil || score > best!.score + 1e-12
                        || (abs(score - best!.score) <= 1e-12 && dt < best!.dt - 1e-9) {
                        best = (score, dt, job.index, pos)
                    }
                }
            }
            guard let b = best else { break }
            var order = cur.order
            order.insert(b.job, at: b.pos)
            if let ns = makeState(order: order) { cur = ns } else { blocked.insert(b.job) }
        }
        return cur
    }

    /// Like `marginalSeconds`, but only variants that keep the total gain in
    /// the limit; the cheapest of those.
    private func marginalWithGain(_ job: PlanJob, at pos: Int, in cur: PlanState) -> Double? {
        let (prev, next) = neighbours(pos, cur.picks)
        let directSecs = next.map { conn(prev, $0) } ?? 0
        let directGain = next.map { connGain(prev, $0) } ?? 0
        guard directSecs.isFinite else { return nil }
        var best = Double.infinity
        for s in 0..<job.nodes.count {
            let a = conn(prev, job.keyIndex[s])
            guard a.isFinite else { continue }
            for t in 0..<job.nodes.count {
                guard let cw = job.cover[s][t] else { continue }
                let b = next.map { conn(job.keyIndex[t], $0) } ?? 0
                guard b.isFinite else { continue }
                let dg = connGain(prev, job.keyIndex[s]) + cw.gainFt
                    + (next.map { connGain(job.keyIndex[t], $0) } ?? 0) - directGain
                if let mg = maxGain, cur.gain + dg > mg + Self.eps { continue }
                best = min(best, a + cw.seconds + b - directSecs)
            }
        }
        return best.isFinite ? best : nil
    }

    // MARK: - Local improvement

    func localSearch(_ initial: PlanState) -> PlanState {
        var cur = initial
        var pass = 0
        let workStart = work
        func spent() -> Bool { work - workStart >= Self.maxWork || PlanCancellation.isCancelled }
        while pass < Self.maxPasses, !spent() {
            pass += 1
            var best = cur
            var improved = false
            func consider(_ s: PlanState?) {
                if let s, better(s, best) { best = s; improved = true }
            }
            func tryOrder(_ o: [Int]) {
                guard !spent(), o != cur.order else { return }
                consider(makeState(order: o))
            }
            let k = cur.order.count
            let inOrder = Set(cur.order)
            let inOrderMembers = Set(cur.order.flatMap { jobs[$0].members })
            let done = Set(cur.eval.finished)
            let pending = promising(jobs.indices.filter { j in
                !inOrder.contains(j) && jobs[j].members.contains { !done.contains($0) }
                    && !(jobs[j].isCluster && jobs[j].members.contains { inOrderMembers.contains($0) })
            }).prefix(Self.pendingFanout)

            for i in 0..<k {                                       // remove one
                var o = cur.order; o.remove(at: i); tryOrder(o)
            }
            for j in pending {                                     // insert one
                for p in 0...k {
                    // Skip spots that cannot fit even with the best entry / exit.
                    if maxGain == nil {
                        guard let dt = marginalSeconds(jobs[j], at: p, in: cur.picks),
                              cur.secs + dt <= limit + Self.eps else { continue }
                    }
                    var o = cur.order; o.insert(j, at: p); tryOrder(o)
                }
            }
            for i in 0..<k { for j in pending {                    // replace one
                var o = cur.order; o[i] = j; tryOrder(o)
            } }
            if k >= 2 {
                for i in 0..<k { for j in (i + 1)..<k {            // swap two
                    var o = cur.order; o.swapAt(i, j); tryOrder(o)
                } }
                for i in 0..<k { for p in 0..<k where p != i {     // move one
                    var o = cur.order; let x = o.remove(at: i); o.insert(x, at: p); tryOrder(o)
                } }
                for i in 0..<k { for j in (i + 2)..<max(k, i + 2) {  // reverse a stretch
                    var o = cur.order; o[i...j].reverse(); tryOrder(o)
                } }
            }
            for c in singleCount..<jobs.count {                    // merge two into a cluster
                let m = jobs[c].members
                guard let p = cur.order.firstIndex(of: m[0]), let q = cur.order.firstIndex(of: m[1]) else { continue }
                for target in [p, q].sorted() {
                    var o: [Int] = []
                    for (i, j) in cur.order.enumerated() {
                        if i == target { o.append(c) }
                        if j != m[0] && j != m[1] { o.append(j) }
                    }
                    tryOrder(o)
                }
            }
            for (i, j) in cur.order.enumerated() where jobs[j].isCluster {   // split a cluster
                let m = jobs[j].members
                for pair in [m, [m[1], m[0]]] {
                    var o = cur.order; o.replaceSubrange(i...i, with: pair); tryOrder(o)
                }
            }
            for i in 0..<k {                                       // trade one for others
                guard !spent() else { break }
                var o = cur.order; o.remove(at: i)
                if let s0 = makeState(order: o) {
                    let refilled = greedyFill(s0, mode: 0)
                    if refilled.order != cur.order { consider(refilled) }
                }
            }
            if improved { cur = best } else { break }
        }
        return cur
    }
}
