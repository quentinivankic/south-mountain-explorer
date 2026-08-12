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

/// The next bend in the trail ahead of you. Returned by
/// `PolylineMath.nextTurn` and shown by `ActiveRecordingBanner` while a
/// trail-mode recording is running.
struct TurnAhead: Equatable {
    /// Metres from where you project onto the trail, along the trail, to the
    /// vertex where it bends.
    let distanceMeters: Double
    let side: Side

    enum Side: Equatable {
        case left
        case right

        var word: String { self == .left ? "left" : "right" }
        /// Chevron pointing the way the trail goes.
        var systemImage: String {
            self == .left ? "arrow.turn.up.left" : "arrow.turn.up.right"
        }
    }
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

    /// The next bend in the trail ahead of you, and which way it goes.
    ///
    /// Direction of travel is inferred from how far along the trail you've
    /// moved between two GPS samples, not from device heading — heading swings
    /// wildly at walking pace, while arc-length along a projected polyline is
    /// stable. Everything ahead is then read in that direction, so the answer
    /// is the same whether the OSM way happens to run your way or against you.
    ///
    /// `nil` whenever the question can't be answered honestly: too few
    /// vertices to hold a bend, no prior sample, you're too far off the trail
    /// to project meaningfully, you haven't moved enough to say which way
    /// you're facing, or there's simply no bend left ahead of you.
    static func nextTurn(
        from currentPoint: CLLocationCoordinate2D,
        priorPoint: CLLocationCoordinate2D?,
        along coords: [CLLocationCoordinate2D],
        turnThresholdDegrees: Double = 25,
        maxPerpendicularMeters: Double = 50,
        minTravelMeters: Double = 3
    ) -> TurnAhead? {
        guard coords.count >= 3, let prior = priorPoint else { return nil }
        guard let here = project(currentPoint, onto: coords),
              here.perpendicularDistance <= maxPerpendicularMeters,
              let before = project(prior, onto: coords)
        else { return nil }

        // Positive delta = walking toward the polyline's end. The floor keeps
        // a standing hiker's GPS jitter from flipping the inferred direction
        // back and forth, which would swap "left" and "right" while they
        // stood still.
        let travelled = here.arcLengthFromStart - before.arcLengthFromStart
        guard abs(travelled) >= minTravelMeters else { return nil }

        if travelled > 0 {
            // Vertices ahead are the ones after the segment you're on. A bend
            // AT vertex v is the angle between the segment arriving at v and
            // the segment leaving it.
            var arc = arcLength(Array(coords[0...(here.segmentIndex + 1)]))
            for v in (here.segmentIndex + 1)..<(coords.count - 1) {
                if v > here.segmentIndex + 1 {
                    arc += MapMath.haversineMeters(
                        lat1: coords[v - 1].latitude, lon1: coords[v - 1].longitude,
                        lat2: coords[v].latitude, lon2: coords[v].longitude
                    )
                }
                let turn = signedTurn(from: bearingDegrees(from: coords[v - 1], to: coords[v]),
                                      to: bearingDegrees(from: coords[v], to: coords[v + 1]))
                if abs(turn) >= turnThresholdDegrees {
                    return TurnAhead(distanceMeters: max(0, arc - here.arcLengthFromStart),
                                     side: turn > 0 ? .right : .left)
                }
            }
        } else {
            // Walking back toward the start, so "ahead" counts down. You're on
            // the segment between `segmentIndex` and `segmentIndex + 1`, which
            // makes `segmentIndex` the next vertex you reach.
            var v = here.segmentIndex
            while v >= 1 {
                let turn = signedTurn(from: bearingDegrees(from: coords[v + 1], to: coords[v]),
                                      to: bearingDegrees(from: coords[v], to: coords[v - 1]))
                if abs(turn) >= turnThresholdDegrees {
                    let arcAtVertex = arcLength(Array(coords[0...v]))
                    return TurnAhead(distanceMeters: max(0, here.arcLengthFromStart - arcAtVertex),
                                     side: turn > 0 ? .right : .left)
                }
                v -= 1
            }
        }
        return nil
    }

    /// Initial bearing in degrees, 0 ..< 360, from `a` to `b`. The
    /// great-circle formula rather than the flat-earth one used above: an
    /// angle is what this whole feature turns on, and it costs two trig calls.
    static func bearingDegrees(from a: CLLocationCoordinate2D,
                               to b: CLLocationCoordinate2D) -> Double {
        let lat1 = a.latitude * .pi / 180
        let lat2 = b.latitude * .pi / 180
        let dLon = (b.longitude - a.longitude) * .pi / 180
        let y = sin(dLon) * cos(lat2)
        let x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLon)
        let deg = atan2(y, x) * 180 / .pi
        return deg < 0 ? deg + 360 : deg
    }

    /// How far, and which way, a heading turns. Result is in -180 ..< 180:
    /// positive is a right turn, negative a left one. Folding matters —
    /// 350° to 10° is a 20° right turn, not a 340° left one.
    static func signedTurn(from inBearing: Double, to outBearing: Double) -> Double {
        var d = (outBearing - inBearing).truncatingRemainder(dividingBy: 360)
        if d > 180 { d -= 360 }
        if d < -180 { d += 360 }
        return d
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
