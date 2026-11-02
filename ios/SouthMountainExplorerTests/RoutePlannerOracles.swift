import Foundation
import Testing
@testable import SouthMountainExplorer

/// Deterministic generator for the random scenes (no system randomness).
struct PlannerLCG {
    var state: UInt64
    init(seed: UInt64) { state = seed &* 6364136223846793005 &+ 1442695040888963407 }
    mutating func next() -> UInt64 {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        return state >> 33
    }
    mutating func int(_ n: Int) -> Int { Int(next() % UInt64(n)) }
    mutating func chance(_ p: Double) -> Bool { Double(next() % 10_000) / 10_000 < p }
}

/// Exhaustive solvers the planner is checked against ("Plan a Hike", Task 5).
/// They are written for these tests only and share nothing with the planner
/// except the Task 3 edge costs and shortest paths.
enum PlannerOracles {
    struct Objective: Equatable {
        var count: Int
        var newMeters: Double
        var seconds: Double

        /// Same order as the planner: trails, then new metres, then time.
        func isBetter(than o: Objective) -> Bool {
            if count != o.count { return count > o.count }
            if abs(newMeters - o.newMeters) > 0.5 { return newMeters > o.newMeters }
            return seconds < o.seconds - 0.5
        }

        func isSame(as o: Objective) -> Bool { !isBetter(than: o) && !o.isBetter(than: self) }
    }

    private static func bit(_ e: Int) -> UInt64 { 1 << UInt64(e) }

    /// ORACLE 2: every subset of candidate trails, in every order, each walked
    /// in each direction, joined by time-shortest paths. Only valid when each
    /// candidate is ONE edge (so a walk is just a direction). Honors the time
    /// and gain limits and loop / one-way. Returns the best objective, or nil
    /// when no trail can be finished.
    static func subsetsAndOrders(_ o: PlannerKit.Outcome) -> Objective? {
        let net = o.net
        let req = o.request
        let costs = RouteCosts(network: net, paceFactor: req.paceFactor)
        let n = net.nodes.count
        let startNode = o.view.start.nodeId
        let finish: Int? = req.shape == .loop ? startNode : o.view.end?.nodeId
        let completed = Set(net.trailKeys.filter { req.completion.isComplete($0) })

        // All-pairs time-shortest paths: seconds, gain, edge mask.
        var secs = [[Double]](repeating: [Double](repeating: .infinity, count: n), count: n)
        var gain = secs
        var mask = [[UInt64]](repeating: [UInt64](repeating: 0, count: n), count: n)
        for a in 0..<n {
            guard let t = net.shortestPaths(from: a, costs: costs) else { continue }
            for b in 0..<n where t.isReachable(b) {
                secs[a][b] = t.seconds[b]
                gain[a][b] = t.gainFt[b]
                var m: UInt64 = 0
                for s in t.path(to: b) ?? [] { m |= bit(s.edgeId) }
                mask[a][b] = m
            }
        }
        let isNew = net.edges.map { e in e.kind == .trail && e.trailKeys.contains { !completed.contains($0) } }
        func newMeters(_ m: UInt64) -> Double {
            net.edges.indices.reduce(0.0) { m & bit($1) != 0 && isNew[$1] ? $0 + net.edges[$1].lengthMeters : $0 }
        }
        let cands = o.eligibility.candidates
        let required: [UInt64] = cands.map { $0.edgeIds.reduce(0) { $0 | bit($1) } }
        precondition(cands.allSatisfy { $0.edgeIds.count == 1 }, "oracle 2 needs single-edge trails")
        precondition(net.edges.count < 62)

        var best: Objective?
        func close(at node: Int, _ t: Double, _ g: Double, _ walked: UInt64) {
            var total = t
            var gg = g
            var m = walked
            if let f = finish {
                guard secs[node][f].isFinite else { return }
                total += secs[node][f]; gg += gain[node][f]; m |= mask[node][f]
            }
            guard total <= req.timeLimitSeconds + 1e-6 else { return }
            if let mg = req.maxGainFt, gg > mg + 1e-6 { return }
            let count = required.filter { $0 & m == $0 }.count
            guard count > 0 else { return }
            let obj = Objective(count: count, newMeters: newMeters(m), seconds: total)
            if best == nil || obj.isBetter(than: best!) { best = obj }
        }
        func dfs(_ node: Int, _ t: Double, _ g: Double, _ walked: UInt64, _ used: Int) {
            close(at: node, t, g, walked)
            for (i, c) in cands.enumerated() where used & (1 << i) == 0 {
                let e = net.edges[c.edgeIds[0]]
                for dir in [RouteDirection.forward, .backward] {
                    let a = dir == .forward ? e.from : e.to
                    let b = dir == .forward ? e.to : e.from
                    guard secs[node][a].isFinite else { continue }
                    let w = costs.walk(e.id, dir)
                    let t2 = t + secs[node][a] + w.seconds
                    // Adding a trail never shortens the closed walk.
                    if t2 + (finish.map { secs[b][$0] } ?? 0) > req.timeLimitSeconds + 1e-6 { continue }
                    dfs(b, t2, g + gain[node][a] + w.gainFt, walked | mask[node][a] | bit(e.id), used | (1 << i))
                }
            }
        }
        dfs(startNode, 0, 0, 0, 0)
        return best
    }

    /// ORACLE 1: the true optimum number of finished trails over EVERY walk,
    /// not only trail-by-trail ones. Dijkstra over (node, set of edges walked).
    /// Time only (no gain limit). Returns the best count and the least time
    /// that reaches it. Needs fewer than ~16 edges.
    static func bestWalk(_ o: PlannerKit.Outcome) -> (count: Int, seconds: Double)? {
        let net = o.net
        let req = o.request
        let costs = RouteCosts(network: net, paceFactor: req.paceFactor)
        let n = net.nodes.count
        let e = net.edges.count
        precondition(e <= 16, "oracle 1 is exhaustive; keep the graph small")
        let startNode = o.view.start.nodeId
        let finish: Int? = req.shape == .loop ? startNode : o.view.end?.nodeId
        let required: [Int] = o.eligibility.candidates.map { $0.edgeIds.reduce(0) { $0 | (1 << $1) } }
        let states = n << e
        var dist = [Double](repeating: .infinity, count: states)
        func id(_ node: Int, _ m: Int) -> Int { m * n + node }
        var heap = MinHeap()
        dist[id(startNode, 0)] = 0
        heap.push(cost: 0, node: id(startNode, 0))
        var best: (count: Int, seconds: Double)?
        while let (cost, sid) = heap.pop() {
            if cost > dist[sid] || cost > req.timeLimitSeconds + 1e-6 { continue }
            let node = sid % n, m = sid / n
            if finish == nil || finish == node {
                let count = required.filter { $0 & m == $0 }.count
                if count > 0, best == nil || count > best!.count || (count == best!.count && cost < best!.seconds) {
                    best = (count, cost)
                }
            }
            for eid in net.nodes[node].edgeIds {
                let edge = net.edges[eid]
                let dirs: [RouteDirection] = edge.from == edge.to ? [.forward, .backward]
                    : (edge.from == node ? [.forward] : [.backward])
                for dir in dirs {
                    let to = dir == .forward ? edge.to : edge.from
                    let c = cost + costs.walk(eid, dir).seconds
                    let nid = id(to, m | (1 << eid))
                    if c < dist[nid] - 1e-9 { dist[nid] = c; heap.push(cost: c, node: nid) }
                }
            }
        }
        return best
    }
}
