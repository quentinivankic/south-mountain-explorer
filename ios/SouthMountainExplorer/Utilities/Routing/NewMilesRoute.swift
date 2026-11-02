import Foundation

/// The most new ground inside the limits, partial trails allowed ("Plan a
/// Hike", Task 6). Used as the "nothing fits" fallback and as the "Most new
/// miles" alternative.
///
/// Method (heuristic, deterministic, no clock): a budgeted best-ratio walk.
/// From the current node, run a time-truncated Dijkstra (Task 3 costs). Every
/// unwalked edge with uncompleted ground is a target in either direction. A
/// target is allowed only if, after walking to it and along it, there is still
/// time (and climb) left to get back to the finish by the time-shortest path.
/// The allowed target with the most new metres per second of detour wins; ties
/// by edge id, forward first. Walk to it, repeat until nothing more fits, then
/// walk home. Edges already walked (including on the way) are never targets,
/// so ground counts once. Whole trails are not required; trails whose every
/// edge ends up walked are credited as finished anyway.
///
/// Not optimal (greedy), but it only ever extends outward while the way back is
/// paid for, so it always returns inside the limits. Edges are whole units: an
/// edge too long to walk out and back in the time left is never started.
enum NewMilesRoute {
    static func plan(
        view: TrailNetworkWithEndpoints,
        credit candidates: [TrailEligibility.Candidate],
        completion: TrailCompletionLookup,
        limitSeconds limit: Double,
        maxGainFt: Double?,
        paceFactor: Double,
        shape: RoutePlanRequest.Shape
    ) -> RoutePlan? {
        let net = view.network
        let start = view.start.nodeId
        guard limit > 0, limit.isFinite, start >= 0, start < net.nodes.count else { return nil }
        let finish: Int? = shape == .loop ? start : view.end?.nodeId
        let costs = RouteCosts(network: net, paceFactor: paceFactor)

        var done = Set<TrailNetwork.TrailKey>()
        for k in net.trailKeys where completion.isComplete(k) { done.insert(k) }
        let isNew = net.edges.map { $0.kind == .trail && $0.trailKeys.contains { !done.contains($0) } }

        // Way home: reversed network Dijkstra from the finish.
        var rev: TrailNetwork?
        var back: ShortestPaths?
        if let f = finish {
            let r = net.reversedForRouting()
            rev = r
            back = r.shortestPaths(from: f, costs: RouteCosts(network: r, paceFactor: paceFactor), maxSeconds: limit)
            guard let b = back, b.isReachable(start) else { return nil }
        }
        func homeSeconds(_ n: Int) -> Double { back.map { $0.seconds[n] } ?? 0 }
        func homeGain(_ n: Int) -> Double { back.map { $0.gainFt[n] } ?? 0 }

        var steps: [RouteStep] = []
        var walked = Set<Int>()
        var cur = start
        var t = 0.0, g = 0.0
        while true {
            guard let tree = net.shortestPaths(from: cur, costs: costs, maxSeconds: limit - t) else { break }
            var best: (score: Double, edge: Int, dir: RouteDirection, from: Int, to: Int)?
            for e in net.edges where isNew[e.id] && !walked.contains(e.id) {
                for dir in [RouteDirection.forward, .backward] {
                    let a = dir == .forward ? e.from : e.to
                    let b = dir == .forward ? e.to : e.from
                    guard tree.isReachable(a) else { continue }
                    let w = costs.walk(e.id, dir)
                    let cost = tree.seconds[a] + w.seconds
                    let ret = homeSeconds(b)
                    guard ret.isFinite, t + cost + ret <= limit - 1e-6, cost > 0 else { continue }
                    if let cap = maxGainFt, g + tree.gainFt[a] + w.gainFt + homeGain(b) > cap + 1e-9 { continue }
                    let score = w.meters / cost
                    if best == nil || score > best!.score + 1e-12 {
                        best = (score, e.id, dir, a, b)
                    }
                }
            }
            guard let pick = best, let path = tree.path(to: pick.from) else { break }
            let step = RouteStep(edgeId: pick.edge, direction: pick.dir)
            for s in path + [step] {
                let w = costs.walk(s.edgeId, s.direction)
                t += w.seconds
                g += w.gainFt
                walked.insert(s.edgeId)
                steps.append(s)
            }
            cur = pick.to
        }
        guard !steps.isEmpty else { return nil }
        if let f = finish, cur != f {
            guard let home = rev?.shortestPaths(from: f, costs: RouteCosts(network: rev!, paceFactor: paceFactor),
                                                maxSeconds: limit)?.path(to: cur) else { return nil }
            steps.append(contentsOf: home.reversed())
        }

        guard let metrics = RouteMetrics(network: net, steps: steps, paceFactor: paceFactor, completed: done),
              metrics.isContinuous, metrics.seconds <= limit + 1e-3, metrics.newMeters > 0
        else { return nil }
        if let cap = maxGainFt, metrics.gainFt > cap + 1e-6 { return nil }

        var firstWalk: [Int: Int] = [:]
        for (i, s) in steps.enumerated() where firstWalk[s.edgeId] == nil { firstWalk[s.edgeId] = i }
        var finished: [RoutePlan.FinishedTrail] = []
        for c in candidates {
            let at = c.edgeIds.compactMap { firstWalk[$0] }
            if at.count == c.edgeIds.count, let last = at.max() {
                finished.append(.init(key: c.key, name: c.name, requiredMeters: c.requiredMeters, completedAtStep: last))
            }
        }
        finished.sort { ($0.completedAtStep, $0.key) < ($1.completedAtStep, $1.key) }

        let info: [RoutePlan.StepInfo] = steps.map { s in
            let e = net.edges[s.edgeId]
            var key: TrailNetwork.TrailKey?
            if e.kind == .trail { key = e.trailKeys.first { !done.contains($0) } ?? e.trailKeys.first }
            return .init(trailKey: key, kind: e.kind, isGoalCover: isNew[s.edgeId])
        }
        let last = steps.last!
        let e = net.edges[last.edgeId]
        let end = last.direction == .forward ? e.to : e.from
        return RoutePlan(steps: steps, stepInfo: info, finished: finished, metrics: metrics, shape: shape,
                         startNodeId: start, endNodeId: end, evaluations: 0)
    }
}
