import Foundation

/// The GPS trust and off-trail numbers shared by the live guides:
/// `TurnCueEngine` (trail cues), `PlanGuideEngine` (planned hike cues) and
/// `PlanProgressMatcher` (planned hike progress). One place, so the trail
/// guide and the planned hike guide cannot drift apart.
///
/// Not the same as `GpsIngest.staleFixSeconds` (20 s): that one decides when
/// the recording itself treats GPS as lost; this one decides when a guide
/// stops trusting a fix enough to speak.
enum GpsTrustPolicy {
    /// A fix worse than this (metres) is untrustworthy: the guide goes silent.
    static let maxAcceptableAccuracy = 50.0
    /// A fix older than this (seconds, against `now`) is stale.
    static let staleFixSeconds = 10.0

    /// Min seconds between spoken cues (a cue at the turn ignores it).
    static let minSpokenSpacing = 6.0
    /// The "GPS weak" notice: degraded this long, needs this long of good
    /// GPS before another episode counts, and at most one per interval.
    static let weakAfterDegradedSeconds = 15.0
    static let weakNeedsGoodSeconds = 30.0
    static let weakMinIntervalSeconds = 300.0

    /// Off trail or off route: farther than this from the line (metres), or
    /// 2x accuracy plus 15 m if larger...
    static let offLineMeters = 40.0
    /// ...for this long, over this many fixes, with this much movement.
    static let offLineSeconds = 20.0
    static let offLineFixes = 3
    static let offLineMoveMeters = 10.0
    /// No warning until you have been on the line this long.
    static let armSeconds = 10.0
    /// Back on within this share of the off distance, for this long.
    static let backOnFraction = 0.6
    static let backOnSeconds = 6.0
    /// A repeat warning needs this long since the last one and this much
    /// farther from the line.
    static let repeatSeconds = 120.0
    static let repeatFartherMeters = 50.0
}
