import Foundation

/// A point in a flat local frame, in metres east (`x`) and north (`y`) of an
/// origin. Same equirectangular approximation `PolylineMath` uses; fine for
/// the few-kilometre extent of one trail.
struct LocalPt: Sendable, Equatable {
    var x: Double
    var y: Double
}

/// Converts lat/lon to `LocalPt` around a fixed origin.
struct TurnCueProjector: Sendable, Equatable {
    static let metersPerDegree = 111_194.9266
    let originLat: Double
    let originLon: Double
    private let cosLat: Double

    init(originLat: Double, originLon: Double) {
        self.originLat = originLat
        self.originLon = originLon
        self.cosLat = cos(originLat * .pi / 180)
    }

    func local(lat: Double, lon: Double) -> LocalPt {
        LocalPt(
            x: (lon - originLon) * cosLat * Self.metersPerDegree,
            y: (lat - originLat) * Self.metersPerDegree
        )
    }
}

enum TurnCueGeometry {
    static func distance(_ a: LocalPt, _ b: LocalPt) -> Double {
        hypot(b.x - a.x, b.y - a.y)
    }

    /// Compass bearing from `a` to `b` in degrees, 0 = north, clockwise, [0, 360).
    static func bearing(from a: LocalPt, to b: LocalPt) -> Double {
        let deg = atan2(b.x - a.x, b.y - a.y) * 180 / .pi
        return normalize360(deg)
    }

    static func normalize360(_ deg: Double) -> Double {
        var d = deg.truncatingRemainder(dividingBy: 360)
        if d < 0 { d += 360 }
        return d
    }

    /// Signed angle from bearing `a` to bearing `b` in (-180, 180]. Positive
    /// means `b` is clockwise (to the right) of `a`.
    static func signedAngleDiff(from a: Double, to b: Double) -> Double {
        var d = (b - a).truncatingRemainder(dividingBy: 360)
        if d > 180 { d -= 360 }
        if d <= -180 { d += 360 }
        return d
    }

    /// Cumulative arc length at every vertex (`cum[0] == 0`).
    static func cumulative(_ pts: [LocalPt]) -> [Double] {
        var out = [Double](repeating: 0, count: pts.count)
        guard pts.count > 1 else { return out }
        for i in 1..<pts.count {
            out[i] = out[i - 1] + distance(pts[i - 1], pts[i])
        }
        return out
    }

    /// Re-samples a polyline so consecutive points are at most `spacing`
    /// metres apart. Original vertices are kept.
    static func resample(_ pts: [LocalPt], every spacing: Double) -> [LocalPt] {
        guard pts.count > 1, spacing > 0 else { return pts }
        var out: [LocalPt] = [pts[0]]
        for i in 1..<pts.count {
            let a = pts[i - 1], b = pts[i]
            let d = distance(a, b)
            if d > spacing {
                let n = Int((d / spacing).rounded(.up))
                for k in 1..<n {
                    let t = Double(k) / Double(n)
                    out.append(LocalPt(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t))
                }
            }
            out.append(b)
        }
        return out
    }

    struct Projection: Sendable, Equatable {
        var arc: Double
        var perpendicular: Double
    }

    /// Projects `p` onto segment `a`-`b`; returns the parameter in [0, 1] and
    /// the distance to the clamped point.
    static func projectOnSegment(_ p: LocalPt, _ a: LocalPt, _ b: LocalPt) -> (t: Double, dist: Double) {
        let dx = b.x - a.x, dy = b.y - a.y
        let len2 = dx * dx + dy * dy
        guard len2 > 1e-9 else { return (0, distance(p, a)) }
        let t = max(0, min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2))
        let q = LocalPt(x: a.x + dx * t, y: a.y + dy * t)
        return (t, distance(p, q))
    }
}
