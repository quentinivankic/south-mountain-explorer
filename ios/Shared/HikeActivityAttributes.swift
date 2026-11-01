import ActivityKit
import Foundation

/// Live Activity data for an in-progress hike or walk. Compiled into BOTH the
/// app and the TrekDexLiveActivity extension, so it must stay free of app-only
/// types. The app pre-formats every user-facing string (distance units come
/// from an app preference the extension cannot read).
///
/// Evolution rule: new `ContentState` fields MUST be optional so an activity
/// started by an older build still decodes after an update.
struct HikeActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        enum Status: String, Codable, Hashable {
            /// GPS healthy.
            case recording
            case waitingForGPS
            case gpsPaused
            case gpsRecovered
            /// Terminal: the hike was saved.
            case finished
        }

        var status: Status
        /// Trail name, else area name, else "Walk" / "Hiking".
        var title: String
        /// Area name when `title` is a trail name.
        var subtitle: String?
        /// Preformatted, e.g. "3.40 mi".
        var distanceText: String
        /// Preformatted trail length, only present with `progress`.
        var trailLengthText: String?
        /// 0...1, nil hides the progress row.
        var progress: Double?
        var estimatedTrailEndAt: Date?
        var estimatedBackAt: Date?
        /// Set only for `.finished`; freezes the timer.
        var endedAt: Date?
        // New in the extras update. All OPTIONAL so an activity started by an
        // older build still decodes (missing keys become nil).
        /// True when the Finish clock time comes from an approximate estimate.
        var estimatedTrailEndApprox: Bool?
        /// True when the Back clock time comes from an approximate estimate.
        var estimatedBackApprox: Bool?
        /// Next-turn row. Nil hides it.
        var nextTurnArrow: NextTurnArrow?
        /// Preformatted, e.g. "Turn left in 300 ft", "Off Crosscut Trail".
        var nextTurnText: String?
        /// Short distance for compact UI, e.g. "300 ft" or "Now".
        var nextTurnDistanceText: String?
        /// Target trail name for a turn cue.
        var nextTurnTrail: String?
        /// VoiceOver sentence, e.g. "turn left in 300 feet onto Crosscut Trail".
        var nextTurnSpoken: String?
    }
    /// Arrow glyph choice for the next-turn row.
    enum NextTurnArrow: String, Codable, Hashable {
        case keepLeft, keepRight, turnLeft, turnRight, sharpLeft, sharpRight
        case offTrail, backOnTrail
    }

    /// Identity used to adopt or end stale activities.
    var recordingId: String
    /// Timer anchor.
    var startedAt: Date
    var isWalk: Bool
}

extension HikeActivityAttributes.ContentState {
    /// Second line of the next-turn row. Nil when it would repeat a name the
    /// first line already shows ("Off Hidden Valley Trail" + "Hidden Valley Trail").
    var nextTurnSubtitle: String? {
        guard let trail = nextTurnTrail?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trail.isEmpty else { return nil }
        if let text = nextTurnText, text.localizedCaseInsensitiveContains(trail) { return nil }
        return trail
    }
}

extension HikeActivityAttributes.ContentState.Status {
    /// Short label for the status pill; nil when nothing needs saying.
    var pillText: String? {
        switch self {
        case .recording: return nil
        case .waitingForGPS: return "Waiting for GPS"
        case .gpsPaused: return "GPS paused"
        case .gpsRecovered: return "GPS recovered"
        case .finished: return "Hike saved"
        }
    }

    /// Spoken status for VoiceOver.
    var spokenText: String {
        switch self {
        case .recording: return "Recording"
        case .waitingForGPS: return "Waiting for GPS"
        case .gpsPaused: return "GPS paused, route stays safe"
        case .gpsRecovered: return "GPS recovered"
        case .finished: return "Hike saved"
        }
    }

    /// True when GPS is not delivering, so estimates would be stale.
    var isDegraded: Bool { self == .gpsPaused || self == .waitingForGPS }
}
