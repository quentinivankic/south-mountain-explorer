import Foundation

/// Where a planned hike starts (or, for a one-way hike, ends) - "Plan a Hike",
/// Task 2. The user picks a trailhead, a parking spot, or drops a pin. All
/// three are just a coordinate plus a display name; the kind is kept so the UI
/// can label the plan and later tasks can tell a parking lot from a pin.
enum StartPoint: Sendable, Equatable {
    case parking(ParkingLot)
    case trailhead(name: String, lat: Double, lon: Double)
    case pin(lat: Double, lon: Double, name: String? = nil)

    /// A trailhead taken from a parking-pool / area lot record.
    static func trailheadLot(_ lot: ParkingLot) -> StartPoint {
        .trailhead(name: lot.name ?? "Trailhead", lat: lot.lat, lon: lot.lon)
    }

    var lat: Double {
        switch self {
        case .parking(let lot): return lot.lat
        case .trailhead(_, let lat, _): return lat
        case .pin(let lat, _, _): return lat
        }
    }

    var lon: Double {
        switch self {
        case .parking(let lot): return lot.lon
        case .trailhead(_, _, let lon): return lon
        case .pin(_, let lon, _): return lon
        }
    }

    var coordinate: TrailNetwork.Coordinate { TrailNetwork.Coordinate(lat: lat, lon: lon) }

    var displayName: String {
        switch self {
        case .parking(let lot): return lot.name ?? "Parking"
        case .trailhead(let name, _, _): return name
        case .pin(_, _, let name): return name ?? "Dropped pin"
        }
    }
}

/// Why a start / end point could not be joined to the network.
enum TrailNetworkStartError: Error, Equatable, Sendable {
    /// The nearest trail is farther than `maxConnectorMeters`. Carries the true
    /// distance in metres so the UI can say how far away the trail is.
    case tooFarFromTrails(Double)
    /// The network has no trail geometry to snap to.
    case noTrails
    /// Latitude or longitude is not a finite number.
    case invalidCoordinate
}

/// Where a point lands on the network: the nearest point on any trail edge.
struct TrailNetworkSnap: Sendable, Equatable {
    /// Edge in the BASE network that was snapped to (always `.trail`; bridges
    /// are never snap targets).
    let edgeId: Int
    /// Metres along that edge from its `from` node.
    let arcMeters: Double
    let edgeLengthMeters: Double
    /// Snapped point, local metres / lat-lon.
    let point: LocalPt
    let coordinate: TrailNetwork.Coordinate
    /// Straight-line distance from the input point to the snapped point.
    let distanceMeters: Double
    /// Trails along the snapped edge.
    let trailKeys: [TrailNetwork.TrailKey]
    /// Connected piece of the network the point joins (0 = largest). Pieces
    /// count `.bridge` edges as connections, because a route may cross them
    /// (flagged unverified).
    let component: TrailNetworkSnapper.Component
}

/// A start / end point after it was spliced into a network view.
struct TrailNetworkEndpoint: Sendable, Equatable {
    let point: StartPoint
    let snap: TrailNetworkSnap
    /// Node the plan starts / ends at (in the returned view). The new start
    /// node when off-trail, else the on-trail node itself.
    let nodeId: Int
    /// On-trail node the plan joins (the split node, or an existing node when
    /// the snap landed within `nodeMergeMeters` of one).
    let trailNodeId: Int
    /// The `.connector` edge, nil when the point is on a trail.
    let connectorEdgeId: Int?
    /// Connector length (straight line, flat); 0 when on a trail.
    let connectorMeters: Double
    var isOffTrail: Bool { connectorEdgeId != nil }
}

/// A network view with start (and optional end) spliced in. `network` is a
/// value copy derived from the base network; the base is untouched.
struct TrailNetworkWithEndpoints: Sendable {
    let network: TrailNetwork
    let start: TrailNetworkEndpoint
    let end: TrailNetworkEndpoint?
}
