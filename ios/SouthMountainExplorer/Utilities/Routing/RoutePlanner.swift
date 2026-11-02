import Foundation

/// Input to the Plan a Hike route planner ("Plan a Hike", Task 5).
struct RoutePlanRequest: Sendable {
    enum Shape: Sendable, Equatable {
        /// Back at the start node.
        case loop
        /// Ends at the inserted end point (or anywhere, when no end was given).
        case oneWay
    }

    /// Network with the start (and optional end) spliced in.
    let network: TrailNetworkWithEndpoints
    /// From `TrailEligibility.evaluate`. Only these trails can be credited.
    let candidates: [TrailEligibility.Candidate]
    /// Which trails are done. Decides what counts as "new" ground.
    let completion: TrailCompletionLookup
    let timeLimitSeconds: Double
    /// Optional cap on climbing in the walking direction, feet.
    let maxGainFt: Double?
    let paceFactor: Double
    let shape: Shape

    init(
        network: TrailNetworkWithEndpoints,
        candidates: [TrailEligibility.Candidate],
        completion: TrailCompletionLookup = .none,
        timeLimitSeconds: Double,
        maxGainFt: Double? = nil,
        paceFactor: Double = 1.0,
        shape: Shape = .loop
    ) {
        self.network = network
        self.candidates = candidates
        self.completion = completion
        self.timeLimitSeconds = timeLimitSeconds
        self.maxGainFt = maxGainFt
        self.paceFactor = paceFactor
        self.shape = shape
    }
}

/// A planned hike: one continuous walk over network edges.
struct RoutePlan: Sendable {
    struct FinishedTrail: Sendable, Equatable {
        let key: TrailNetwork.TrailKey
        let name: String
        /// Network length of the whole trail, metres.
        let requiredMeters: Double
        /// Index into `steps` of the step that completed it.
        let completedAtStep: Int
    }

    /// Per-step context for the UI and later tasks.
    struct StepInfo: Sendable, Equatable {
        /// Trail this step is on. nil for connectors and bridges with no
        /// trail. For a shared edge: the trail being finished here if any,
        /// else an uncompleted trail, else the first.
        let trailKey: TrailNetwork.TrailKey?
        let kind: TrailNetwork.EdgeKind
        /// true when this step is part of covering a goal trail (as opposed to
        /// walking between goals).
        let isGoalCover: Bool
        /// Bridges are unverified connections; connectors are off-trail.
        var isFlagged: Bool { kind != .trail }
    }

    /// Ordered walk from the start node to the end node.
    let steps: [RouteStep]
    /// Same length and order as `steps`.
    let stepInfo: [StepInfo]
    /// Every trail whose required edges are all in `steps`, in the order the
    /// walk completes them. Includes incidental finishes.
    let finished: [FinishedTrail]
    let metrics: RouteMetrics
    let shape: RoutePlanRequest.Shape
    let startNodeId: Int
    /// Node the walk ends at (the start for a loop, the last node otherwise).
    let endNodeId: Int
    /// Candidate plans the search scored (a work counter, not a time).
    let evaluations: Int

    var finishedTrailKeys: [TrailNetwork.TrailKey] { finished.map(\.key) }
    var newMiles: Double { metrics.newMeters / 1609.344 }
    var distanceMiles: Double { metrics.meters / 1609.344 }
    /// Metres on bridges / connectors, which the UI must flag.
    var flaggedMeters: Double { metrics.bridgeMeters + metrics.connectorMeters }
}

/// The Plan a Hike route planner.
///
/// Goal: from a start, finish as many WHOLE uncompleted trails as possible,
/// then walk more new ground, then less time, inside a time limit and an
/// optional gain limit. A trail is finished only when every one of its
/// required edges is in the walk. Completed trails may be walked as
/// connectors. Only network edges are used (bridges and the connector
/// included, and flagged in the result).
///
/// Algorithm (heuristic, deterministic, no randomness, no clock):
///
/// 1. **Jobs.** Each candidate trail is a job: "walk all its required edges".
///    A job has up to 7 boundary nodes (its ends, junctions, the nodes nearest
///    the start / finish, and the start / end if on the trail). For every
///    (entry, exit) pair of boundary nodes the cheapest known covering walk is
///    built:
///    - a simple path: exact, in closed form (visit both ends, either order);
///    - any other connected trail (branches, loops, tees): a Chinese-postman
///      cover, i.e. duplicate the cheapest paths so only entry and exit have
///      odd degree, then walk an Euler trail. Exact on trees and single loops;
///    - a trail in several pieces joined only through other trails: nearest
///      edge first, repositioning along the network.
///    Every variant walks every required edge; the final check does not trust
///    the construction.
/// 2. **Clusters.** Two candidate trails that touch also become one extra job
///    covering both together (unless together they are just one path). That
///    lets a spur be walked from the middle of the trail it leaves, or a ring
///    made of two trails be lapped from any point.
/// 3. **Distances.** One truncated Dijkstra (Task 3) from every boundary node,
///    the start and the finish gives time and gain between any two of them.
/// 4. **Greedy insertion.** Start from the direct start-to-finish walk. Repeatedly
///    insert the pending job at the best position and variant, by
///    (trails finished + small new-miles bonus) per extra second, while time
///    and gain stay in limits including the return leg. After every insertion
///    the exact walk is checked and trails it finished incidentally are
///    credited. Several seeds are built (three scorings, plus "this trail
///    first" for the most promising trails) and the best few are polished.
/// 5. **Local improvement.** Best-improvement hill climbing over: remove a job,
///    insert a pending job, replace one with another, swap, move, reverse a
///    stretch, merge two jobs into a cluster or split one, and "remove one
///    then greedy refill" (trades one trail for two cheaper ones). After every
///    order change the best entry / exit of every job is re-chosen by dynamic
///    programming, so each trail is tried in both directions. A move is kept
///    only if the objective strictly improves: more trails finished, then
///    more new metres, then less time. Capped by a solve counter.
///
/// Ties break by trail key, so input order never matters.
///
/// Limits: not optimal in general (this is NP-hard). Measured against
/// exhaustive search on small random graphs it finds the optimum nearly
/// always, but not when the best hike weaves three or more trails together
/// (for example a ring made of alternating pieces of three trails). Connectors
/// are time-shortest paths, so a gain limit can reject a route that a longer,
/// flatter connector would allow, and a connector is not bent to pick up extra
/// new ground. Spare time is not used to wander over extra partial ground.
enum RoutePlanner {
    /// nil when no candidate trail can be finished in the limits (Task 6
    /// builds the user message and fallback).
    static func plan(_ request: RoutePlanRequest) -> RoutePlan? {
        guard request.timeLimitSeconds > 0, request.timeLimitSeconds.isFinite else { return nil }
        guard let engine = RoutePlanEngine(request) else { return nil }
        return engine.run()
    }
}
