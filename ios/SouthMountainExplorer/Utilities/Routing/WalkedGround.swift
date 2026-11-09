import Foundation

/// Ground the user has already walked, from their saved GPS tracks ("Plan a
/// Hike" round 4).
///
/// Before this, the planner only knew whole-trail completion: a trail was new
/// in full until it was marked complete, so a trail walked 90 percent of the
/// way still counted all its miles as new. Now a network edge is new only for
/// the part of it no saved track went along.
///
/// The rule is the one the app's progress UI uses for a trail's walked part
/// (the "% remaining" bar from `measureCoverageByLength` and the orange walked
/// overlay in `TrailMapView.trailNodeRuns`): the line is sampled every
/// `stepMeters`, a sample counts when a GPS point is within `bufferMeters`
/// (the same 10 m), and a stretch counts as walked when both its ends do.
/// A gap shorter than `gapFillMeters` between two walked stretches is a GPS
/// dropout and counts as walked.
///
/// Trails marked complete stay fully walked whatever the tracks say; that part
/// is still `TrailCompletionLookup.isComplete`.
struct WalkedGround: Sendable {
    static let bufferMeters = 10.0
    static let stepMeters = 6.0
    static let gapFillMeters = 20.0

    private let grid: SpatialGrid?

    /// No tracks: every edge of an uncompleted trail is new in full.
    static let none = WalkedGround()

    init() { grid = nil }

    init(paths: [[GpsPoint]]) {
        var g = SpatialGrid()
        var count = 0
        for path in paths {
            for p in path where p.count >= 2 {
                g.insert(p)
                count += 1
            }
        }
        grid = count > 0 ? g : nil
    }

    var isEmpty: Bool { grid == nil }

    /// Share of the line, 0 to 1, that no saved track walked. 1 with no tracks
    /// or a line under two points.
    func unwalkedFraction(of line: [TrailNetwork.Coordinate]) -> Double {
        guard let grid, line.count >= 2 else { return 1 }
        // Pieces between consecutive samples: length and walked flag.
        var pieces: [(meters: Double, walked: Bool)] = []
        var prevHit = grid.hasNeighbor(lat: line[0].lat, lon: line[0].lon, withinMeters: Self.bufferMeters)
        for i in 0..<(line.count - 1) {
            let a = line[i], b = line[i + 1]
            let len = haversineDistanceM(lat1: a.lat, lon1: a.lon, lat2: b.lat, lon2: b.lon)
            guard len > 0 else { continue }
            let n = max(1, Int((len / Self.stepMeters).rounded(.up)))
            for k in 1...n {
                let t = Double(k) / Double(n)
                let hit = grid.hasNeighbor(
                    lat: a.lat + (b.lat - a.lat) * t, lon: a.lon + (b.lon - a.lon) * t,
                    withinMeters: Self.bufferMeters)
                pieces.append((len / Double(n), prevHit && hit))
                prevHit = hit
            }
        }
        let total = pieces.reduce(0) { $0 + $1.meters }
        guard total > 0 else { return 1 }
        var unwalked = 0.0
        var i = 0
        while i < pieces.count {
            guard !pieces[i].walked else { i += 1; continue }
            var j = i
            var run = 0.0
            while j < pieces.count, !pieces[j].walked { run += pieces[j].meters; j += 1 }
            // Walked on both sides and short: a dropout, not unwalked ground.
            let bounded = i > 0 && j < pieces.count
            if !(bounded && run < Self.gapFillMeters) { unwalked += run }
            i = j
        }
        return min(1, unwalked / total)
    }
}

extension TrailNetwork {
    /// New metres per edge (indexed by edge id): 0 for bridges, connectors and
    /// edges whose every trail is complete, else the edge length times the
    /// share no saved track walked. Uses `completion.edgeNewMeters` when it was
    /// resolved for this network.
    func newMetersByEdge(_ completion: TrailCompletionLookup) -> [Double] {
        if let cached = completion.edgeNewMeters, cached.count == edges.count { return cached }
        var done = Set<TrailKey>()
        for k in trailKeys where completion.isComplete(k) { done.insert(k) }
        return edges.map { e in
            guard e.kind == .trail, e.trailKeys.contains(where: { !done.contains($0) }) else { return 0 }
            if completion.walked.isEmpty { return e.lengthMeters }
            return e.lengthMeters * completion.walked.unwalkedFraction(of: e.coordinates)
        }
    }
}
