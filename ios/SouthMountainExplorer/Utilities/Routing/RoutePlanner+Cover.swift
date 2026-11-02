import Foundation

/// Covering walks for one trail ("Plan a Hike", Task 5): given the trail's
/// required edges, the cheapest known walk that traverses all of them, for
/// each (entry node, exit node) pair of boundary nodes.
extension RoutePlanEngine {
    /// The sub-graph made of one trail's required edges.
    struct TrailGraph {
        let edges: [Int]
        let nodes: [Int]
        /// Steps leaving each node (a self-loop appears once per direction).
        let adj: [Int: [RouteStep]]
        func degree(_ n: Int) -> Int { adj[n]?.count ?? 0 }
    }

    /// Dijkstra inside a trail graph (tiny).
    struct LocalTree {
        let dist: [Int: Double]
        let arrival: [Int: RouteStep]
    }

    enum TrailShape {
        /// Nodes along a simple path, end to end.
        case path([Int])
        /// Connected but branched or looped: Chinese-postman cover.
        case connected
        /// In several pieces joined only through other trails: greedy.
        case pieces
    }

    /// Most boundary nodes kept per trail. Each costs one Dijkstra.
    static let maxBoundaryNodes = 7

    // MARK: - Graph

    func makeGraph(_ edgeIds: [Int]) -> TrailGraph {
        var adj: [Int: [RouteStep]] = [:]
        var nodes = Set<Int>()
        for id in edgeIds {
            let e = net.edges[id]
            nodes.insert(e.from); nodes.insert(e.to)
            adj[e.from, default: []].append(RouteStep(edgeId: id, direction: .forward))
            adj[e.to, default: []].append(RouteStep(edgeId: id, direction: .backward))
        }
        for n in adj.keys {
            adj[n]!.sort { ($0.edgeId, $0.direction == .forward ? 0 : 1) < ($1.edgeId, $1.direction == .forward ? 0 : 1) }
        }
        return TrailGraph(edges: edgeIds, nodes: nodes.sorted(), adj: adj)
    }

    private func isConnected(_ g: TrailGraph) -> Bool {
        guard let first = g.nodes.first else { return true }
        var seen: Set<Int> = [first]
        var stack = [first]
        while let n = stack.popLast() {
            for s in g.adj[n] ?? [] {
                let v = endOf(s)
                if seen.insert(v).inserted { stack.append(v) }
            }
        }
        return seen.count == g.nodes.count
    }

    func classify(_ g: TrailGraph) -> TrailShape {
        guard isConnected(g) else { return .pieces }
        let hasLoop = g.edges.contains { net.edges[$0].from == net.edges[$0].to }
        let maxDeg = g.nodes.map { g.degree($0) }.max() ?? 0
        if !hasLoop, g.edges.count == g.nodes.count - 1, maxDeg <= 2 {
            guard let end = g.nodes.first(where: { g.degree($0) == 1 }) else { return .connected }
            var chain = [end]
            var prev = -1
            var cur = end
            while let s = g.adj[cur]?.first(where: { $0.edgeId != prev }) {
                chain.append(endOf(s))
                prev = s.edgeId
                cur = endOf(s)
            }
            return chain.count == g.nodes.count ? .path(chain) : .connected
        }
        return .connected
    }

    // MARK: - Local shortest paths

    func localTree(_ g: TrailGraph, from s: Int) -> LocalTree {
        var dist: [Int: Double] = [s: 0]
        var arrival: [Int: RouteStep] = [:]
        var done = Set<Int>()
        while true {
            var best: (Double, Int)?
            for n in g.nodes where !done.contains(n) {
                if let d = dist[n], best == nil || (d, n) < best! { best = (d, n) }
            }
            guard let (d, u) = best else { break }
            done.insert(u)
            for step in g.adj[u] ?? [] {
                let v = endOf(step)
                if done.contains(v) { continue }
                let c = d + costs.walk(step.edgeId, step.direction).seconds
                if dist[v] == nil || c < dist[v]! { dist[v] = c; arrival[v] = step }
            }
        }
        return LocalTree(dist: dist, arrival: arrival)
    }

    private func localPath(_ lt: LocalTree, from s: Int, to t: Int) -> [RouteStep]? {
        guard lt.dist[t] != nil else { return nil }
        var steps: [RouteStep] = []
        var cur = t
        while cur != s {
            guard let step = lt.arrival[cur] else { return nil }
            steps.append(step)
            cur = startOf(step)
        }
        return steps.reversed()
    }

    // MARK: - Cover walks

    /// Path trail: the exact optimum, in closed form. The walk must reach both
    /// ends; from entry `i` to exit `j` it visits the left end then the right
    /// end, or the right end then the left, and the cheaper wins.
    private func pathCover(right: [RouteStep], left: [RouteStep], from i: Int, to j: Int) -> [RouteStep] {
        let m = right.count
        func goLeft(_ from: Int, _ to: Int) -> [RouteStep] {
            from > to ? stride(from: from - 1, through: to, by: -1).map { left[$0] } : []
        }
        func goRight(_ from: Int, _ to: Int) -> [RouteStep] {
            from < to ? (from..<to).map { right[$0] } : []
        }
        let a = goLeft(i, 0) + goRight(0, m) + goLeft(m, j)
        let b = goRight(i, m) + goLeft(m, 0) + goRight(0, j)
        return makeCover(a).seconds <= makeCover(b).seconds ? a : b
    }

    /// Most odd-degree nodes the postman matching handles (2^n subsets).
    private static let maxOddNodes = 12

    /// Chinese-postman cover of a connected trail graph from `s` to `t`:
    /// duplicate the cheapest set of paths so that only `s` and `t` have odd
    /// degree (none when `s == t`), then walk an Euler trail. Exact for trees
    /// and loops; for mixed graphs it is the classic undirected optimum, then
    /// costed with the real, direction-dependent times. nil when there are
    /// too many odd nodes to match.
    private func postmanCover(_ g: TrailGraph, from s: Int, to t: Int,
                              trees: inout [Int: LocalTree]) -> [RouteStep]? {
        var odd = Set(g.nodes.filter { g.degree($0) % 2 == 1 })
        if s != t { odd.formSymmetricDifference([s, t]) }
        let o = odd.sorted()
        guard o.count <= Self.maxOddNodes else { return nil }
        for n in o where trees[n] == nil { trees[n] = localTree(g, from: n) }

        var instances = g.edges                       // edge id per instance
        if !o.isEmpty {
            func d(_ a: Int, _ b: Int) -> Double {
                let ab = trees[a]!.dist[b] ?? .infinity
                let ba = trees[b]!.dist[a] ?? .infinity
                return (ab + ba) / 2
            }
            let m = o.count
            var dp = [Double](repeating: .infinity, count: 1 << m)
            var pairI = [Int](repeating: -1, count: 1 << m)
            var pairJ = [Int](repeating: -1, count: 1 << m)
            dp[0] = 0
            for mask in 0..<(1 << m) where dp[mask].isFinite {
                guard let i = (0..<m).first(where: { mask & (1 << $0) == 0 }) else { continue }
                for j in (i + 1)..<max(m, i + 1) where mask & (1 << j) == 0 {
                    let next = mask | (1 << i) | (1 << j)
                    let c = dp[mask] + d(o[i], o[j])
                    if c < dp[next] { dp[next] = c; pairI[next] = i; pairJ[next] = j }
                }
            }
            var mask = (1 << m) - 1
            guard dp[mask].isFinite else { return nil }
            while mask != 0 {
                let i = pairI[mask], j = pairJ[mask]
                guard i >= 0, j >= 0 else { return nil }
                let ab = trees[o[i]]!.dist[o[j]] ?? .infinity
                let ba = trees[o[j]]!.dist[o[i]] ?? .infinity
                let path = ab <= ba ? localPath(trees[o[i]]!, from: o[i], to: o[j])
                                    : localPath(trees[o[j]]!, from: o[j], to: o[i])
                guard let p = path else { return nil }
                instances.append(contentsOf: p.map(\.edgeId))
                mask &= ~((1 << i) | (1 << j))
            }
        }

        // Euler trail (Hierholzer), deterministic by instance index.
        var adj: [Int: [Int]] = [:]
        for (k, eid) in instances.enumerated() {
            let e = net.edges[eid]
            adj[e.from, default: []].append(k)
            if e.to != e.from { adj[e.to, default: []].append(k) }
        }
        var used = [Bool](repeating: false, count: instances.count)
        var pos: [Int: Int] = [:]
        var stack: [(node: Int, via: RouteStep?)] = [(s, nil)]
        var out: [RouteStep] = []
        while let top = stack.last {
            let u = top.node
            var i = pos[u] ?? 0
            let list = adj[u] ?? []
            while i < list.count, used[list[i]] { i += 1 }
            pos[u] = i
            if i < list.count {
                let k = list[i]
                used[k] = true
                let e = net.edges[instances[k]]
                let dir: RouteDirection = e.from == u ? .forward : .backward
                let step = RouteStep(edgeId: e.id, direction: dir)
                stack.append((endOf(step), step))
            } else {
                stack.removeLast()
                if let via = top.via { out.append(via) }
            }
        }
        var steps = Array(out.reversed())
        guard used.allSatisfy({ $0 }), steps.count == instances.count else { return nil }
        if s == t {
            // A closed walk can run either way round.
            let back = steps.reversed().map { $0.reversedStep }
            if makeCover(back).seconds < makeCover(steps).seconds - 1e-9 { steps = back }
        }
        return steps
    }

    /// Nearest-required-edge-first walk from `s`. Repositions through the
    /// trail's own edges, falling back to the whole network when a remaining
    /// edge is in a separate piece. Returns the steps (ending wherever the
    /// last required edge ends) or nil when some edge is unreachable.
    private func greedyCover(_ g: TrailGraph, from s: Int, cache: inout [Int: LocalTree]) -> [RouteStep]? {
        var remaining = Set(g.edges)
        var cur = s
        var steps: [RouteStep] = []
        while !remaining.isEmpty {
            if cache[cur] == nil { cache[cur] = localTree(g, from: cur) }
            let lt = cache[cur]!
            var best: (cost: Double, edge: Int, dirRank: Int, step: RouteStep)?
            for eid in remaining.sorted() {
                for (rank, d) in [RouteDirection.forward, .backward].enumerated() {
                    let st = RouteStep(edgeId: eid, direction: d)
                    guard let dist = lt.dist[startOf(st)] else { continue }
                    let c = dist + costs.walk(eid, d).seconds
                    if best == nil || (c, eid, rank) < (best!.cost, best!.edge, best!.dirRank) {
                        best = (c, eid, rank, st)
                    }
                }
            }
            var approach: [RouteStep]
            var chosen: RouteStep
            if let b = best {
                chosen = b.step
                guard let p = localPath(lt, from: cur, to: startOf(b.step)) else { return nil }
                approach = p
            } else {
                // Remaining edges are in another piece: go through the network.
                guard let ft = tree(cur) else { return nil }
                var fb: (cost: Double, edge: Int, dirRank: Int, step: RouteStep)?
                for eid in remaining.sorted() {
                    for (rank, d) in [RouteDirection.forward, .backward].enumerated() {
                        let st = RouteStep(edgeId: eid, direction: d)
                        guard ft.isReachable(startOf(st)) else { continue }
                        let c = ft.seconds[startOf(st)] + costs.walk(eid, d).seconds
                        if fb == nil || (c, eid, rank) < (fb!.cost, fb!.edge, fb!.dirRank) {
                            fb = (c, eid, rank, st)
                        }
                    }
                }
                guard let f = fb, let p = ft.path(to: startOf(f.step)) else { return nil }
                chosen = f.step
                approach = p
            }
            for st in approach { remaining.remove(st.edgeId) }
            steps.append(contentsOf: approach)
            if remaining.contains(chosen.edgeId) {
                steps.append(chosen)
                remaining.remove(chosen.edgeId)
                cur = endOf(chosen)
            } else {
                cur = approach.last.map { endOf($0) } ?? cur
            }
        }
        return steps
    }

    /// Walk from `end` to `t` for the tail of a variant.
    private func tail(_ g: TrailGraph, lt: LocalTree, from end: Int, to t: Int) -> [RouteStep]? {
        if end == t { return [] }
        if let p = localPath(lt, from: end, to: t) { return p }
        return tree(end)?.path(to: t)
    }

    // MARK: - Job

    /// Builds one job. `preferred` boundary nodes (clusters pass their
    /// members' nodes) come first; `allowed`, when set, restricts the boundary
    /// so a cluster adds no new distance runs. A cluster whose union is just a
    /// path is skipped: ordering its two members already expresses it.
    func buildJob(
        index: Int, key: TrailNetwork.TrailKey, name: String, edgeIds rawEdges: [Int],
        entryNode: Int, exitNode: Int?, members: [Int], owner: [Int: TrailNetwork.TrailKey],
        preferred: [Int], allowed: Set<Int>?, startDist: [Double]
    ) -> PlanJob? {
        let edgeIds = Array(Set(rawEdges)).sorted()
        guard !edgeIds.isEmpty, edgeIds.allSatisfy({ $0 >= 0 && $0 < net.edges.count }) else { return nil }
        let g = makeGraph(edgeIds)
        let shape = classify(g)
        if members.count > 1, case .path = shape { return nil }
        let inGraph = Set(g.nodes)

        // Boundary nodes in priority order.
        var priority: [Int] = []
        if case .path(let chain) = shape { priority += [chain[0], chain[chain.count - 1]] }
        priority += preferred
        priority.append(entryNode)
        if let x = exitNode { priority.append(x) }
        priority.append(startNode)
        if let f = finishNode { priority.append(f) }
        func byStart(_ a: Int, _ b: Int) -> Bool { (startDist[a], a) < (startDist[b], b) }
        if case .path = shape {} else {
            priority += g.nodes.filter { g.degree($0) != 2 }.sorted(by: byStart)
        }
        priority += g.nodes.filter { net.nodes[$0].edgeIds.count > g.degree($0) }.sorted(by: byStart)
        priority += g.nodes.sorted(by: byStart)   // last resort, e.g. a pure cycle

        var boundary: [Int] = []
        for n in priority where inGraph.contains(n) && startDist[n].isFinite && !boundary.contains(n) {
            if let allowed, !allowed.contains(n) { continue }
            boundary.append(n)
            if boundary.count == Self.maxBoundaryNodes { break }
        }
        guard !boundary.isEmpty else { return nil }

        let m = boundary.count
        var cover = [[PlanCoverWalk?]](repeating: [PlanCoverWalk?](repeating: nil, count: m), count: m)
        func offer(_ i: Int, _ j: Int, _ steps: [RouteStep]) {
            let w = makeCover(steps)
            if let old = cover[i][j], old.seconds <= w.seconds { return }
            cover[i][j] = w
        }
        func fillGreedy() {
            var cache: [Int: LocalTree] = [:]
            for i in 0..<m {
                let s = boundary[i]
                guard let walk = greedyCover(g, from: s, cache: &cache) else { continue }
                let end = walk.last.map { endOf($0) } ?? s
                if cache[end] == nil { cache[end] = localTree(g, from: end) }
                for j in 0..<m {
                    guard let ext = tail(g, lt: cache[end]!, from: end, to: boundary[j]) else { continue }
                    offer(i, j, walk + ext)
                }
            }
        }

        switch shape {
        case .path(let chain):
            let pos = Dictionary(uniqueKeysWithValues: chain.enumerated().map { ($1, $0) })
            var right: [RouteStep] = []
            for k in 0..<(chain.count - 1) {
                right.append(g.adj[chain[k]]!.first { endOf($0) == chain[k + 1] }!)
            }
            let left = right.map { $0.reversedStep }
            for i in 0..<m { for j in 0..<m {
                offer(i, j, pathCover(right: right, left: left, from: pos[boundary[i]]!, to: pos[boundary[j]]!))
            } }
        case .connected:
            var cache: [Int: LocalTree] = [:]
            for i in 0..<m { for j in 0..<m {
                if let w = postmanCover(g, from: boundary[i], to: boundary[j], trees: &cache) { offer(i, j, w) }
            } }
            // Too many odd nodes for the matching: fall back to greedy.
            if cover.allSatisfy({ $0.allSatisfy { $0 == nil } }) { fillGreedy() }
        case .pieces:
            fillGreedy()
        }

        var newMeters = 0.0
        for id in edgeIds where isNewEdge[id] { newMeters += net.edges[id].lengthMeters }
        let requiredMeters = edgeIds.reduce(0.0) { $0 + net.edges[$1].lengthMeters }
        return PlanJob(index: index, key: key, name: name, members: members, owner: owner, edgeIds: edgeIds,
                       requiredMeters: requiredMeters, nodes: boundary, keyIndex: [], cover: cover,
                       bonus: min(0.25, 0.02 * newMeters / 1609.344))
    }

    /// Builds all jobs (sorted by key, so input order never matters), then the
    /// clusters, then the distance tables.
    func prepare() {
        guard let st = tree(startNode) else { return }
        var seen = Set<TrailNetwork.TrailKey>()
        for c in request.candidates.sorted(by: { $0.key < $1.key }) {
            guard seen.insert(c.key).inserted, !completedKeys.contains(c.key) else { continue }
            let owner = Dictionary(uniqueKeysWithValues: Set(c.edgeIds).map { ($0, c.key) })
            if let job = buildJob(
                index: jobs.count, key: c.key, name: c.name, edgeIds: c.edgeIds, entryNode: c.entryNodeId,
                exitNode: c.exitNodeId, members: [jobs.count], owner: owner, preferred: [], allowed: nil,
                startDist: st.seconds) {
                jobs.append(job)
            }
        }
        singleCount = jobs.count
        buildClusters(startDist: st.seconds)
        buildTables()
    }

    /// Pairs of candidate trails that touch at a node become extra jobs.
    private func buildClusters(startDist: [Double]) {
        let singles = Array(jobs)
        let nodeSets = singles.map { j in Set(j.edgeIds.flatMap { [net.edges[$0].from, net.edges[$0].to] }) }
        for a in 0..<singles.count {
            for b in (a + 1)..<max(singles.count, a + 1) {
                let shared = nodeSets[a].intersection(nodeSets[b])
                guard !shared.isEmpty else { continue }
                let ja = singles[a], jb = singles[b]
                var owner = ja.owner
                for (e, k) in jb.owner where owner[e] == nil { owner[e] = k }
                var preferred = shared.sorted()
                for i in 0..<max(ja.nodes.count, jb.nodes.count) {
                    if i < ja.nodes.count { preferred.append(ja.nodes[i]) }
                    if i < jb.nodes.count { preferred.append(jb.nodes[i]) }
                }
                let allowed = Set(ja.nodes).union(jb.nodes).union(shared)
                if let job = buildJob(
                    index: jobs.count, key: ja.key, name: "\(ja.name) + \(jb.name)",
                    edgeIds: ja.edgeIds + jb.edgeIds, entryNode: ja.nodes[0], exitNode: jb.nodes[0],
                    members: [a, b], owner: owner, preferred: preferred, allowed: allowed,
                    startDist: startDist) {
                    jobs.append(job)
                }
            }
        }
    }
}

extension RouteStep {
    /// Same edge, opposite direction.
    var reversedStep: RouteStep { RouteStep(edgeId: edgeId, direction: direction.reversed) }
}
