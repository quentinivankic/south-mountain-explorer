import Foundation

/// System notification permission, reduced to what turn cues care about.
enum TurnCueNotificationAuthorization: Sendable, Equatable {
    case notDetermined, denied, authorized
}

/// One local notification, as plain data. The UN adapter turns it into a
/// `UNNotificationRequest`; tests compare it directly.
struct TurnCueNotification: Sendable, Equatable {
    enum Level: Sendable, Equatable { case active, timeSensitive }
    /// Same for every cue in a recording, so each new cue REPLACES the
    /// previous pending/delivered one instead of stacking on the Lock Screen.
    let identifier: String
    let threadIdentifier: String
    let title: String
    let body: String
    /// True when audio already carries the cue (voice is speaking).
    let silent: Bool
    let level: Level
    let deepLink: String
}

/// The slice of `UNUserNotificationCenter` the notifier needs.
@MainActor
protocol TurnCueNotificationCenter: AnyObject {
    func authorizationStatus() async -> TurnCueNotificationAuthorization
    /// Shows the system prompt when undetermined. True when authorized afterwards.
    func requestAuthorization() async -> Bool
    func post(_ notification: TurnCueNotification) async
    /// Removes the pending and delivered notification with this identifier.
    func remove(identifier: String)
}

/// Everything the pure planner needs for one event.
struct TurnCueNotificationContext: Sendable, Equatable {
    var enabled: Bool
    var appActive: Bool
    var recordingId: String
    var trailName: String
    var units: UnitsPreference
    /// A `.speak` action was planned for this event.
    var voiceWillSpeak: Bool
}

/// Pure decision: should this cue event become a Lock Screen notification,
/// and what does it say. Foreground, disabled and GPS-weak events give nil.
enum TurnCueNotificationPlanner {
    static func identifier(forRecording id: String) -> String { "turn-cue-\(id)" }

    static func notification(for event: TurnCueEvent, _ ctx: TurnCueNotificationContext) -> TurnCueNotification? {
        guard ctx.enabled, !ctx.appActive else { return nil }
        let trail = ctx.trailName.trimmingCharacters(in: .whitespacesAndNewlines)
        let title: String
        let body: String
        switch event.kind {
        case .gpsWeak:
            return nil
        case .offTrail:
            title = "Off trail"
            body = trail.isEmpty ? "You may be off the trail" : trail
        case .backOnTrail:
            title = "Back on trail"
            body = trail.isEmpty ? "You are back on the trail" : trail
        case .approach, .atJunction:
            let turn = LiveActivityNextTurn(kind: event.kind, symbol: event.symbol,
                                            trailName: trail, distanceMeters: event.distanceMeters)
            title = LiveActivityNextTurnFormatter.row(for: turn, units: ctx.units)?.text ?? event.shortText
            body = trail.isEmpty ? "Tap to open your hike" : trail
        }
        let id = identifier(forRecording: ctx.recordingId)
        return TurnCueNotification(
            identifier: id, threadIdentifier: id, title: title, body: body,
            silent: ctx.voiceWillSpeak,
            // Time Sensitive needs an App ID capability + entitlement (and a
            // regenerated profile); not enabled, so cues are plain `.active`.
            level: .active,
            deepLink: LiveActivityDeepLink.activeHikeURL.absoluteString)
    }
}

/// Posts turn-cue notifications while the app is not active. Dedupes repeats,
/// asks nothing of the user (permission is requested from Settings), and
/// removes its notification when the recording ends.
@MainActor
final class TurnCueNotifier {
    private let center: any TurnCueNotificationCenter
    private var lastKey: [String: String] = [:]
    private var closed: Set<String> = []

    init(center: any TurnCueNotificationCenter) {
        self.center = center
    }

    /// Returns the notification that was posted, if any.
    @discardableResult
    func handle(_ event: TurnCueEvent, _ ctx: TurnCueNotificationContext) async -> TurnCueNotification? {
        guard !closed.contains(ctx.recordingId),
              let note = TurnCueNotificationPlanner.notification(for: event, ctx) else { return nil }
        let key = "\(note.title)|\(note.body)"
        guard lastKey[ctx.recordingId] != key else { return nil }
        guard await center.authorizationStatus() == .authorized,
              !closed.contains(ctx.recordingId) else { return nil }
        lastKey[ctx.recordingId] = key
        await center.post(note)
        // The recording may have ended while the add was in flight.
        if closed.contains(ctx.recordingId) { center.remove(identifier: note.identifier) }
        return note
    }

    /// Recording ended or changed: drop dedupe state and clear the Lock Screen.
    func recordingEnded(recordingId: String) {
        closed.insert(recordingId)
        lastKey[recordingId] = nil
        center.remove(identifier: TurnCueNotificationPlanner.identifier(forRecording: recordingId))
    }
}
