import Foundation

/// Input to `RoutePlanningService.planOutcome` ("Plan a Hike", Task 6).
struct PlanOutcomeRequest: Sendable {
    /// Network with the start (and optional end) spliced in.
    let network: TrailNetworkWithEndpoints
    /// Every trail (display names; eligibility looks at the whole network).
    let trails: [TrailNetwork.Input]
    let completion: TrailCompletionLookup
    let timeLimitSeconds: Double
    /// Optional cap on climbing in the walking direction, feet.
    let maxGainFt: Double?
    let paceFactor: Double
    let shape: RoutePlanRequest.Shape

    init(
        network: TrailNetworkWithEndpoints,
        trails: [TrailNetwork.Input],
        completion: TrailCompletionLookup = .none,
        timeLimitSeconds: Double,
        maxGainFt: Double? = nil,
        paceFactor: Double = 1.0,
        shape: RoutePlanRequest.Shape = .loop
    ) {
        self.network = network
        self.trails = trails
        self.completion = completion
        self.timeLimitSeconds = timeLimitSeconds
        self.maxGainFt = maxGainFt
        self.paceFactor = paceFactor
        self.shape = shape
    }
}

/// What "Plan a Hike" shows after Plan is tapped.
enum PlanOutcome: Sendable {
    /// At least one whole trail fits. `alternatives` has up to 3 genuinely
    /// different routes, each inside the same limits.
    case planned(best: RoutePlan, alternatives: [RouteAlternative])
    /// There are trails to do, but none fits in the time (or climb) limit.
    case nothingFits(NothingFits)
    /// There is nothing to plan at all; the summary says honestly why.
    case noCandidates(NoCandidates)
}

/// An extra route offered under "Show alternatives".
struct RouteAlternative: Sendable {
    enum Label: Sendable, Equatable, CaseIterable {
        case shortest, leastClimb, mostNewMiles

        /// Short chip text.
        var title: String {
            switch self {
            case .shortest: return "Shortest"
            case .leastClimb: return "Least climb"
            case .mostNewMiles: return "Most new miles"
            }
        }
    }

    let label: Label
    let plan: RoutePlan
    /// One line, relative to the best route, e.g. "28 min shorter, 1 fewer trail".
    let why: String
}

/// "Nothing fits in 1 h. The closest finish is X in 1 h 35 m."
struct NothingFits: Sendable {
    /// The trail that takes the least time when hiked alone.
    struct Closest: Sendable, Equatable {
        let key: TrailNetwork.TrailKey
        let name: String
        /// Exact time of the best route that finishes only this trail.
        let exactSeconds: Double
        /// `exactSeconds` rounded UP to the next 5 minutes; the time the
        /// one-tap "Plan with 1 h 35 m" action uses.
        let roundedSeconds: Double
    }

    let limitSeconds: Double
    /// nil when no trail can be finished at all (the climb limit rules every
    /// trail out, or each takes more than 24 h).
    let closest: Closest?
    /// Most new ground inside the limits, no whole trail finished. nil when
    /// even that is impossible (the start is too far from any new trail).
    let fallback: RoutePlan?
    /// Honest user-facing text.
    let message: String

    /// Time for the one-tap "re-plan with this time" action.
    var replanSeconds: Double? { closest?.roundedSeconds }
}

/// Why there is nothing to plan, with counts per reason.
struct NoCandidates: Sendable, Equatable {
    var completed = 0
    var noRoute = 0
    var splitTooFar = 0
    var piecesNotConnected = 0
    var notReachable = 0
    var tooFar = 0
    var total: Int { completed + noRoute + splitTooFar + piecesNotConnected + notReachable + tooFar }
    /// Trails left to do (not completed) that could never be finished here.
    var excludedOther: Int { total - completed }
    let message: String
}

/// Wording shared by the outcome types.
enum PlanText {
    /// "45 min", "1 h", "1 h 35 m".
    static func duration(_ seconds: Double) -> String {
        let minutes = Int((seconds / 60).rounded())
        if minutes < 60 { return "\(minutes) min" }
        let h = minutes / 60, m = minutes % 60
        return m == 0 ? "\(h) h" : "\(h) h \(m) m"
    }

    static func miles(_ meters: Double) -> String { String(format: "%.1f mi", meters / 1609.344) }

    static func trailCount(_ n: Int) -> String { n == 1 ? "1 trail" : "\(n) trails" }

    /// Rounds up to the next multiple of `step` seconds (a value already on a
    /// multiple stays).
    static func roundedUp(_ seconds: Double, step: Double = 300) -> Double {
        (((seconds - 1e-6) / step).rounded(.up)) * step
    }
}
