import Foundation

/// A routable graph built from trail geometry ("Plan a Hike", Task 1).
///
/// Trail data has no node ids: a `Trail` is a bag of polylines in arbitrary
/// OSM order and direction, often disconnected. This type recovers the graph:
///
/// - **Nodes** are junctions and dead ends, in local metres around one
///   `TurnCueProjector` origin per network.
/// - **Edges** are the stretches of trail between nodes.
/// - Trails are split where they meet: exact shared vertices, endpoints within
///   `contactMeters` of another trail (T-junctions), vertices within
///   `vertexMergeMeters` of another trail (collinear / co-aligned geometry),
///   and proper X crossings.
/// - A stretch two trails share (two names on one path) is ONE edge carrying
///   both trail keys, so its length is counted once.
/// - Gaps up to `bridgeMeters` between a trail endpoint and the nearest other
///   trail become `.bridge` edges, flagged as an unverified connection. Wider
///   gaps are never bridged.
///
/// Pure, deterministic value types. Input order does not matter (trails are
/// sorted by key); the same input always yields the same ids.
///
/// Feed it `area.rawTrails ?? area.trails`: the decimated `trails` set has
/// Douglas-Peucker 5 m simplification that moves junctions.
struct TrailNetwork: Sendable {
    /// A trail namespaced by area. Trail ids collide across unrelated areas
    /// (`ice-age-trail` appears in 47), so ids are never used alone.
    struct TrailKey: Hashable, Comparable, Sendable, CustomStringConvertible {
        let areaId: String
        let trailId: String
        static func < (a: TrailKey, b: TrailKey) -> Bool {
            (a.areaId, a.trailId) < (b.areaId, b.trailId)
        }
        var description: String { "\(areaId)/\(trailId)" }
    }

    struct Input: Sendable {
        let areaId: String
        let trail: Trail
        init(areaId: String, trail: Trail) {
            self.areaId = areaId
            self.trail = trail
        }
        var key: TrailKey { TrailKey(areaId: areaId, trailId: trail.id) }
    }

    enum EdgeKind: Sendable, Equatable {
        /// Real trail geometry.
        case trail
        /// Synthetic connection across a small gap. Unverified: nothing in the
        /// data says it can be walked.
        case bridge
        /// Straight-line off-trail walk from a start / end point to the nearest
        /// trail ("Plan a Hike", Task 2). Only ever present in a network view
        /// made by `TrailNetworkSnapper`; the cached base network has none.
        /// Flat: no elevation is known, so gains are 0.
        case connector
    }

    struct Coordinate: Sendable, Equatable {
        let lat: Double
        let lon: Double
    }

    struct Node: Sendable, Identifiable {
        let id: Int
        let point: LocalPt
        let coordinate: Coordinate
        /// Ids of the edges that start or end here (ascending).
        let edgeIds: [Int]
    }

    struct Edge: Sendable, Identifiable {
        let id: Int
        let from: Int
        let to: Int
        let kind: EdgeKind
        /// Trails that run along this edge (sorted). For a `.bridge` these are
        /// the trails on either side of the gap; bridges are NOT part of any
        /// trail's `edgeIds(of:)`.
        let trailKeys: [TrailKey]
        /// `from` to `to`, local metres.
        let polyline: [LocalPt]
        /// Same vertices as `polyline`.
        let coordinates: [Coordinate]
        let lengthMeters: Double
        /// Feet climbed walking `from` to `to` / `to` to `from`.
        let gainForwardFt: Double
        let gainBackwardFt: Double
        /// false when no owning trail had a usable `profileFt`; gains are 0.
        let elevationKnown: Bool
        var isUnverifiedConnection: Bool { kind == .bridge }
        /// true for the dashed start / end connector.
        var isOffTrail: Bool { kind == .connector }
    }

    struct Options: Sendable {
        /// Endpoint-to-trail distance treated as touching.
        var contactMeters = 6.0
        /// Largest gap that is bridged. Never exceeded.
        var bridgeMeters = 30.0
        /// Mid-trail vertices this close to another trail are merged onto it,
        /// and cut points this close to an existing vertex reuse it.
        var vertexMergeMeters = 1.0
        init() {}
    }

    let projector: TurnCueProjector
    let nodes: [Node]
    let edges: [Edge]
    /// Internal (not private) so `TrailNetworkSnapper` can derive a view with a
    /// start point spliced in without mutating this network.
    let trailEdgeIds: [TrailKey: [Int]]
    let pieceGaps: [TrailKey: Double]

    init(
        projector: TurnCueProjector,
        nodes: [Node],
        edges: [Edge],
        trailEdgeIds: [TrailKey: [Int]],
        pieceGaps: [TrailKey: Double]
    ) {
        self.projector = projector
        self.nodes = nodes
        self.edges = edges
        self.trailEdgeIds = trailEdgeIds
        self.pieceGaps = pieceGaps
    }

    /// Builds the network. Deterministic and independent of input order.
    init(trails: [Input], options: Options = Options()) {
        self = TrailNetworkBuilder(inputs: trails, options: options).build()
    }

    /// Convenience: one area, using the dense geometry.
    init(area: Area, options: Options = Options()) {
        let source = area.rawTrails ?? area.trails
        self.init(trails: source.map { Input(areaId: area.id, trail: $0) }, options: options)
    }

    // MARK: - Queries

    /// Every trail in the network.
    var trailKeys: [TrailKey] { trailEdgeIds.keys.sorted() }

    /// Edge ids (ascending) whose geometry is part of `key`. This is what
    /// "walking the whole trail" means. Empty if the trail is unknown or too
    /// short to form an edge. Bridges are excluded.
    func edgeIds(of key: TrailKey) -> [Int] { trailEdgeIds[key] ?? [] }

    /// Largest gap in metres between consecutive pieces of the trail, ordered
    /// the way the elevation pipeline chains them (least total gap). 0 for a
    /// single-piece trail, nil for an unknown key. Callers use this for the
    /// 0.25 mi split-trail exclusion.
    func maxPieceGapMeters(of key: TrailKey) -> Double? { pieceGaps[key] }

    var bridgeEdges: [Edge] { edges.filter { $0.kind == .bridge } }

    /// Edges carrying more than one trail (co-aligned stretches).
    var sharedEdges: [Edge] { edges.filter { $0.kind == .trail && $0.trailKeys.count > 1 } }

    /// Latitude / longitude of a local point (inverse of the projector).
    func coordinate(of p: LocalPt) -> Coordinate {
        TrailNetworkBuilder.coordinate(of: p, projector: projector)
    }
}
