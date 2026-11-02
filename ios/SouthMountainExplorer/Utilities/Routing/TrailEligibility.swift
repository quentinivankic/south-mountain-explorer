import Foundation

/// Which trails a planned hike could finish ("Plan a Hike", Task 4).
///
/// Pure and deterministic. Goal of the planner: finish WHOLE uncompleted
/// trails. Completed trails are not goals (they may still be walked as
/// connectors, and the shortest-path costs here do walk through them).
///
/// A trail is a candidate when every one of its edges can be walked, and a
/// lower-bound estimate of "go there, cover it, come back / go to the end"
/// fits the time budget. Everything else is returned in `excluded` with ONE
/// reason, checked in this order:
///
/// 1. `.completed`: the injected lookup says it is done.
/// 2. `.noEdges`: the trail has no edge in the network (too short / unknown).
/// 3. `.splitTooFar(gap)`: its own pieces are more than `maxSplitGapMeters`
///    (0.25 mi) apart. Approved rule: "can't be finished in one route".
/// 4. `.piecesNotConnected(gap)`: pieces closer than that, but the network has
///    no path joining them (Task 1 only bridges gaps up to 30 m). The planner
///    walks network edges only, and an off-network hop between pieces is not
///    allowed in Release A, so the trail is excluded and the gap reported.
///    A trail whose pieces are joined through other trails is NOT flagged: the
///    walk between them is real network walking and is costed.
/// 5. `.disconnected`: the trail is in a different connected piece of the
///    network than the start (South Mountain has two big halves).
/// 6. `.outOfReach(seconds)`: the cheapest-cover estimate exceeds the budget.
///
/// The estimate is a LOWER bound: entry walk + every required edge once (its
/// faster direction) + exit walk. It never overstates, so `.outOfReach` never
/// drops a trail that could fit. It can understate for spurs or branches that
/// must be walked twice; Task 5's real route search is the exact judge.
/// "Required length" is network length (shared edges included, a self-retracing
/// trail counted once), never `Trail.distanceMi`.
struct TrailEligibility: Sendable {
    /// 0.25 mi: pieces of one trail farther apart than this cannot be finished
    /// in one route.
    static let maxSplitGapMeters = 402.336

    enum Trip: Sendable, Equatable {
        /// Back at the start. Reach prefilter radius is half the budget.
        case loop
        /// Ends at the inserted end point (or anywhere, if none was given).
        /// Reach prefilter radius is the whole budget.
        case oneWay
    }

    struct Options: Sendable {
        var budgetSeconds: Double
        var paceFactor = 1.0
        var trip = Trip.loop
        var maxSplitGapMeters = TrailEligibility.maxSplitGapMeters
        init(budgetSeconds: Double, paceFactor: Double = 1.0, trip: Trip = .loop) {
            self.budgetSeconds = budgetSeconds
            self.paceFactor = paceFactor
            self.trip = trip
        }
    }

    struct Candidate: Sendable, Equatable {
        let key: TrailNetwork.TrailKey
        let name: String
        /// Every edge that makes up the whole trail (ascending; shared edges
        /// included).
        let edgeIds: [Int]
        /// Network length of `edgeIds`, each edge once.
        let requiredMeters: Double
        /// Lower-bound seconds to reach, cover and leave the trail.
        let estimatedSeconds: Double
        /// Always true for a candidate (unreachable trails are in `excluded`).
        let isReachable: Bool
        /// Trail node the estimate enters / leaves by.
        let entryNodeId: Int
        let exitNodeId: Int?
    }

    enum Reason: Sendable, Equatable {
        case completed
        case noEdges
        case splitTooFar(maxGapMeters: Double)
        case piecesNotConnected(gapMeters: Double)
        case disconnected
        case outOfReach(estimatedSeconds: Double)

        /// Short user-facing text.
        var label: String {
            switch self {
            case .completed: return "already completed"
            case .noEdges: return "no routable path"
            case .splitTooFar: return "can't be finished in one route"
            case .piecesNotConnected: return "pieces aren't connected"
            case .disconnected: return "not reachable from the start"
            case .outOfReach: return "too far for the time"
            }
        }
    }

    struct Exclusion: Sendable, Equatable {
        let key: TrailNetwork.TrailKey
        let name: String
        let reason: Reason
    }

    /// Cheapest first (ties by key).
    let candidates: [Candidate]
    /// By key.
    let excluded: [Exclusion]

    func excluded(because match: (Reason) -> Bool) -> [Exclusion] {
        excluded.filter { match($0.reason) }
    }
}

/// Injected "is this trail done" lookup, so eligibility never touches
/// `ProgressService` (a main-actor singleton) directly.
struct TrailCompletionLookup: Sendable {
    let isComplete: @Sendable (TrailNetwork.TrailKey) -> Bool

    init(_ isComplete: @escaping @Sendable (TrailNetwork.TrailKey) -> Bool) {
        self.isComplete = isComplete
    }

    init(completed: Set<TrailNetwork.TrailKey>) {
        self.isComplete = { completed.contains($0) }
    }

    static let none = TrailCompletionLookup(completed: [])

    /// App adapter: snapshots `ProgressService.isComplete(_:areaId:)` (id match
    /// first, then geometry fingerprint) for every trail, once, on the main
    /// actor. The returned lookup is a plain value and safe to use anywhere.
    @MainActor
    static func progress(_ service: ProgressService, trails: [TrailNetwork.Input]) -> TrailCompletionLookup {
        var done = Set<TrailNetwork.TrailKey>()
        for input in trails where service.isComplete(input.trail, areaId: input.areaId) {
            done.insert(input.key)
        }
        return TrailCompletionLookup(completed: done)
    }
}

extension TrailEligibility {
    /// Classifies every trail in `trails` (plus any trail key the network
    /// knows). `trails` supplies display names.
    static func evaluate(
        network view: TrailNetworkWithEndpoints,
        trails: [TrailNetwork.Input],
        completion: TrailCompletionLookup,
        options: Options
    ) -> TrailEligibility {
        let net = view.network
        let startNode = view.start.nodeId
        let loop = options.trip == .loop
        let finishNode: Int? = loop ? startNode : view.end?.nodeId
        let budget = options.budgetSeconds
        let radius = loop ? budget / 2 : budget

        let costs = RouteCosts(network: net, paceFactor: options.paceFactor)
        let fromStart = net.shortestPaths(from: startNode, costs: costs)?.seconds
            ?? [Double](repeating: .infinity, count: net.nodes.count)
        var toFinish: [Double]?
        if let finishNode {
            let rev = net.reversedForRouting()
            toFinish = rev.shortestPaths(
                from: finishNode, costs: RouteCosts(network: rev, paceFactor: options.paceFactor))?.seconds
        }
        let component = components(of: net)

        var names: [TrailNetwork.TrailKey: String] = [:]
        for t in trails { names[t.key] = t.trail.name }
        let keys = Set(names.keys).union(net.trailKeys).sorted()

        var candidates: [Candidate] = []
        var excluded: [Exclusion] = []
        for key in keys {
            let name = names[key] ?? key.trailId
            func out(_ r: Reason) { excluded.append(Exclusion(key: key, name: name, reason: r)) }

            if completion.isComplete(key) { out(.completed); continue }
            let edgeIds = net.edgeIds(of: key)
            if edgeIds.isEmpty { out(.noEdges); continue }
            let gap = net.maxPieceGapMeters(of: key) ?? 0
            if gap > options.maxSplitGapMeters {
                out(.splitTooFar(maxGapMeters: gap)); continue
            }
            let comps = Set(edgeIds.map { component[net.edges[$0].from] })
            if comps.count > 1 { out(.piecesNotConnected(gapMeters: gap)); continue }
            if comps.first != component[startNode] { out(.disconnected); continue }

            var nodes = Set<Int>()
            var meters = 0.0
            var cover = 0.0
            for id in edgeIds {
                let e = net.edges[id]
                nodes.insert(e.from); nodes.insert(e.to)
                meters += e.lengthMeters
                cover += min(costs.walk(id, .forward).seconds, costs.walk(id, .backward).seconds)
            }
            let ordered = nodes.sorted()
            let entry = ordered.min { (fromStart[$0], $0) < (fromStart[$1], $1) }!
            let entrySeconds = fromStart[entry]

            // Cheap prefilter: nothing of the trail inside the reach radius.
            if entrySeconds > radius {
                out(.outOfReach(estimatedSeconds: entrySeconds * (loop ? 2 : 1) + cover)); continue
            }
            var exit: Int?
            var exitSeconds = 0.0
            if let toFinish {
                let x = ordered.min { (toFinish[$0], $0) < (toFinish[$1], $1) }!
                exit = x
                exitSeconds = toFinish[x]
            }
            let estimate = entrySeconds + cover + exitSeconds
            if !estimate.isFinite || estimate > budget {
                out(.outOfReach(estimatedSeconds: estimate)); continue
            }
            candidates.append(Candidate(
                key: key, name: name, edgeIds: edgeIds, requiredMeters: meters,
                estimatedSeconds: estimate, isReachable: true, entryNodeId: entry, exitNodeId: exit))
        }
        candidates.sort { ($0.estimatedSeconds, $0.key) < ($1.estimatedSeconds, $1.key) }
        return TrailEligibility(candidates: candidates, excluded: excluded)
    }

    /// Connected-piece id per node (all edges, bridges and connectors count).
    private static func components(of net: TrailNetwork) -> [Int] {
        var parent = Array(0..<net.nodes.count)
        func find(_ x: Int) -> Int {
            var r = x
            while parent[r] != r { r = parent[r] }
            var c = x
            while parent[c] != r { let n = parent[c]; parent[c] = r; c = n }
            return r
        }
        for e in net.edges {
            let a = find(e.from), b = find(e.to)
            if a != b { parent[max(a, b)] = min(a, b) }
        }
        return net.nodes.indices.map { find($0) }
    }
}

extension TrailNetwork {
    /// Same graph with every edge flipped, so a forward Dijkstra from X gives
    /// the cost of walking each node TO X in the original.
    fileprivate func reversedForRouting() -> TrailNetwork {
        let flipped = edges.map {
            Edge(id: $0.id, from: $0.to, to: $0.from, kind: $0.kind, trailKeys: $0.trailKeys,
                 polyline: $0.polyline.reversed(), coordinates: $0.coordinates.reversed(),
                 lengthMeters: $0.lengthMeters, gainForwardFt: $0.gainBackwardFt,
                 gainBackwardFt: $0.gainForwardFt, elevationKnown: $0.elevationKnown)
        }
        return TrailNetwork(projector: projector, nodes: nodes, edges: flipped,
                            trailEdgeIds: trailEdgeIds, pieceGaps: pieceGaps)
    }
}
