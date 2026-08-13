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
/// trailhead and must never be drawn as one. We supply direction from the USER
/// instead: `snap` puts a marker where they are, and `startIsNearer` puts the
/// trail end nearest them on the left. That question — which end is the start —
/// is why the reverse-profile idea stalled for so long.
///
/// Rejected alternatives and the evidence against each are recorded in
/// CLAUDE.md ("Trail elevation profiles — the direction problem"). The short
/// version: network connectivity answers only ~33% of trails (measured),
/// parking covers ~1% of areas today, and low-end-left is wrong for every
/// canyon descent.
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

    /// Is the trail's stored START the end nearer this coordinate?
    ///
    /// This is the ONLY orientation rule: put the end nearest the user on the
    /// LEFT, because that's the end they'd set off from. It always has an
    /// answer — no distance cutoff, no parking, no fallback chain — and it
    /// sharpens as they approach: arbitrary-ish from home, right while driving
    /// in, exact at the trailhead.
    ///
    /// Compares the two ENDPOINTS rather than the snapped fraction. Snapping
    /// would return ~0.5 for anyone standing off the middle of the trail, which
    /// is precisely where the answer needs to be most stable.
    ///
    /// Returns nil when the trail has no usable geometry — the caller then
    /// draws the bare shape rather than inventing a direction.
    static func startIsNearer(lat: Double, lon: Double,
                              segments: [[[Double]]]) -> Bool? {
        let pts = polyline(segments)
        guard let first = pts.first, let last = pts.last, pts.count >= 2 else { return nil }
        let me = Point(lat: lat, lon: lon)
        return meters(me, first) <= meters(me, last)
    }

    /// Which end of the trail the nearest PARKING is at, or nil when parking
    /// cannot answer it.
    ///
    /// This outranks `startIsNearer`. Where you are standing is a proxy for
    /// where you will set off from; the trailhead car park IS where you will set
    /// off from. Browsing from home the proxy is close to a coin flip, and it
    /// gets the answer wrong on exactly the trails people look up in advance.
    /// Mormon Trail in South Mountain is the reported case: its northwest end is
    /// 32 m from a lot and its southeast end is 1,588 m from that same lot, yet
    /// the chart opened from the southeast because that end happened to be
    /// nearer the user's sofa.
    ///
    /// **Only answers when parking is decisive.** The lot has to be within
    /// `withinMeters` of an endpoint, and the far endpoint has to be at least
    /// `decisiveRatio` times further than the near one. Measured over a random
    /// 500-area sample of shipped geom: 47% of trails in parking-carrying areas
    /// have a lot inside the app's own 805 m endpoint radius, the median nearest
    /// lot is 127 m away, and the far/near ratio has a median of 5.07 — usually
    /// emphatic. But 28.4% come in under 2x, which is a lot sitting mid-trail or
    /// a loop whose two ends coincide. Those get no answer here and fall back to
    /// the user's position, which is the honest outcome rather than a coin flip
    /// dressed up as data.
    ///
    /// 805 m is not a new number: it is the endpoint radius
    /// `Area.nearestParking` already uses to decide a lot belongs to a trail.
    static func startIsNearerParking(segments: [[[Double]]],
                                     lots: [ParkingLot],
                                     withinMeters: Double = 805,
                                     decisiveRatio: Double = 2) -> Bool? {
        let pts = polyline(segments)
        guard let first = pts.first, let last = pts.last, pts.count >= 2 else { return nil }

        var bestNear = Double.greatestFiniteMagnitude
        var bestStart = 0.0
        var bestEnd = 0.0
        for lot in lots {
            let p = Point(lat: lot.lat, lon: lot.lon)
            let ds = meters(p, first)
            let de = meters(p, last)
            let near = min(ds, de)
            if near < bestNear {
                bestNear = near
                bestStart = ds
                bestEnd = de
            }
        }
        guard bestNear <= withinMeters else { return nil }

        let near = max(min(bestStart, bestEnd), 1)
        let far = max(bestStart, bestEnd)
        guard far / near >= decisiveRatio else { return nil }
        return bestStart < bestEnd
    }

    /// Compass label for the trail END the chart starts from, e.g. "west end".
    ///
    /// Replaces the earlier "nearest end" wording, which described the ALGORITHM
    /// rather than answering the question. "Nearest" is ambiguous (nearest to
    /// what?), never says which physical end, and is weakest exactly when you're
    /// browsing from far away — the case where you most need to know.
    ///
    /// A compass bearing is unambiguous wherever you're standing and needs no
    /// data beyond the two endpoints we already have. Bearing runs FROM the
    /// opposite end TO the starting end, so the label names where the start
    /// lies: a trail drawn from its western end reads "west end".
    ///
    /// Returns nil when the ends are too close to have a meaningful direction —
    /// a loop's endpoints coincide, and "north end" would be a lie there.
    static func startEndCompassLabel(segments: [[[Double]]],
                                     startIsNearer: Bool) -> String? {
        let pts = polyline(segments)
        guard let first = pts.first, let last = pts.last, pts.count >= 2 else { return nil }
        let startPt = startIsNearer ? first : last
        let otherPt = startIsNearer ? last : first
        // Loops and near-loops have no distinguishable ends. 150 m is comfortably
        // past GPS-scale noise while still admitting genuinely short trails.
        guard meters(startPt, otherPt) >= 150 else { return nil }

        let dLat = startPt.lat - otherPt.lat
        // Longitude degrees shrink toward the poles; scale so the bearing is
        // geometric rather than skewed by latitude.
        let dLon = (startPt.lon - otherPt.lon) * cos(otherPt.lat * .pi / 180)
        var deg = atan2(dLon, dLat) * 180 / .pi     // 0 = north, clockwise
        if deg < 0 { deg += 360 }

        let names = ["north", "northeast", "east", "southeast",
                     "south", "southwest", "west", "northwest"]
        let idx = Int(((deg + 22.5) / 45).rounded(.down)) % 8
        return "\(names[idx]) end"
    }

    /// The profile oriented so the user's end of the trail reads LEFT → RIGHT,
    /// with their position mapped into the same frame.
    ///
    /// `startIsNearer == false` means the stored order runs toward them, so
    /// both the samples and the marker flip together.
    ///
    /// NOTE: orientation is deliberately NOT recomputed as you walk. Latching
    /// it (see `TrailRow.profileStartIsNearer`) is what keeps the chart still:
    /// recomputing would flip it at the trail's midpoint, when the far end
    /// becomes the nearer one — mid-hike, for every point-to-point trail.
    static func oriented(_ profile: [Int], fraction: Double,
                         startIsNearer: Bool) -> (samples: [Int], fraction: Double) {
        startIsNearer
            ? (profile, min(1, max(0, fraction)))
            : (profile.reversed(), 1 - min(1, max(0, fraction)))
    }
}
