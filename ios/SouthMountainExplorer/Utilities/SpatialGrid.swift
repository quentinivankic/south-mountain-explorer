import Foundation

/// Spatial hash grid for O(1) per-point neighbor lookups against a
/// collection of 2D coordinates. Buckets points by a fixed cell size
/// in degrees; `neighbors` returns every bucketed point in the 9 cells
/// surrounding a query, which is the candidate set you then filter by
/// actual haversine distance.
///
/// Used in two places that historically hand-rolled the same grid:
/// - `RecordingService.measureCoverage` — grid of recorded GPS points,
///   queried per trail node.
/// - `TrailMapView.onTrailSegments` — grid of trail nodes, queried per
///   recorded GPS point.
///
/// Default cell size of `0.0003` degrees (~33 m) lines up with the
/// `bufferMeters` used for coverage so a single 9-cell window always
/// contains every candidate.
struct SpatialGrid {
    let cellSize: Double
    private var buckets: [CellKey: [Point]] = [:]

    typealias Point = (lat: Double, lon: Double)

    private struct CellKey: Hashable {
        let r: Int
        let c: Int
    }

    init(cellSize: Double = 0.0003) {
        self.cellSize = cellSize
    }

    mutating func insert(lat: Double, lon: Double) {
        buckets[cellKey(lat: lat, lon: lon), default: []].append((lat, lon))
    }

    /// Convenience for the `[lat, lon, ...]` array form used in
    /// recorded paths and trail segments.
    mutating func insert(_ point: [Double]) {
        guard point.count >= 2 else { return }
        insert(lat: point[0], lon: point[1])
    }

    /// All inserted points in the 9-cell window centered on
    /// `(lat, lon)`. Candidate set — caller filters by real distance.
    func neighbors(lat: Double, lon: Double) -> [Point] {
        let r = Int((lat / cellSize).rounded())
        let c = Int((lon / cellSize).rounded())
        var out: [Point] = []
        for dr in -1...1 {
            for dc in -1...1 {
                if let pts = buckets[CellKey(r: r + dr, c: c + dc)] {
                    out += pts
                }
            }
        }
        return out
    }

    /// Is any inserted point within `meters` of `(lat, lon)`?
    func hasNeighbor(lat: Double, lon: Double, withinMeters meters: Double) -> Bool {
        for p in neighbors(lat: lat, lon: lon) {
            if haversineDistanceM(lat1: lat, lon1: lon, lat2: p.lat, lon2: p.lon) <= meters {
                return true
            }
        }
        return false
    }

    private func cellKey(lat: Double, lon: Double) -> CellKey {
        CellKey(
            r: Int((lat / cellSize).rounded()),
            c: Int((lon / cellSize).rounded())
        )
    }
}
