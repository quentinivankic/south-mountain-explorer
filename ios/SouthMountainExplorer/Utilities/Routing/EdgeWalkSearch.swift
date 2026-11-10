import Foundation

/// Edge-level improvement of a finished walk ("Plan a Hike" v2, round 2).
///
/// The whole-trail planner and the greedy new-ground walk both build a route
/// first and never ask "would turning onto the unfinished trail here beat the
/// old trail I am about to walk?". This search asks it, on the walk itself,
/// with the exact `RouteScore`.
///
/// Moves (all keep the start, the finish, the time limit and the climb limit):
///
/// - **Detour.** Replace the stretch `steps[i..<j]` (or nothing, when `i == j`)
///   by: the quickest way from the route's node `i` to the start `a` of a
///   *run*, the run, and the quickest way from the run's end `b` to the
///   route's node `j`. A run is a chain of up to `maxRunEdges` consecutive
///   edges that carry unfinished ground, in either direction, and it may cross
///   from one trail onto another. The case `i == j` is "walk onto the trail and
///   back"; `i < j` is "turn right here instead of walking the old trail
///   to `j`". A run is partial by design: finishing the whole trail is a
///   bonus the score already pays for, not a requirement.
/// - **Shortcut.** Replace `steps[i..<j]` by the quickest way between the same
///   two nodes when that is faster. This drops walking in circles over old
///   ground and gives time back for detours.
///
/// A stretch may not contain the first walk of any unfinished edge, so a move
/// never gives up new ground it already had. Candidate detours are ranked by
/// a cheap estimate of the score change; the best few are scored exactly and
/// the best exact improvement is applied. The search repeats until no move
/// helps or `maxPasses` is reached. Deterministic: no clock, no randomness.
final class EdgeWalkSearch {
    static let maxPasses = 40
    static let maxRunEdges = 4
    static let maxRunsPerNode = 64
    /// Detours scored exactly per pass.
    static let exactPerPass = 24
    /// Distance trees are cut at this share of the time limit.
    static let reachShare = 0.6
    /// Flat walking speed used only to rank detours (m/s); the exact score decides.
    private static let rankSpeed = 1.34
    private static let tighten = 60

    struct Run {
        let steps: [RouteStep]
        let from: Int
        let to: Int
        let seconds: Double
        let edgeIds: [Int]
    }

    private struct Eval {
        var seconds: Double
        var gainFt: Double
        var newMeters: Double
        var totalMeters: Double
        var finished: Int
        var score: Double { RouteScore.value(newMeters: newMeters, totalMeters: totalMeters, finished: finished) }
        var efficiency: Double { RouteScore.efficiency(newMeters: newMeters, totalMeters: totalMeters) }
    }

    private struct Move {
        let approx: Double
        let i: Int
        let j: Int
        let run: Run
    }

    private let net: TrailNetwork
    private let rev: TrailNetwork
    private let costs: RouteCosts
    private let revCosts: RouteCosts
    private let limit: Double
    private let maxGain: Double?
    private let startNode: Int
    private let finishNode: Int?
    private let isNew: [Bool]
    /// New part of each edge, metres (only unwalked ground of uncompleted trails).
    private let newMeters: [Double]
    private let candidateEdges: [[Int]]
    private let edgeCandidates: [Int: [Int]]
    private var fwdTrees: [Int: ShortestPaths] = [:]
    private var bwdTrees: [Int: ShortestPaths] = [:]
    private var runCache: [Int: [Run]] = [:]
    private var newOut: [[RouteStep]] = []
    private var stamp: [Int]
    private var gen = 0
    private(set) var passes = 0

    init?(
        view: TrailNetworkWithEndpoints, credit: [TrailEligibility.Candidate], completion: TrailCompletionLookup,
        limitSeconds: Double, maxGainFt: Double?, paceFactor: Double, shape: RoutePlanRequest.Shape
    ) {
        net = view.network
        startNode = view.start.nodeId
        guard limitSeconds > 0, limitSeconds.isFinite, startNode >= 0, startNode < net.nodes.count else { return nil }
        limit = limitSeconds
        maxGain = maxGainFt
        finishNode = shape == .loop ? view.start.nodeId : view.end?.nodeId
        costs = RouteCosts(network: net, paceFactor: paceFactor)
        rev = net.reversedForRouting()
        revCosts = RouteCosts(network: rev, paceFactor: paceFactor)
        newMeters = net.newMetersByEdge(completion)
        isNew = newMeters.map { $0 > 0 }
        candidateEdges = credit.map(\.edgeIds)
        var map: [Int: [Int]] = [:]
        for (ci, c) in credit.enumerated() { for e in c.edgeIds { map[e, default: []].append(ci) } }
        edgeCandidates = map
        stamp = [Int](repeating: 0, count: net.edges.count)
        newOut = [[RouteStep]](repeating: [], count: net.nodes.count)
        for e in net.edges where isNew[e.id] {
            newOut[e.from].append(RouteStep(edgeId: e.id, direction: .forward))
            newOut[e.to].append(RouteStep(edgeId: e.id, direction: .backward))
        }
    }

    // MARK: - Public

    /// A walk at least as good as `seed`. Returns `seed` itself when it is
    /// not a valid walk inside the limits or nothing improves it.
    func improve(_ seed: [RouteStep]) -> [RouteStep] {
        guard var ev = eval(seed) else { return seed }
        var cur = tightened(seed, &ev)
        passes = 0
        while passes < Self.maxPasses {
            if PlanCancellation.isCancelled { break }
            passes += 1
            guard let (next, nextEval) = bestDetour(cur, ev) else { break }
            cur = next
            ev = nextEval
            cur = tightened(cur, &ev)
        }
        return cur
    }

    // MARK: - Exact evaluation

    private func eval(_ steps: [RouteStep]) -> Eval? {
        gen += 1
        var e = Eval(seconds: 0, gainFt: 0, newMeters: 0, totalMeters: 0, finished: 0)
        var at: Int?
        var walked: [Int] = []
        for s in steps {
            guard s.edgeId >= 0, s.edgeId < net.edges.count else { return nil }
            let edge = net.edges[s.edgeId]
            let from = s.direction == .forward ? edge.from : edge.to
            let to = s.direction == .forward ? edge.to : edge.from
            if let a = at, a != from { return nil }
            at = to
            let w = costs.walk(s.edgeId, s.direction)
            e.seconds += w.seconds
            e.gainFt += w.gainFt
            e.totalMeters += w.meters
            if stamp[s.edgeId] != gen {
                stamp[s.edgeId] = gen
                walked.append(s.edgeId)
                e.newMeters += newMeters[s.edgeId]
            }
        }
        guard e.seconds <= limit + 1e-6 else { return nil }
        if let cap = maxGain, e.gainFt > cap + 1e-9 { return nil }
        if let first = steps.first {
            let edge = net.edges[first.edgeId]
            if (first.direction == .forward ? edge.from : edge.to) != startNode { return nil }
        } else if let f = finishNode, f != startNode { return nil }
        if let f = finishNode, let end = at, end != f { return nil }
        for edges in candidateEdges where !edges.isEmpty {
            if edges.allSatisfy({ stamp[$0] == gen }) { e.finished += 1 }
        }
        return e
    }

    private func better(_ a: Eval, than b: Eval) -> Bool {
        RouteScore.isBetter(score: a.score, efficiency: a.efficiency, seconds: a.seconds,
                            than: b.score, efficiency: b.efficiency, seconds: b.seconds)
    }

    // MARK: - Trees and runs

    private func fwd(_ n: Int) -> ShortestPaths? {
        if let t = fwdTrees[n] { return t }
        guard let t = net.shortestPaths(from: n, costs: costs, maxSeconds: limit * Self.reachShare) else { return nil }
        fwdTrees[n] = t
        return t
    }

    /// Times and paths from every node TO `n` (reversed network).
    private func bwd(_ n: Int) -> ShortestPaths? {
        if let t = bwdTrees[n] { return t }
        guard let t = rev.shortestPaths(from: n, costs: revCosts, maxSeconds: limit * Self.reachShare) else { return nil }
        bwdTrees[n] = t
        return t
    }

    private func nodeAfter(_ s: RouteStep) -> Int {
        let e = net.edges[s.edgeId]
        return s.direction == .forward ? e.to : e.from
    }

    /// Runs starting at `a`, shortest first, capped.
    private func runs(from a: Int) -> [Run] {
        if let c = runCache[a] { return c }
        var out: [Run] = []
        var frontier: [(steps: [RouteStep], secs: Double, at: Int)] = newOut[a].map {
            ([$0], costs.walk($0.edgeId, $0.direction).seconds, nodeAfter($0))
        }
        var depth = 1
        while !frontier.isEmpty, out.count < Self.maxRunsPerNode {
            var next: [(steps: [RouteStep], secs: Double, at: Int)] = []
            for f in frontier {
                if out.count >= Self.maxRunsPerNode { break }
                out.append(Run(steps: f.steps, from: a, to: f.at, seconds: f.secs, edgeIds: f.steps.map(\.edgeId)))
                guard depth < Self.maxRunEdges else { continue }
                for s in newOut[f.at] where !f.steps.contains(where: { $0.edgeId == s.edgeId }) {
                    next.append((f.steps + [s], f.secs + costs.walk(s.edgeId, s.direction).seconds, nodeAfter(s)))
                }
            }
            frontier = next
            depth += 1
        }
        out = out.filter { $0.seconds <= limit * 0.5 }
        runCache[a] = out
        return out
    }

    // MARK: - Route geometry helpers

    private struct Geometry {
        var nodes: [Int]
        var prefix: [Double]
        var protectedStep: [Bool]
        var walkedEdges: Set<Int>
    }

    private func geometry(_ steps: [RouteStep]) -> Geometry {
        var nodes = [startNode]
        var prefix = [0.0]
        var prot = [Bool](repeating: false, count: steps.count)
        var seen = Set<Int>()
        for (k, s) in steps.enumerated() {
            nodes.append(nodeAfter(s))
            prefix.append(prefix[k] + costs.walk(s.edgeId, s.direction).seconds)
            if isNew[s.edgeId], seen.insert(s.edgeId).inserted { prot[k] = true }
        }
        return Geometry(nodes: nodes, prefix: prefix, protectedStep: prot, walkedEdges: Set(steps.map(\.edgeId)))
    }

    private func splice(
        _ steps: [RouteStep], i: Int, j: Int, nodes: [Int], run: Run?
    ) -> [RouteStep]? {
        var mid: [RouteStep] = []
        if let run {
            guard let out = fwd(nodes[i])?.path(to: run.from) else { return nil }
            mid += out + run.steps
            if j == nodes.count - 1, finishNode == nil { /* free end: stop at the run's end */ } else {
                guard let back = bwd(nodes[j])?.path(to: run.to) else { return nil }
                mid += back.reversed()
            }
        } else {
            guard let direct = fwd(nodes[i])?.path(to: nodes[j]) else { return nil }
            mid = direct
        }
        return Array(steps[0..<i]) + mid + Array(steps[j...])
    }

    // MARK: - Shortcuts

    private func tightened(_ steps: [RouteStep], _ ev: inout Eval) -> [RouteStep] {
        var cur = steps
        for _ in 0..<Self.tighten {
            let g = geometry(cur)
            let n = cur.count
            var saves: [(save: Double, i: Int, j: Int)] = []
            var distinct = Set<Int>()
            for k in 0...n where distinct.insert(g.nodes[k]).inserted { _ = fwd(g.nodes[k]) }
            for i in 0..<n {
                guard let t = fwdTrees[g.nodes[i]] else { continue }
                var j = i + 1
                while j <= n {
                    if g.protectedStep[j - 1] { break }
                    let direct = t.seconds[g.nodes[j]]
                    let save = (g.prefix[j] - g.prefix[i]) - direct
                    if save > 1e-6 { saves.append((save, i, j)) }
                    j += 1
                }
            }
            saves.sort { ($0.save, -$0.i, -$0.j) > ($1.save, -$1.i, -$1.j) }
            var applied = false
            for s in saves.prefix(6) {
                guard let next = splice(cur, i: s.i, j: s.j, nodes: g.nodes, run: nil), let e = eval(next),
                      better(e, than: ev) else { continue }
                cur = next
                ev = e
                applied = true
                break
            }
            if !applied { break }
        }
        return cur
    }

    // MARK: - Detours

    private func bestDetour(_ steps: [RouteStep], _ ev: Eval) -> ([RouteStep], Eval)? {
        let g = geometry(steps)
        let n = steps.count
        let slack = limit - ev.seconds + 1e-6
        // Trees for every distinct node of the walk.
        var distinct = Set<Int>()
        var distinctList: [Int] = []
        for k in 0...n where distinct.insert(g.nodes[k]).inserted { distinctList.append(g.nodes[k]) }
        for d in distinctList { _ = fwd(d); _ = bwd(d) }
        let fwdAt: [ShortestPaths?] = g.nodes.map { fwdTrees[$0] }
        let bwdAt: [ShortestPaths?] = g.nodes.map { bwdTrees[$0] }
        // Candidate trails still open, and how many of their edges are left.
        var remaining = [Int](repeating: 0, count: candidateEdges.count)
        for (ci, edges) in candidateEdges.enumerated() {
            remaining[ci] = edges.reduce(0) { $0 + (g.walkedEdges.contains($1) ? 0 : 1) }
        }
        let mi = RouteScore.metersPerMile
        var moves: [Move] = []
        var startsSeen = Set<Int>()
        var startNodes: [Int] = []
        for t in fwdAt.compactMap({ $0 }) {
            for a in t.seconds.indices where t.seconds[a].isFinite && !newOut[a].isEmpty && startsSeen.insert(a).inserted {
                startNodes.append(a)
            }
        }
        startNodes.sort()
        let freeEnd = finishNode == nil
        var a2 = [Double](repeating: .infinity, count: n + 1)
        for a in startNodes {
            var any = false
            for i in 0...n {
                if let t = fwdAt[i], t.seconds[a].isFinite {
                    a2[i] = t.seconds[a] + g.prefix[i]
                    any = true
                } else {
                    a2[i] = .infinity
                }
            }
            if !any { continue }
            for run in runs(from: a) {
                var newGain = 0.0
                var tally: [Int: Int] = [:]
                for e in run.edgeIds where isNew[e] && !g.walkedEdges.contains(e) {
                    newGain += net.edges[e].lengthMeters
                    for c in edgeCandidates[e] ?? [] { tally[c, default: 0] += 1 }
                }
                if newGain <= 0 { continue }
                var finishGain = 0
                for (c, k) in tally where k == remaining[c] && k > 0 { finishGain += 1 }
                var curMin = Double.infinity
                var curArg = -1
                var bestExtra = Double.infinity
                var bestI = -1, bestJ = -1
                for j in 0...n {
                    if j > 0, g.protectedStep[j - 1] { curMin = .infinity; curArg = -1 }
                    if a2[j] < curMin { curMin = a2[j]; curArg = j }
                    guard curArg >= 0 else { continue }
                    let back: Double
                    if freeEnd, j == n {
                        back = 0
                    } else if let t = bwdAt[j], t.seconds[run.to].isFinite {
                        back = t.seconds[run.to]
                    } else {
                        continue
                    }
                    let extra = curMin + run.seconds + back - g.prefix[j]
                    if extra < bestExtra - 1e-9 { bestExtra = extra; bestI = curArg; bestJ = j }
                }
                guard bestI >= 0, bestExtra <= slack else { continue }
                let addedMeters = bestExtra * Self.rankSpeed
                let approx = newGain / mi + RouteScore.bonusPerFinishedTrailMiles * Double(finishGain)
                    - RouteScore.repeatPenalty * max(0, addedMeters - newGain) / mi
                if approx > 1e-9 { moves.append(Move(approx: approx, i: bestI, j: bestJ, run: run)) }
            }
        }
        moves.sort { a, b in
            if a.approx != b.approx { return a.approx > b.approx }
            if a.i != b.i { return a.i < b.i }
            if a.j != b.j { return a.j < b.j }
            if a.run.from != b.run.from { return a.run.from < b.run.from }
            return a.run.edgeIds.lexicographicallyPrecedes(b.run.edgeIds)
        }
        var best: ([RouteStep], Eval)?
        for m in moves.prefix(Self.exactPerPass) {
            guard let next = splice(steps, i: m.i, j: m.j, nodes: g.nodes, run: m.run), let e = eval(next),
                  better(e, than: best?.1 ?? ev) else { continue }
            best = (next, e)
        }
        return best
    }
}
