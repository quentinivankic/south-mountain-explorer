import ActivityKit
import Foundation

/// Shared between the main app and the widget extension. The app
/// updates `ContentState` ~1x/sec during a recording; the widget
/// renders the lock-screen + Dynamic Island UI from whatever the
/// current state is.
///
/// `name` is fixed for the lifetime of the activity (the area /
/// trail being recorded); `ContentState` carries every value that
/// changes during the hike.
struct RecordingActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        var distanceMeters: Double
        var elapsedSeconds: Int
        var ascentMeters: Double
        /// "→ 420 ft to next turn" / nil when the trail isn't
        /// recording in trail mode (or direction unknown).
        var nextTurnMeters: Double?
    }

    /// Display name for the activity — shown as the title on the
    /// lock-screen card and the leading region of the Dynamic
    /// Island. Trail name in trail mode, area name in roam mode.
    var name: String
    /// Subtitle below the name on the lock-screen card. Area name
    /// in trail mode (so the user has geographic context); blank
    /// in roam mode.
    var subtitle: String?
}
