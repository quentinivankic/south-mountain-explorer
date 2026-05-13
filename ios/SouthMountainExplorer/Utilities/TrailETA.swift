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
/// v1 assumes the user is walking the trail forward (toward the
/// last vertex). When that's wrong the ETA shrinks then grows
/// again as they double back; we'll add direction inference in a
/// follow-up if it's a real problem.
enum TrailETA {

    /// Maximum perpendicular distance (meters) from the trail
    /// before we stop computing an ETA. 50 m is generous enough to
    /// cover GPS scatter on densely-tree-covered trails but tight
    /// enough that someone on a clearly-different path doesn't get
    /// a stale ETA.
    static let onTrailThresholdMeters: Double = 50

    /// Compute ETA in seconds. `nil` for any of the
    /// "show a dash" cases above.
    static func compute(
        currentLocation: CLLocationCoordinate2D,
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
        let remaining = max(0, total - projection.arcLengthFromStart)
        return remaining / pace
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
