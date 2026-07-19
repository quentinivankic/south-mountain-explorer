import Foundation

/// Position-anchored elevation profile math for a PLANNED trail — the
/// counterpart to `ElevationStats`, which summarises a hike you already
/// recorded from GPS altitude.
///
/// The data side (`trailforge/serve/elevation.py`) bakes `Trail.profileFt`:
/// elevations in feet, evenly spaced BY DISTANCE along the trail. Even spacing
/// is what lets us map a position to an index with plain arithmetic instead of
/// shipping a parallel distance array in every geom file.
///
/// **Direction is deliberately meaningless.** OSM way order is arbitrary —
/// Humphreys Summit Trail is stored summit→trailhead — so index 0 is NOT the
/// trailhead and must never be drawn as one. Instead we anchor the chart on
/// where the hiker actually is (`snap`) and orient it by which way they're
/// walking (`travellingForward`), which sidesteps "which end is the start"
/// entirely. That question is why the reverse-profile idea stalled.
enum TrailProfile {

    // MARK: - Geometry

    struct Point: Equatable, Sendable {
        let lat: Double
        let lon: Double
    }

    /// Flatten a trail's segments into ONE polyline, in stored order.
    /// Multi-segment trails are treated as contiguous: the profile is sampled
    /// the same way on the pipeline side, so both agree on what "along" means.
    static func polyline(_ segments: [[[Double]]]) -> [Point] {
        segments.flatMap { seg in
            seg.compactMap { p in p.count >= 2 ? Point(lat: p[0], lon: p[1]) : nil }
        }
    }

    /// Metres between two coordinates (haversine).
    static func meters(_ a: Point, _ b: Point) -> Double {
        let r = 6_371_000.0
        let dLat = (b.lat - a.lat) * .pi / 180
        let dLon = (b.lon - a.lon) * .pi / 180
        let la1 = a.lat * .pi / 180
        let la2 = b.lat * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(la1) * cos(la2) * sin(dLon / 2) * sin(dLon / 2)
        return 2 * r * asin(min(1, sqrt(h)))
    }

    /// Cumulative distance in metres at each polyline vertex (first is 0).
    static func cumulativeMeters(_ pts: [Point]) -> [Double] {
        guard !pts.isEmpty else { return [] }
        var out = [0.0]
        out.reserveCapacity(pts.count)
        var run = 0.0
        for (a, b) in zip(pts, pts.dropFirst()) {
            run += meters(a, b)
            out.append(run)
        }
        return out
    }

    // MARK: - Snapping

    struct Snap: Equatable, Sendable {
        /// Where the hiker is along the STORED polyline, 0...1.
        let fraction: Double
        /// How far off the trail they are, in metres. The caller decides what
        /// is "on trail" — `RecordingService` uses a 10 m buffer for coverage.
        let offTrailMeters: Double
    }

    /// Snap a coordinate onto the trail, returning how far along it sits.
    ///
    /// Projects onto each polyline SEGMENT rather than snapping to the nearest
    /// vertex: on a trail densified to ~30 m, vertex-snapping would quantise
    /// the marker into visible 30 m hops as you walk. Returns nil for a trail
    /// with no usable geometry or zero length.
    static func snap(lat: Double, lon: Double, segments: [[[Double]]]) -> Snap? {
        let pts = polyline(segments)
        guard pts.count >= 2 else { return nil }
        let cum = cumulativeMeters(pts)
        guard let total = cum.last, total > 0 else { return nil }

        // Local equirectangular projection: at trail scale the error is far
        // below GPS noise, and it makes the point-to-segment projection plain
        // 2-D algebra instead of spherical trigonometry.
        let latRad = lat * .pi / 180
        let mPerDegLat = 111_132.0
        let mPerDegLon = 111_320.0 * cos(latRad)
        func xy(_ p: Point) -> (Double, Double) {
            ((p.lon - lon) * mPerDegLon, (p.lat - lat) * mPerDegLat)
        }

        var bestDist = Double.infinity
        var bestAlong = 0.0
        for i in 0..<(pts.count - 1) {
            let (ax, ay) = xy(pts[i])
            let (bx, by) = xy(pts[i + 1])
            let dx = bx - ax, dy = by - ay
            let lenSq = dx * dx + dy * dy
            // t = how far along THIS segment the projection falls, clamped so
            // a point beyond either end snaps to that end rather than past it.
            let t = lenSq > 0 ? max(0, min(1, -(ax * dx + ay * dy) / lenSq)) : 0
            let px = ax + dx * t, py = ay + dy * t
            let d = sqrt(px * px + py * py)
            if d < bestDist {
                bestDist = d
                bestAlong = cum[i] + (cum[i + 1] - cum[i]) * t
            }
        }
        return Snap(fraction: min(1, max(0, bestAlong / total)),
                    offTrailMeters: bestDist)
    }

    // MARK: - Reading the profile

    /// Elevation in feet at a fraction along the trail, linearly interpolated
    /// between the two bracketing samples. nil when there's no profile.
    static func elevationFt(_ profile: [Int], at fraction: Double) -> Double? {
        guard !profile.isEmpty else { return nil }
        guard profile.count > 1 else { return Double(profile[0]) }
        let f = min(1, max(0, fraction))
        let pos = f * Double(profile.count - 1)
        let i = Int(pos)
        if i >= profile.count - 1 { return Double(profile[profile.count - 1]) }
        let frac = pos - Double(i)
        return Double(profile[i]) + (Double(profile[i + 1]) - Double(profile[i])) * frac
    }

    // MARK: - Orientation

    /// Are they walking in the stored polyline's direction?
    ///
    /// Compares two successive snapped fractions. Equal fractions (standing
    /// still, or GPS jitter under the resolution of the trail) keep the last
    /// known orientation rather than flipping the chart — a marker that
    /// mirrors every time you pause would be unreadable.
    static func travellingForward(previous: Double, current: Double,
                                  lastKnown: Bool = true) -> Bool {
        if current > previous { return true }
        if current < previous { return false }
        return lastKnown
    }

    /// The profile oriented so the hiker's direction of travel reads LEFT →
    /// RIGHT, with their own position mapped into the same frame.
    ///
    /// When they're walking against the stored order we reverse the samples,
    /// so "ahead of me" is always to the right of the marker no matter how OSM
    /// happened to store the way.
    static func oriented(_ profile: [Int], fraction: Double,
                         travellingForward: Bool) -> (samples: [Int], fraction: Double) {
        travellingForward
            ? (profile, min(1, max(0, fraction)))
            : (profile.reversed(), 1 - min(1, max(0, fraction)))
    }
}
