import Foundation

/// Turns the winning sequence into a `RoutePlan` ("Plan a Hike", Task 5).
extension RoutePlanEngine {
    func assemble(_ state: PlanState) -> RoutePlan? {
        var steps: [RouteStep] = []
        var goal: [TrailNetwork.TrailKey?] = []      // job key for cover steps, else nil
        func add(_ s: [RouteStep], _ key: TrailNetwork.TrailKey?) {
            steps.append(contentsOf: s)
            goal.append(contentsOf: [TrailNetwork.TrailKey?](repeating: key, count: s.count))
        }
        var prev = startK
        for p in state.picks {
            let job = jobs[p.job]
            add(leg(prev, job.keyIndex[p.s]), nil)
            if let cw = job.cover[p.s][p.t] {
                steps.append(contentsOf: cw.steps)
                goal.append(contentsOf: cw.steps.map { job.owner[$0.edgeId] ?? job.key })
            }
            prev = job.keyIndex[p.t]
        }
        if let f = finishK { add(leg(prev, f), nil) }

        guard let metrics = RouteMetrics(network: net, steps: steps, paceFactor: request.paceFactor,
                                         completed: completedKeys),
              metrics.isContinuous,
              metrics.seconds <= limit + 1e-3
        else { return nil }
        if let first = steps.first, startOf(first) != startNode { return nil }
        if let f = finishNode, let last = steps.last, endOf(last) != f { return nil }

        // Finished = every required edge is in the walk. Checked on the final
        // steps, independent of the search's bookkeeping.
        var firstWalk: [Int: Int] = [:]
        for (i, s) in steps.enumerated() where firstWalk[s.edgeId] == nil { firstWalk[s.edgeId] = i }
        var finished: [RoutePlan.FinishedTrail] = []
        for job in jobs[0..<singleCount] {
            var at = 0
            var all = true
            for e in job.edgeIds {
                guard let w = firstWalk[e] else { all = false; break }
                at = max(at, w)
            }
            if all {
                finished.append(.init(key: job.key, name: job.name,
                                      requiredMeters: job.requiredMeters, completedAtStep: at))
            }
        }
        finished.sort { ($0.completedAtStep, $0.key) < ($1.completedAtStep, $1.key) }
        if finished.isEmpty { return nil }
        let rank = Dictionary(uniqueKeysWithValues: finished.enumerated().map { ($1.key, $0) })

        var info: [RoutePlan.StepInfo] = []
        info.reserveCapacity(steps.count)
        for (i, s) in steps.enumerated() {
            let e = net.edges[s.edgeId]
            var key: TrailNetwork.TrailKey?
            if e.kind == .trail {
                if let g = goal[i], e.trailKeys.contains(g) {
                    key = g
                } else if let f = e.trailKeys.filter({ rank[$0] != nil }).min(by: { rank[$0]! < rank[$1]! }) {
                    key = f
                } else {
                    key = e.trailKeys.first { !completedKeys.contains($0) } ?? e.trailKeys.first
                }
            }
            info.append(.init(trailKey: key, kind: e.kind, isGoalCover: goal[i] != nil))
        }
        let endNode = steps.last.map { endOf($0) } ?? startNode
        return RoutePlan(steps: steps, stepInfo: info, finished: finished, metrics: metrics,
                         shape: request.shape, startNodeId: startNode, endNodeId: endNode,
                         evaluations: evaluations)
    }
}
