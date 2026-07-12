import Foundation

// Compact card-art geometry produced by scripts/silhouettes-from-geom.py
// (derived from the published trailforge geom). JSON shape per area:
//   { "b": [w, s, e, n], "l": [{ "d": "e|m|h", "p": [[lat, lon], ...] }] }
// Equatable so AreaSilhouetteService can tell a revalidated fetch apart
// from the on-disk copy and only re-render when the art actually changed.
struct AreaSilhouette: Codable, Sendable, Equatable {
    let b: [Double]
    let l: [SilhouetteLine]

    var bbox: (w: Double, s: Double, e: Double, n: Double)? {
        guard b.count == 4 else { return nil }
        return (b[0], b[1], b[2], b[3])
    }
}

struct SilhouetteLine: Codable, Sendable, Equatable {
    let d: String
    let p: [[Double]]
}
