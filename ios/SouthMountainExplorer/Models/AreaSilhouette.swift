import Foundation

// Compact card-art geometry produced by scripts/build-trail-counts.py.
// JSON shape per area:
//   { "b": [w, s, e, n], "l": [{ "d": "e|m|h", "p": [[lat, lon], ...] }] }
struct AreaSilhouette: Codable, Sendable {
    let b: [Double]
    let l: [SilhouetteLine]

    var bbox: (w: Double, s: Double, e: Double, n: Double)? {
        guard b.count == 4 else { return nil }
        return (b[0], b[1], b[2], b[3])
    }
}

struct SilhouetteLine: Codable, Sendable {
    let d: String
    let p: [[Double]]
}
