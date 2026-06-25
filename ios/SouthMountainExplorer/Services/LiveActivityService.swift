import ActivityKit
import Foundation
import OSLog

private let log = Logger(subsystem: "com.trekdex.app", category: "liveActivity")

/// ActivityKit wrapper. RecordingService calls start/update/end at
/// recording lifecycle points; this service is the single owner of
/// the in-flight `Activity<RecordingActivityAttributes>` instance.
///
/// Update budget: ActivityKit allows ~once per second of background
/// budget. The service throttles internally — callers can fire as
/// often as they like and only the first one per second per recording
/// actually pushes to the Live Activity.
@MainActor
final class LiveActivityService {
    static let shared = LiveActivityService()

    private var activity: Activity<RecordingActivityAttributes>?
    private var lastUpdateAt: Date?
    private let throttleSeconds: TimeInterval = 1.0

    private init() {}

    /// Whether the user has granted permission for Live Activities
    /// (Settings → app → Live Activities toggle). Disabled by default
    /// in some Focus modes, so we check on every start attempt.
    var areActivitiesEnabled: Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    /// Begin a Live Activity for the current recording. No-ops when
    /// the user has Live Activities disabled, or when an activity is
    /// already running (callers can fire this on every start; only
    /// the first wins).
    func start(name: String, subtitle: String?, initialState: RecordingActivityAttributes.ContentState) {
        guard areActivitiesEnabled else {
            log.notice("Live Activities disabled — skipping start")
            return
        }
        guard activity == nil else { return }
        let attrs = RecordingActivityAttributes(name: name, subtitle: subtitle)
        let content = ActivityContent(state: initialState, staleDate: nil)
        do {
            activity = try Activity.request(attributes: attrs, content: content, pushType: nil)
            lastUpdateAt = Date()
            log.notice("started Live Activity id=\(self.activity?.id ?? "nil", privacy: .public)")
        } catch {
            log.error("Live Activity start failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    /// Push a state update. Throttled to ~1/sec to stay within
    /// ActivityKit's budget; updates that arrive faster than that
    /// are silently dropped (the next eligible call will pick up
    /// the freshest state).
    func update(_ state: RecordingActivityAttributes.ContentState) {
        guard let activity else { return }
        if let last = lastUpdateAt, Date().timeIntervalSince(last) < throttleSeconds {
            return
        }
        lastUpdateAt = Date()
        Task {
            await activity.update(ActivityContent(state: state, staleDate: nil))
        }
    }

    /// End the activity. Called by RecordingService.stopRecording
    /// and discardRecording. Dismissal policy is `.immediate` so the
    /// lock-screen card disappears with the recording's UI — without
    /// it iOS keeps the card around for up to four hours.
    func end() {
        guard let activity else { return }
        let final = activity.content.state
        self.activity = nil
        self.lastUpdateAt = nil
        Task {
            await activity.end(ActivityContent(state: final, staleDate: nil), dismissalPolicy: .immediate)
        }
    }
}
