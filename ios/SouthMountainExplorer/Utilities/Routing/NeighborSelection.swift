import Foundation

/// Picks which neighboring areas to load for a plan ("Plan a Hike", Task 7).
/// Pure and deterministic.
///
/// Reach: a loop can go at most half the time budget out and must come back;
/// a one-way hike can go the whole budget. The straight-line reach is that time
/// times 6 km/h. Tobler's function peaks at 6 km/h (a slight downhill), so no
/// walker at pace 1.0 beats it; a faster pace factor scales it. Straight line
/// is an upper bound on trail distance, so the radius never excludes an area
/// the walker could actually reach. It over-includes, which only costs load.
enum NeighborSelection {
    static let walkingSpeedMps = 6.0 / 3.6
    static let defaultMaxExtra = 6

    struct Candidate: Sendable, Equatable {
        let id: String
        let centerLat: Double
        let centerLon: Double
        /// [minLon, minLat, maxLon, maxLat] when known.
        let bbox: [Double]?
        init(id: String, centerLat: Double, centerLon: Double, bbox: [Double]? = nil) {
            self.id = id
            self.centerLat = centerLat
            self.centerLon = centerLon
            self.bbox = bbox
        }
    }

    enum Reason: Sendable, Equatable {
        case included
        case primary
        case hiddenAlias
        case outOfReach
        case overCap
    }

    struct Decision: Sendable, Equatable {
        let id: String
        let reason: Reason
        /// Straight-line metres from the start to the bbox (centre if unknown).
        let distanceMeters: Double
        var included: Bool { reason == .included }
    }

    struct Result: Sendable, Equatable {
        let radiusMeters: Double
        /// Every candidate with its reason, sorted by distance then id.
        let decisions: [Decision]
        /// Included neighbor ids, nearest first (primary not listed).
        var includedIds: [String] { decisions.filter(\.included).map(\.id) }
    }

    static func radiusMeters(budgetSeconds: Double, shape: RoutePlanRequest.Shape, paceFactor: Double) -> Double {
        let out = shape == .loop ? budgetSeconds / 2 : budgetSeconds
        return max(0, out) * walkingSpeedMps * max(1, paceFactor)
    }

    static func select(
        primaryId: String,
        startLat: Double, startLon: Double,
        budgetSeconds: Double, shape: RoutePlanRequest.Shape, paceFactor: Double = 1.0,
        candidates: [Candidate],
        isHiddenAlias: (String) -> Bool = { _ in false },
        maxExtra: Int = defaultMaxExtra
    ) -> Result {
        let radius = radiusMeters(budgetSeconds: budgetSeconds, shape: shape, paceFactor: paceFactor)
        var unique: [Candidate] = []
        var seen = Set<String>()
        for c in candidates where seen.insert(c.id).inserted { unique.append(c) }
        let measured = unique.map { ($0, distance(startLat, startLon, $0)) }
            .sorted { ($0.1, $0.0.id) < ($1.1, $1.0.id) }
        var decisions: [Decision] = []
        var taken = 0
        for (c, d) in measured {
            let reason: Reason
            if c.id == primaryId { reason = .primary }
            else if isHiddenAlias(c.id) { reason = .hiddenAlias }
            else if d > radius { reason = .outOfReach }
            else if taken >= maxExtra { reason = .overCap }
            else { reason = .included; taken += 1 }
            decisions.append(Decision(id: c.id, reason: reason, distanceMeters: d))
        }
        return Result(radiusMeters: radius, decisions: decisions)
    }

    /// Metres from the start to the bbox (0 inside), or to the centre.
    static func distance(_ lat: Double, _ lon: Double, _ c: Candidate) -> Double {
        var tLat = c.centerLat, tLon = c.centerLon
        if let b = c.bbox, b.count == 4 {
            tLat = min(max(lat, b[1]), b[3])
            tLon = min(max(lon, b[0]), b[2])
        }
        let mpd = TurnCueProjector.metersPerDegree
        let dy = (tLat - lat) * mpd
        let dx = (tLon - lon) * mpd * cos(lat * .pi / 180)
        return (dx * dx + dy * dy).squareRoot()
    }
}
