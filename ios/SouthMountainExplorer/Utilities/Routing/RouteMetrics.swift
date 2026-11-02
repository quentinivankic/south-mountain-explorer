import Foundation

/// Totals for an ordered edge walk ("Plan a Hike", Task 3).
struct RouteMetrics: Sendable, Equatable {
    var meters = 0.0
    var seconds = 0.0
    /// Climb / descent in the walking direction, feet.
    var gainFt = 0.0
    var lossFt = 0.0
    var trailMeters = 0.0
    var connectorMeters = 0.0
    var bridgeMeters = 0.0
    /// Walked length per trail key. Every key on a shared edge is credited the
    /// full edge length; an edge walked twice counts twice (it is walked twice).
    /// Bridges and connectors are not part of any trail.
    var metersByTrail: [TrailNetwork.TrailKey: Double] = [:]
    /// Distinct ground on trails not in the completed set, metres. An edge
    /// counts once however many times it is walked and however many trails
    /// share it; it is new when ANY of its trails is uncompleted. Bridges and
    /// connectors are never new.
    var newMeters = 0.0
    /// false when consecutive steps do not meet at a node.
    var isContinuous = true

    var trailKeys: [TrailNetwork.TrailKey] { metersByTrail.keys.sorted() }

    /// nil when a step names an edge that does not exist.
    init?(network: TrailNetwork, steps: [RouteStep], paceFactor: Double = 1.0,
          completed: Set<TrailNetwork.TrailKey> = []) {
        var seenNew = Set<Int>()
        var at: Int? = nil
        for s in steps {
            guard s.edgeId >= 0, s.edgeId < network.edges.count else { return nil }
            let e = network.edges[s.edgeId]
            let w = TrailNetwork.walk(e, direction: s.direction, paceFactor: paceFactor)
            meters += w.meters
            seconds += w.seconds
            gainFt += w.gainFt
            lossFt += w.lossFt
            switch e.kind {
            case .trail: trailMeters += w.meters
            case .connector: connectorMeters += w.meters
            case .bridge: bridgeMeters += w.meters
            }
            if e.kind == .trail {
                for k in e.trailKeys { metersByTrail[k, default: 0] += w.meters }
                if e.trailKeys.contains(where: { !completed.contains($0) }), seenNew.insert(e.id).inserted {
                    newMeters += w.meters
                }
            }
            let start = s.direction == .forward ? e.from : e.to
            let end = s.direction == .forward ? e.to : e.from
            if let a = at, a != start { isContinuous = false }
            at = end
        }
    }
}
