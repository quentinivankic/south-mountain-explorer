import Foundation
import CoreLocation

/// Computes a "time to end of trail" estimate for the recording
/// panel, given the user's current location, the active trail's
/// geometry, and a smoothed pace from the recording's recent path.
///
/// Returns `nil` (which the panel renders as "—") in cases where a
/// number would be misleading:
///
/// - **Loop trail** — no natural "end" from the user's projected
///   position; the closest end is whichever direction they're
///   heading, which we don't reliably know yet.
/// - **User off-trail** — projection >50 m from the trail means
///   they're not actually on it, so distance-along-trail is
///   meaningless.
/// - **Insufficient pace data** — fewer than ~5 path samples or
///   <30 s of recording. Pace estimates from tiny samples bounce
///   wildly; better to show nothing than a wrong number.
/// - **Trail with <2 vertices** — no polyline to project against.
///
/// Direction of travel is INFERRED, not assumed. v1 always measured toward the
/// trail's last vertex, so on any trail whose OSM way runs against you the
/// number shrank and then grew as you "doubled back" — and OSM way order is
/// arbitrary, so that was a coin flip on every trail. Two consecutive fixes
/// projected onto the polyline say which way you are actually going.
enum TrailETA {

    /// Maximum perpendicular distance (meters) from the trail
    /// before we stop computing an ETA. 50 m is generous enough to
    /// cover GPS scatter on densely-tree-covered trails but tight
    /// enough that someone on a clearly-different path doesn't get
    /// a stale ETA.
    static let onTrailThresholdMeters: Double = 50

    /// Compute ETA in seconds. `nil` for any of the
    /// "show a dash" cases above.
    /// Seconds to the END OF THE TRAIL you are on, in the direction you are
    /// walking. `priorLocation` is the previous recorded fix; without one this
    /// falls back to measuring toward the polyline's last vertex.
    static func compute(
        currentLocation: CLLocationCoordinate2D,
        priorLocation: CLLocationCoordinate2D? = nil,
        trail: Trail,
        paceMetersPerSec: Double?
    ) -> TimeInterval? {
        guard let pace = paceMetersPerSec, pace > 0.1 else { return nil }
        let coords = trail.flattenedCoords
        guard coords.count >= 2 else { return nil }
        if PolylineMath.isLoop(coords) { return nil }
        guard let projection = PolylineMath.project(currentLocation, onto: coords) else {
            return nil
        }
        guard projection.perpendicularDistance <= onTrailThresholdMeters else {
            return nil
        }
        let total = PolylineMath.arcLength(coords)

        // Which end are you heading for? Movement ALONG the polyline between two
        // fixes answers it; device heading does not, because heading swings
        // wildly at walking pace. The 3 m floor is there so a hiker standing
        // still with GPS jitter doesn't flip the answer back and forth.
        var towardEnd = true
        if let priorLocation,
           let before = PolylineMath.project(priorLocation, onto: coords) {
            let travelled = projection.arcLengthFromStart - before.arcLengthFromStart
            if abs(travelled) >= 3 { towardEnd = travelled > 0 }
        }

        let remaining = towardEnd
            ? max(0, total - projection.arcLengthFromStart)
            : max(0, projection.arcLengthFromStart)
        return remaining / pace
    }

    /// Seconds to get back to where the hike STARTED, by retracing the route you
    /// walked. `walkedMeters` is the recording's own distance so far.
    ///
    /// Retracing is the only route home we can measure. Anything shorter needs
    /// routing over a network we do not have, and a straight line is worse than
    /// useless in terrain — it happily points through a ridge. So this is an
    /// upper bound, and it answers the question people actually ask, which is
    /// "if I turn around now, when am I back at the car".
    ///
    /// It is exactly right for an out-and-back and pessimistic on a loop, where
    /// carrying on would be shorter. Measured over a random 400-area sample of
    /// shipped geom using the same 10 m closing test `PolylineMath.isLoop` uses:
    /// 57 of 4,822 trails are loops, 1.2%. The pessimistic case is rare, and
    /// being early back at the car is the safe direction to be wrong in.
    ///
    /// Unlike `compute` this needs no trail at all, so it works during a roam
    /// recording, which has never had an estimate of any kind.
    static func returnToStart(walkedMeters: Double,
                              paceMetersPerSec: Double?) -> TimeInterval? {
        guard let pace = paceMetersPerSec, pace > 0.1 else { return nil }
        guard walkedMeters > 0 else { return nil }
        return walkedMeters / pace
    }

    /// Format an ETA as a short human label — `"<1 min"`,
    /// `"12 min"`, `"1h 5m"`. Used by the recording panel so the
    /// formatting decision lives next to the math.
    static func formatLabel(_ seconds: TimeInterval) -> String {
        let totalMinutes = Int((seconds / 60).rounded())
        if totalMinutes < 1 { return "<1 min" }
        if totalMinutes < 60 { return "\(totalMinutes) min" }
        let h = totalMinutes / 60
        let m = totalMinutes % 60
        return "\(h)h \(m)m"
    }
}
