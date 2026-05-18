import Foundation
import CoreLocation

/// Where on a polyline a point projects, and how that relates to
/// the polyline's start. Returned by `PolylineMath.project` and
/// consumed by `TrailETA` for the "remaining distance to end"
/// calculation.
struct PolylineProjection: Equatable {
    /// Projected coordinate on the polyline.
    let projected: CLLocationCoordinate2D
    /// Distance in meters from the polyline's first vertex along
    /// the polyline to `projected`.
    let arcLengthFromStart: Double
    /// Perpendicular distance in meters from the input point to
    /// `projected`. Use this to gate "is the user even on the
    /// trail" — if it's > some threshold (e.g. 50 m) the
    /// projection is meaningless for ETA purposes.
    let perpendicularDistance: Double
    /// Index of the polyline segment (between vertices `i` and
    /// `i+1`) the projection landed on.
    let segmentIndex: Int
}

/// Pure flat-earth math over polylines: projection, arc length,
/// loop detection. All meters-scale; uses the same equirectangular
/// approximation `MapMath` does (1° lat ≈ 111 km, 1° lon scaled by
/// cos(lat)) which is plenty accurate for trail-sized geometry
/// (single area, < ~50 km extent). Pure functions so unit tests
/// exercise them without booting any SwiftUI / MapKit context.
enum PolylineMath {
    /// Total length in meters of the polyline described by
    /// `coords`. Returns 0 for fewer than 2 points.
    static func arcLength(_ coords: [CLLocationCoordinate2D]) -> Double {
        guard coords.count >= 2 else { return 0 }
        var total = 0.0
        for i in 1..<coords.count {
            total += MapMath.haversineMeters(
                lat1: coords[i - 1].latitude, lon1: coords[i - 1].longitude,
                lat2: coords[i].latitude,     lon2: coords[i].longitude
            )
        }
        return total
    }

    /// Project `point` onto the polyline, returning the closest
    /// point + its arc-length-from-start + perpendicular distance.
    /// `nil` for polylines with fewer than 2 vertices.
    ///
    /// Algorithm: walk the polyline a segment at a time, compute
    /// each segment's perpendicular foot (clamped to endpoints),
    /// keep the segment whose perpendicular foot is closest. Track
    /// running arc length so the answer is properly anchored to
    /// the polyline's start.
    static func project(_ point: CLLocationCoordinate2D,
                        onto coords: [CLLocationCoordinate2D]) -> PolylineProjection? {
        guard coords.count >= 2 else { return nil }

        // Equirectangular projection centered on the input point —
        // distances within ~50 km of it stay accurate to <0.1%.
        let lat0 = point.latitude * .pi / 180
        let metersPerLat = 111_000.0
        let metersPerLon = 111_000.0 * cos(lat0)

        func toMeters(_ c: CLLocationCoordinate2D) -> (x: Double, y: Double) {
            let dx = (c.longitude - point.longitude) * metersPerLon
            let dy = (c.latitude  - point.latitude)  * metersPerLat
            return (dx, dy)
        }
        // Inverse: meters → coord, anchored on `point`.
        func toCoord(_ x: Double, _ y: Double) -> CLLocationCoordinate2D {
            CLLocationCoordinate2D(
                latitude: point.latitude + y / metersPerLat,
                longitude: point.longitude + x / metersPerLon
            )
        }

        var best: (segmentIndex: Int, t: Double, distSq: Double)? = nil
        var bestProjected: (x: Double, y: Double) = (0, 0)
        for i in 0..<(coords.count - 1) {
            let a = toMeters(coords[i])
            let b = toMeters(coords[i + 1])
            let dx = b.x - a.x
            let dy = b.y - a.y
            let lengthSq = dx * dx + dy * dy
            let t: Double
            if lengthSq == 0 {
                t = 0
            } else {
                let raw = ((0 - a.x) * dx + (0 - a.y) * dy) / lengthSq
                t = max(0, min(1, raw))
            }
            let qx = a.x + t * dx
            let qy = a.y + t * dy
            let distSq = qx * qx + qy * qy
            if best == nil || distSq < best!.distSq {
                best = (i, t, distSq)
                bestProjected = (qx, qy)
            }
        }
        guard let pick = best else { return nil }

        // Sum prior segment lengths to get arc length to the start
        // of the picked segment, then add the partial along it.
        var arc = 0.0
        for i in 0..<pick.segmentIndex {
            arc += MapMath.haversineMeters(
                lat1: coords[i].latitude, lon1: coords[i].longitude,
                lat2: coords[i + 1].latitude, lon2: coords[i + 1].longitude
            )
        }
        let segLen = MapMath.haversineMeters(
            lat1: coords[pick.segmentIndex].latitude,
            lon1: coords[pick.segmentIndex].longitude,
            lat2: coords[pick.segmentIndex + 1].latitude,
            lon2: coords[pick.segmentIndex + 1].longitude
        )
        arc += pick.t * segLen

        return PolylineProjection(
            projected: toCoord(bestProjected.x, bestProjected.y),
            arcLengthFromStart: arc,
            perpendicularDistance: pick.distSq.squareRoot(),
            segmentIndex: pick.segmentIndex
        )
    }

    /// Whether the polyline is a closed loop — first and last
    /// vertex within `closingThresholdMeters`. Loop trails have no
    /// natural "end" for ETA purposes, so the recording panel
    /// shows "—" for them rather than a meaningless number.
    ///
    /// Default threshold dropped from 50 m → 10 m after the build
    /// 12 device test surfaced an out-and-back trail whose first
    /// and last vertices happened to be ~30 m apart (typical for
    /// open OSM ways at trailhead-to-summit-and-back). The 50 m
    /// threshold classified it as a loop, `TrailETA.compute`
    /// short-circuited to nil, and the ETA pill never appeared.
    /// 10 m is tight enough that only genuinely closed polylines
    /// (where the OSM way closes on itself, vertex coincidence
    /// well under 1 m) qualify.
    static func isLoop(_ coords: [CLLocationCoordinate2D],
                       closingThresholdMeters: Double = 10) -> Bool {
        guard let first = coords.first, let last = coords.last, coords.count >= 3 else { return false }
        let d = MapMath.haversineMeters(
            lat1: first.latitude, lon1: first.longitude,
            lat2: last.latitude,  lon2: last.longitude
        )
        return d <= closingThresholdMeters
    }

    /// Concatenate a multi-segment trail into one virtual polyline
    /// for projection / arc-length purposes. Most trails have one
    /// segment; multi-segment trails are usually two or three
    /// connected pieces, so concatenation gives sensible numbers
    /// even when the segments don't perfectly meet at endpoints.
    static func flatten(_ segments: [[CLLocationCoordinate2D]]) -> [CLLocationCoordinate2D] {
        var out: [CLLocationCoordinate2D] = []
        for seg in segments {
            out.append(contentsOf: seg)
        }
        return out
    }

    /// Distance in meters from `currentPoint`'s projection onto
    /// `coords` to the next significant turn — the first vertex
    /// ahead of the user (in their travel direction) whose heading
    /// change from the previous segment exceeds
    /// `turnThresholdDegrees`. Returns `nil` when:
    ///   - the polyline is too short to host a turn,
    ///   - the user's projection is too far off-trail (more than
    ///     `maxPerpendicularMeters`) to call them "on" it,
    ///   - direction-of-travel can't be inferred (no prior point
    ///     supplied, or projection didn't move enough between the
    ///     two samples), or
    ///   - no turn is found ahead.
    ///
    /// Used by `ActiveRecordingBanner` for the "→ X ft to next
    /// turn" line during trail-mode recordings. Strictly pure
    /// flat-earth math — no MapKit dependency — so it lives next
    /// to `project`.
    static func distanceToNextTurn(
        currentPoint: CLLocationCoordinate2D,
        priorPoint: CLLocationCoordinate2D?,
        coords: [CLLocationCoordinate2D],
        turnThresholdDegrees: Double = 25,
        maxPerpendicularMeters: Double = 50
    ) -> Double? {
        guard coords.count >= 3 else { return nil }
        guard let prior = priorPoint else { return nil }
        guard let currentProj = project(currentPoint, onto: coords) else { return nil }
        guard currentProj.perpendicularDistance <= maxPerpendicularMeters else { return nil }
        guard let priorProj = project(prior, onto: coords) else { return nil }

        // Direction inference: positive delta = moving toward end
        // (vertex index increasing); negative = moving toward start.
        // Require at least 3 m of arc-length movement so a stationary
        // user with GPS jitter doesn't flicker between directions.
        let arcDelta = currentProj.arcLengthFromStart - priorProj.arcLengthFromStart
        guard abs(arcDelta) >= 3 else { return nil }
        let forward = arcDelta > 0

        // Walk vertices in the direction of travel, starting from
        // the vertex AHEAD of the current projection's segment.
        // Compute the bearing of the segment landing on each vertex
        // and the segment leaving it; their difference (mod 360) is
        // the turn angle. First one over the threshold wins.
        if forward {
            for v in (currentProj.segmentIndex + 1)..<(coords.count - 1) {
                let inBearing = bearingDegrees(from: coords[v - 1], to: coords[v])
                let outBearing = bearingDegrees(from: coords[v], to: coords[v + 1])
                if turnDelta(inBearing, outBearing) >= turnThresholdDegrees {
                    let dist = arcLengthBetween(coords, fromVertex: currentProj.segmentIndex,
                                                fromArc: currentProj.arcLengthFromStart,
                                                toVertex: v)
                    return dist
                }
            }
        } else {
            // Walking toward the start; "ahead" means decreasing index.
            // The user is on segment `segmentIndex` (between vertices
            // `segmentIndex` and `segmentIndex + 1`); their next
            // forward vertex is `segmentIndex`, then `segmentIndex - 1`, etc.
            var v = currentProj.segmentIndex
            while v >= 1 {
                let inBearing = bearingDegrees(from: coords[v + 1], to: coords[v])
                let outBearing = bearingDegrees(from: coords[v], to: coords[v - 1])
                if turnDelta(inBearing, outBearing) >= turnThresholdDegrees {
                    // arc(start → v) is smaller than the user's
                    // current arc-from-start (they're walking
                    // backward); the gap between the two is the
                    // remaining distance to the turn.
                    let arcAtVertex = arcFromStart(coords, toVertex: v)
                    return currentProj.arcLengthFromStart - arcAtVertex
                }
                v -= 1
            }
        }
        return nil
    }

    /// Initial bearing in degrees [0, 360) from `a` to `b`. Standard
    /// great-circle formula — accurate at the latitudes hiking
    /// trails care about, doesn't need any flat-earth approximation.
    private static func bearingDegrees(from a: CLLocationCoordinate2D,
                                       to b: CLLocationCoordinate2D) -> Double {
        let lat1 = a.latitude * .pi / 180
        let lat2 = b.latitude * .pi / 180
        let dLon = (b.longitude - a.longitude) * .pi / 180
        let y = sin(dLon) * cos(lat2)
        let x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLon)
        var deg = atan2(y, x) * 180 / .pi
        if deg < 0 { deg += 360 }
        return deg
    }

    /// Absolute angular difference between two bearings, folded into
    /// [0, 180]. A 350° → 10° change reads as 20° here, not 340°.
    private static func turnDelta(_ a: Double, _ b: Double) -> Double {
        let d = abs(a - b).truncatingRemainder(dividingBy: 360)
        return min(d, 360 - d)
    }

    /// Arc length from a starting point inside segment `fromVertex`
    /// (at fractional arc `fromArc` from the polyline start) to the
    /// vertex `toVertex`, walking forward (`toVertex > fromVertex`).
    private static func arcLengthBetween(_ coords: [CLLocationCoordinate2D],
                                         fromVertex: Int,
                                         fromArc: Double,
                                         toVertex: Int) -> Double {
        var arc = 0.0
        for i in 0..<toVertex {
            arc += MapMath.haversineMeters(
                lat1: coords[i].latitude, lon1: coords[i].longitude,
                lat2: coords[i + 1].latitude, lon2: coords[i + 1].longitude
            )
        }
        return arc - fromArc
    }

    /// Arc length from polyline start to `vertex`. Used by the
    /// reverse-direction walk so we can convert a vertex index to
    /// arc-length-from-start.
    private static func arcFromStart(_ coords: [CLLocationCoordinate2D],
                                     toVertex vertex: Int) -> Double {
        var arc = 0.0
        for i in 0..<vertex {
            arc += MapMath.haversineMeters(
                lat1: coords[i].latitude, lon1: coords[i].longitude,
                lat2: coords[i + 1].latitude, lon2: coords[i + 1].longitude
            )
        }
        return arc
    }
}

extension Trail {
    /// Trail geometry as a single concatenated polyline of
    /// `CLLocationCoordinate2D`. Built fresh each call — cheap
    /// (O(node count)) and sized for the active trail only, not
    /// the whole area.
    var flattenedCoords: [CLLocationCoordinate2D] {
        var out: [CLLocationCoordinate2D] = []
        for seg in segments {
            for p in seg where p.count >= 2 {
                out.append(CLLocationCoordinate2D(latitude: p[0], longitude: p[1]))
            }
        }
        return out
    }
}
