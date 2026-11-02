import Foundation

/// Snaps start / end points to a `TrailNetwork` and splices them in
/// ("Plan a Hike", Task 2).
///
/// Build one per cached base network (it holds a spatial grid and the
/// connected-component table); then call `snap` / `insert` for as many plans
/// as you like. Nothing here mutates the base network: `insert` returns a
/// value copy with the start (and end) added.
///
/// Rules:
/// - **Nearest wins.** The snap target is the nearest point on any `.trail`
///   edge, measured in local metres by exact point-to-segment projection.
///   Bridges (unverified synthetic edges) are never snap targets.
/// - **On trail within `onTrailToleranceMeters` (10 m):** no connector; the
///   plan starts at the snap point. 10 m is typical handheld-GPS / dropped-pin
///   error, a little above Task 1's 6 m "touching" radius.
/// - **Otherwise** a straight `.connector` edge (flat, flagged off-trail) joins
///   the point to the snap point.
/// - **Farther than `maxConnectorMeters` (1 mile):** `.tooFarFromTrails`. A
///   straight line across more than a mile of unknown ground is not a hike
///   plan; the user should move the pin.
/// - **Components:** pure nearest. The snap reports which connected piece it
///   joined (`Component`: id 0 is the largest, plus its size) so callers can
///   warn about a small fragment. No silent re-targeting to a bigger piece.
/// - A snap within `nodeMergeMeters` (1 m) of a node or of another snap joins
///   that node instead of creating a sliver edge.
struct TrailNetworkSnapper: Sendable {
    struct Options: Sendable {
        var onTrailToleranceMeters = 10.0
        var maxConnectorMeters = 1_609.344
        var nodeMergeMeters = 1.0
        init() {}
    }

    /// A connected piece of the base network (bridges count as connections).
    struct Component: Sendable, Equatable {
        /// 0 = largest by node count, ties broken by lowest node id.
        let id: Int
        let nodeCount: Int
        let edgeCount: Int
        /// Sum of `.trail` edge lengths, each edge once.
        let lengthMeters: Double
        let isLargest: Bool
    }

    let network: TrailNetwork
    let options: Options
    let components: [Component]

    private let componentOfNode: [Int]
    private let grid: SegmentGrid
    /// Snap candidates: one per segment of every `.trail` edge.
    private let segEdge: [Int32]
    private let segIndex: [Int32]
    /// Cumulative arc length per edge polyline.
    private let edgeCum: [[Double]]

    private static let gridCell = 100.0

    init(network: TrailNetwork, options: Options = Options()) {
        self.network = network
        self.options = options

        var edgeCum: [[Double]] = []
        var grid = SegmentGrid(cell: Self.gridCell)
        var segEdge: [Int32] = []
        var segIndex: [Int32] = []
        for e in network.edges {
            edgeCum.append(TurnCueGeometry.cumulative(e.polyline))
            guard e.kind == .trail, e.polyline.count >= 2 else { continue }
            for s in 0..<(e.polyline.count - 1) {
                grid.insert(Int32(segEdge.count), a: e.polyline[s], b: e.polyline[s + 1])
                segEdge.append(Int32(e.id))
                segIndex.append(Int32(s))
            }
        }
        self.edgeCum = edgeCum
        self.grid = grid
        self.segEdge = segEdge
        self.segIndex = segIndex

        // Components: union-find over nodes, all edges (bridges connect).
        var parent = Array(0..<network.nodes.count)
        func find(_ x: Int) -> Int {
            var r = x
            while parent[r] != r { r = parent[r] }
            var c = x
            while parent[c] != r { let n = parent[c]; parent[c] = r; c = n }
            return r
        }
        for e in network.edges {
            let a = find(e.from), b = find(e.to)
            if a != b { parent[max(a, b)] = min(a, b) }
        }
        var nodeCount: [Int: Int] = [:]
        var edgeCount: [Int: Int] = [:]
        var length: [Int: Double] = [:]
        for n in network.nodes { nodeCount[find(n.id), default: 0] += 1 }
        for e in network.edges {
            let r = find(e.from)
            edgeCount[r, default: 0] += 1
            if e.kind == .trail { length[r, default: 0] += e.lengthMeters }
        }
        // Roots are the lowest node id of each piece, so (size desc, root asc)
        // is deterministic.
        let roots = nodeCount.keys.sorted { (nodeCount[$0]!, -$0) > (nodeCount[$1]!, -$1) }
        var idOfRoot: [Int: Int] = [:]
        var comps: [Component] = []
        for (i, r) in roots.enumerated() {
            idOfRoot[r] = i
            comps.append(Component(id: i, nodeCount: nodeCount[r]!, edgeCount: edgeCount[r] ?? 0,
                                   lengthMeters: length[r] ?? 0, isLargest: i == 0))
        }
        self.components = comps
        self.componentOfNode = network.nodes.map { idOfRoot[find($0.id)]! }
    }

    // MARK: - Snapping

    /// Snaps a start point. Throws `TrailNetworkStartError`.
    func snap(_ point: StartPoint) throws -> TrailNetworkSnap {
        try snap(lat: point.lat, lon: point.lon)
    }

    /// Snaps the END of a one-way hike. Identical logic to `snap`; named so
    /// call sites read clearly.
    func snapEnd(_ point: StartPoint) throws -> TrailNetworkSnap {
        try snap(lat: point.lat, lon: point.lon)
    }

    func snap(lat: Double, lon: Double) throws -> TrailNetworkSnap {
        guard lat.isFinite, lon.isFinite, abs(lat) <= 90, abs(lon) <= 180 else {
            throw TrailNetworkStartError.invalidCoordinate
        }
        guard !segEdge.isEmpty else { throw TrailNetworkStartError.noTrails }
        let p = network.projector.local(lat: lat, lon: lon)
        let cap = options.maxConnectorMeters

        // Widen the grid search until a hit is provably the nearest: any
        // segment within R of p overlaps a cell inside the box p +/- R.
        var radius = Self.gridCell
        while true {
            let r = min(radius, cap)
            if let hit = nearest(to: p, within: r), hit.distance <= r {
                return makeSnap(hit)
            }
            if r >= cap { break }
            radius *= 2
        }
        // Nothing within the cap. Find the true nearest only to report it.
        let far = nearestBruteForce(to: p)
        let coord = network.coordinate(of: far.point)
        throw TrailNetworkStartError.tooFarFromTrails(
            Self.haversine(lat, lon, coord.lat, coord.lon))
    }

    private struct Hit {
        let seg: Int
        let point: LocalPt
        let distance: Double
        let arc: Double
    }

    private func nearest(to p: LocalPt, within r: Double) -> Hit? {
        var best: Hit?
        var seen = Set<Int32>()
        for cx in grid.cellRange(p.x - r, p.x + r) {
            for cy in grid.cellRange(p.y - r, p.y + r) {
                guard let ids = grid.cells[SegmentGrid.key(cx, cy)] else { continue }
                for id in ids where seen.insert(id).inserted {
                    consider(Int(id), p, &best)
                }
            }
        }
        return best
    }

    private func nearestBruteForce(to p: LocalPt) -> Hit {
        var best: Hit?
        for id in 0..<segEdge.count { consider(id, p, &best) }
        return best!
    }

    /// Keeps the closer hit; exact ties go to the lowest edge id, then segment.
    private func consider(_ id: Int, _ p: LocalPt, _ best: inout Hit?) {
        let edge = network.edges[Int(segEdge[id])]
        let s = Int(segIndex[id])
        let a = edge.polyline[s], b = edge.polyline[s + 1]
        let dx = b.x - a.x, dy = b.y - a.y
        let len2 = dx * dx + dy * dy
        var t = len2 > 0 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2 : 0
        t = min(1, max(0, t))
        let q = LocalPt(x: a.x + dx * t, y: a.y + dy * t)
        let d = TurnCueGeometry.distance(p, q)
        let arc = edgeCum[edge.id][s] + t * sqrt(len2)
        let candidate = Hit(seg: id, point: q, distance: d, arc: arc)
        guard let cur = best else { best = candidate; return }
        if d < cur.distance
            || (d == cur.distance && (segEdge[id], segIndex[id]) < (segEdge[cur.seg], segIndex[cur.seg])) {
            best = candidate
        }
    }

    private func makeSnap(_ hit: Hit) -> TrailNetworkSnap {
        let edge = network.edges[Int(segEdge[hit.seg])]
        let length = edgeCum[edge.id].last ?? edge.lengthMeters
        return TrailNetworkSnap(
            edgeId: edge.id,
            arcMeters: min(max(hit.arc, 0), length),
            edgeLengthMeters: length,
            point: hit.point,
            coordinate: network.coordinate(of: hit.point),
            distanceMeters: hit.distance,
            trailKeys: edge.trailKeys,
            component: components[componentOfNode[edge.from]])
    }

    /// Great-circle metres (reports the distance of a far-away pin honestly,
    /// where the local flat frame would distort).
    static func haversine(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let r = 6_371_000.0
        let p1 = lat1 * .pi / 180, p2 = lat2 * .pi / 180
        let dp = p2 - p1, dl = (lon2 - lon1) * .pi / 180
        let h = sin(dp / 2) * sin(dp / 2) + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2)
        return 2 * r * asin(min(1, sqrt(h)))
    }

    // Used by the splice extension.
    func cumulative(ofEdge id: Int) -> [Double] { edgeCum[id] }
}
