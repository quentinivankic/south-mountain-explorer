import Foundation
import Testing
@testable import SouthMountainExplorer

/// Shared helpers for the Plan a Hike planner tests (Task 5). Synthetic scenes
/// are laid out in local metres (x east, y north); flat ground walks at about
/// 5 km/h, so 1 km is about 720 s.
enum PlannerKit {
    typealias Key = TrailNetwork.TrailKey

    static let lat0 = 33.34
    static let lon0 = -112.0

    static func ll(_ x: Double, _ y: Double) -> [Double] {
        let mpd = TurnCueProjector.metersPerDegree
        return [lat0 + y / mpd, lon0 + x / (cos(lat0 * .pi / 180) * mpd)]
    }

    static func line(_ pts: [(Double, Double)], step: Double = 20) -> [[Double]] {
        var out: [[Double]] = []
        for (a, b) in zip(pts, pts.dropFirst()) {
            let n = max(1, Int((hypot(b.0 - a.0, b.1 - a.1) / step).rounded()))
            for i in 0..<n {
                let t = Double(i) / Double(n)
                out.append(ll(a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t))
            }
        }
        out.append(ll(pts.last!.0, pts.last!.1))
        return out
    }

    /// One trail made of one or more polylines (several pieces that meet make
    /// a branched trail).
    static func trail(_ id: String, _ pieces: [[(Double, Double)]], profile: [Int]? = nil) -> Trail {
        Trail(id: id, name: "Trail \(id)", distanceMi: 0, difficulty: .easy,
              segments: pieces.map { line($0) }, gainFt: nil, profileFt: profile)
    }

    static func trail(_ id: String, _ pts: [(Double, Double)], profile: [Int]? = nil) -> Trail {
        trail(id, [pts], profile: profile)
    }

    static func key(_ id: String) -> Key { Key(areaId: "a", trailId: id) }
    static func inputs(_ trails: [Trail]) -> [TrailNetwork.Input] { trails.map { .init(areaId: "a", trail: $0) } }

    static func pin(_ x: Double, _ y: Double) -> StartPoint {
        let c = ll(x, y)
        return .pin(lat: c[0], lon: c[1])
    }

    static func ramp(_ top: Int, samples: Int = 21) -> [Int] { (0..<samples).map { $0 * top / (samples - 1) } }
    static func flat(samples: Int = 21) -> [Int] { [Int](repeating: 0, count: samples) }

    /// Seconds to walk `meters` of flat trail at pace 1.0.
    static func flatSeconds(_ meters: Double, pace: Double = 1.0) -> Double {
        PaceModel.seconds(length: meters, grade: 0, factor: pace)
    }

    struct Outcome {
        let view: TrailNetworkWithEndpoints
        let eligibility: TrailEligibility
        let request: RoutePlanRequest
        let plan: RoutePlan?
        let seconds: Double
        var net: TrailNetwork { view.network }
    }

    static func view(_ trails: [Trail], start: (Double, Double) = (0, 0), end: (Double, Double)? = nil) throws
        -> TrailNetworkWithEndpoints {
        let net = TrailNetwork(trails: inputs(trails))
        return try TrailNetworkSnapper(network: net).insert(start: pin(start.0, start.1), end: end.map { pin($0.0, $0.1) })
    }

    /// Eligibility, then the planner.
    static func run(
        _ trails: [Trail], completed: Set<String> = [], start: (Double, Double) = (0, 0),
        end: (Double, Double)? = nil, limit: Double, maxGain: Double? = nil, pace: Double = 1.0,
        shape: RoutePlanRequest.Shape = .loop, reverseCandidates: Bool = false
    ) throws -> Outcome {
        let v = try view(trails, start: start, end: end)
        let done = TrailCompletionLookup(completed: Set(completed.map(key)))
        let elig = TrailEligibility.evaluate(
            network: v, trails: inputs(trails), completion: done,
            options: .init(budgetSeconds: limit, paceFactor: pace, trip: shape == .loop ? .loop : .oneWay))
        let cands = reverseCandidates ? elig.candidates.reversed() : elig.candidates
        let req = RoutePlanRequest(network: v, candidates: Array(cands), completion: done, timeLimitSeconds: limit,
                                   maxGainFt: maxGain, paceFactor: pace, shape: shape)
        let t0 = Date()
        let plan = RoutePlanner.plan(req)
        return Outcome(view: v, eligibility: elig, request: req, plan: plan, seconds: Date().timeIntervalSince(t0))
    }

    /// Independent checks on a plan. Returns a list of problems (empty = valid).
    static func validate(_ o: Outcome) -> [String] {
        guard let plan = o.plan else { return ["no plan"] }
        var problems: [String] = []
        let net = o.net
        let req = o.request
        let completed = Set(net.trailKeys.filter { req.completion.isComplete($0) })

        guard let m = RouteMetrics(network: net, steps: plan.steps, paceFactor: req.paceFactor, completed: completed)
        else { return ["unknown edge in steps"] }
        if !m.isContinuous { problems.append("walk is not continuous") }
        if abs(m.seconds - plan.metrics.seconds) > 1e-6 { problems.append("metrics seconds differ") }
        if abs(m.newMeters - plan.metrics.newMeters) > 1e-6 { problems.append("metrics new metres differ") }
        if m.seconds > req.timeLimitSeconds + 1e-3 { problems.append("over the time limit") }
        if let g = req.maxGainFt, m.gainFt > g + 1e-6 { problems.append("over the gain limit") }
        if plan.stepInfo.count != plan.steps.count { problems.append("stepInfo count") }

        func from(_ s: RouteStep) -> Int { s.direction == .forward ? net.edges[s.edgeId].from : net.edges[s.edgeId].to }
        func to(_ s: RouteStep) -> Int { s.direction == .forward ? net.edges[s.edgeId].to : net.edges[s.edgeId].from }
        if let f = plan.steps.first, from(f) != o.view.start.nodeId { problems.append("does not start at the start") }
        if let l = plan.steps.last {
            if to(l) != plan.endNodeId { problems.append("endNodeId is not where the walk ends") }
            switch req.shape {
            case .loop: if to(l) != o.view.start.nodeId { problems.append("loop does not return to the start") }
            case .oneWay: if let e = o.view.end, to(l) != e.nodeId { problems.append("one-way does not end at the end") }
            }
        }

        let walked = Set(plan.steps.map(\.edgeId))
        let finished = Set(plan.finishedTrailKeys)
        for c in o.eligibility.candidates {
            let all = net.edgeIds(of: c.key).allSatisfy { walked.contains($0) }
            if all != finished.contains(c.key) { problems.append("finished flag wrong for \(c.key)") }
            if c.edgeIds != net.edgeIds(of: c.key) { problems.append("candidate edges differ for \(c.key)") }
        }
        for k in finished where completed.contains(k) { problems.append("completed trail credited: \(k)") }
        for k in finished where !o.eligibility.candidates.contains(where: { $0.key == k }) {
            problems.append("finished a non-candidate: \(k)")
        }
        if plan.finished.map(\.completedAtStep) != plan.finished.map(\.completedAtStep).sorted() {
            problems.append("finished not in walk order")
        }
        for (s, info) in zip(plan.steps, plan.stepInfo) where info.kind != net.edges[s.edgeId].kind {
            problems.append("stepInfo kind wrong")
        }
        return problems
    }

    static func ids(_ plan: RoutePlan?) -> [String] { (plan?.finishedTrailKeys ?? []).map(\.trailId) }
}
