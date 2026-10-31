import Foundation
import CoreLocation

/// A selected trail prepared for estimating: local-meter vertices, cumulative
/// arc length on the same basis as the elevation chart (`TrailProfile`), loop
/// flag and an optional DEM elevation profile. Pure value type.
struct TrailGeometry: Equatable, Sendable {
    static let metersPerDegree = 111_194.9    // matches haversine R = 6,371,000 m

    let id: String
    let name: String
    let lat0: Double
    let lon0: Double
    let xs: [Double]
    let ys: [Double]
    /// Cumulative meters at each vertex (stored order, first is 0).
    let cum: [Double]
    let total: Double
    let isLoop: Bool
    /// Elevation samples in meters, evenly spaced by arc length (stored order).
    let profileMeters: [Double]?

    init?(trail: Trail) {
        let pts = TrailProfile.polyline(trail.segments)
        guard pts.count >= 2 else { return nil }
        let cum = TrailProfile.cumulativeMeters(pts)
        guard let total = cum.last, total > 1 else { return nil }
        self.id = trail.id
        self.name = trail.name
        self.lat0 = pts[0].lat
        self.lon0 = pts[0].lon
        let cosLat = cos(pts[0].lat * .pi / 180)
        self.xs = pts.map { ($0.lon - pts[0].lon) * Self.metersPerDegree * cosLat }
        self.ys = pts.map { ($0.lat - pts[0].lat) * Self.metersPerDegree }
        self.cum = cum
        self.total = total
        self.isLoop = PolylineMath.isLoop(pts.map {
            CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon)
        })
        if let ft = trail.profileFt, ft.count >= 2 {
            self.profileMeters = ft.map { Double($0) * 0.3048 }
        } else {
            self.profileMeters = nil
        }
    }

    func xy(lat: Double, lon: Double) -> (x: Double, y: Double) {
        ((lon - lon0) * Self.metersPerDegree * cos(lat0 * .pi / 180),
         (lat - lat0) * Self.metersPerDegree)
    }

    /// Elevation in meters at arc position `s` (wrapped for loops), nil with no profile.
    func elevation(at s: Double) -> Double? {
        guard let p = profileMeters else { return nil }
        var pos = s
        if isLoop { pos = pos.truncatingRemainder(dividingBy: total); if pos < 0 { pos += total } }
        let f = min(1, max(0, pos / total))
        let idx = f * Double(p.count - 1)
        let i = min(p.count - 2, Int(idx))
        return p[i] + (p[i + 1] - p[i]) * (idx - Double(i))
    }

    /// Wrap an arc delta into (-total/2, total/2] for loops; untouched otherwise.
    func wrapped(_ delta: Double) -> Double {
        guard isLoop else { return delta }
        var d = delta.truncatingRemainder(dividingBy: total)
        if d > total / 2 { d -= total } else if d <= -total / 2 { d += total }
        return d
    }

    struct Projection: Equatable, Sendable {
        var s: Double
        var distance: Double
    }

    /// Nearest point on the trail. With `around`, only segments within `half`
    /// meters of arc of that position are considered (circular for loops).
    func project(x: Double, y: Double, around: Double? = nil, half: Double = 0) -> Projection? {
        var best: Projection? = nil
        for i in 0..<(xs.count - 1) {
            if let around {
                let lo = cum[i], hi = cum[i + 1]
                var gap = max(0, lo - around, around - hi)
                if isLoop {
                    gap = min(gap, max(0, lo - (around + total), (around + total) - hi),
                              max(0, lo - (around - total), (around - total) - hi))
                }
                if gap > half { continue }
            }
            let ax = xs[i], ay = ys[i]
            let dx = xs[i + 1] - ax, dy = ys[i + 1] - ay
            let lenSq = dx * dx + dy * dy
            let t = lenSq > 0 ? max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / lenSq)) : 0
            let ex = x - (ax + t * dx), ey = y - (ay + t * dy)
            let d = (ex * ex + ey * ey).squareRoot()
            if best == nil || d < best!.distance {
                best = Projection(s: cum[i] + t * (cum[i + 1] - cum[i]), distance: d)
            }
        }
        if var b = best, isLoop, b.s >= total { b.s -= total; best = b }
        return best
    }

    /// Seconds to cover `length` meters from `start` moving in `direction`
    /// (+1 / -1 along stored order), using the Tobler grade of the profile.
    func seconds(from start: Double, direction: Int, length: Double, factor: Double) -> Double {
        var t = 0.0, pos = start, left = max(0, length)
        let step = Double(direction >= 0 ? 1 : -1)
        while left > 0.5 {
            let chunk = min(PaceModel.chunkMeters, left)
            var next = pos + step * chunk
            if !isLoop { next = min(total, max(0, next)) }
            var grade = 0.0
            if let e0 = elevation(at: pos), let e1 = elevation(at: next) {
                grade = (e1 - e0) / chunk
            }
            t += PaceModel.seconds(length: chunk, grade: grade, factor: factor)
            pos = next
            left -= chunk
        }
        return t
    }
}

/// Monotonic, direction-aware progress along a selected trail, consumed
/// incrementally from the recorded path. Pure value type.
///
/// - Each fix is projected only near the previous position, so a trail that
///   passes close to itself cannot make the position jump to another stretch.
/// - Direction latches after 30 m of net progress (or immediately when the hike
///   began within 50 m of one end) and reverses only after 60 m of contrary
///   progress, so jitter cannot flip it.
/// - Off-trail uses hysteresis (off beyond 50 m, back on below 35 m).
struct TrailProgress: Equatable, Sendable {
    static let offTrailMeters = 50.0
    static let rejoinMeters = 35.0
    static let latchMeters = 30.0
    static let reverseMeters = 60.0
    static let endProximityMeters = 50.0

    private(set) var processed = 0
    /// Last on-trail position along the stored polyline.
    private(set) var s: Double? = nil
    private(set) var startS: Double? = nil
    /// Signed unwrapped net progress (stored-order positive) since joining.
    private(set) var net = 0.0
    private var extremum = 0.0
    /// +1 toward the stored end, -1 toward the stored start, 0 unknown.
    private(set) var direction = 0
    private(set) var isOff = false
    /// Straight-line meters to the nearest trail point while off trail.
    private(set) var rejoinDistance = 0.0
    /// True when the very first fix of the hike was on the trail.
    private(set) var startedOnTrail = false
    /// True once any fix has been seen farther than the off-trail threshold.
    private(set) var everOff = false

    mutating func ingest(path: [GpsPoint], geometry g: TrailGeometry) {
        if processed > path.count { self = TrailProgress() }
        var px: Double? = nil, py: Double? = nil
        if processed > 0, processed - 1 < path.count, path[processed - 1].count >= 2 {
            let q = g.xy(lat: path[processed - 1][0], lon: path[processed - 1][1])
            px = q.x; py = q.y
        }
        while processed < path.count {
            let p = path[processed]
            let prev: GpsPoint? = processed > 0 ? path[processed - 1] : nil
            processed += 1
            guard p.count >= 3 else { continue }
            let q = g.xy(lat: p[0], lon: p[1])
            var step = 0.0
            if let ox = px, let oy = py { step = ((q.x - ox) * (q.x - ox) + (q.y - oy) * (q.y - oy)).squareRoot() }
            px = q.x; py = q.y
            let gap = prev.map { $0.count >= 3 && GpsIngest.isGap(prev: $0, p: p) } ?? false
            apply(x: q.x, y: q.y, step: step, gap: gap, isFirst: prev == nil, g: g)
        }
    }

    private mutating func apply(x: Double, y: Double, step: Double, gap: Bool, isFirst: Bool,
                                g: TrailGeometry) {
        guard let global = g.project(x: x, y: y) else { return }
        var proj = global
        var reseed = true
        if let sPrev = s, !isOff, !gap,
           let local = g.project(x: x, y: y, around: sPrev, half: max(100, step * 2 + 60)),
           local.distance <= Self.offTrailMeters {
            proj = local
            reseed = false
        }
        if isOff {
            guard global.distance < Self.rejoinMeters else { rejoinDistance = global.distance; return }
            isOff = false
            proj = global
            reseed = true
        } else if proj.distance > Self.offTrailMeters {
            isOff = true
            everOff = true
            rejoinDistance = global.distance
            return
        }
        if isFirst { startedOnTrail = true }
        var delta = 0.0
        if let sPrev = s {
            delta = g.wrapped(proj.s - sPrev)
            if reseed {
                // After a gap / rejoin credit the move only when it agrees with the
                // latched direction and is plausible walking distance.
                if direction == 0 || Double(direction) * delta <= 0 || abs(delta) > 400 { delta = 0 }
            }
        }
        net += delta
        s = proj.s
        if startS == nil {
            startS = proj.s
            if !g.isLoop {
                if proj.s <= Self.endProximityMeters { direction = 1 }
                else if g.total - proj.s <= Self.endProximityMeters { direction = -1 }
            }
            extremum = net
        }
        if direction == 0 {
            if abs(net) >= Self.latchMeters {
                direction = net > 0 ? 1 : -1
                extremum = net
            }
        } else {
            let dir = Double(direction)
            if dir * net > dir * extremum { extremum = net }
            if dir * (extremum - net) >= Self.reverseMeters {
                direction = -direction
                extremum = net
                if g.isLoop { net = 0; extremum = 0 }
            }
        }
    }

    /// Meters left to finish the trail in the latched direction, nil while unknown.
    func remainingMeters(in g: TrailGeometry) -> Double? {
        guard let s, direction != 0 else { return nil }
        if g.isLoop { return max(0, g.total - Double(direction) * net) }
        return direction > 0 ? max(0, g.total - s) : max(0, s)
    }
}
