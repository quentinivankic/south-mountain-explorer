import Foundation
import UserNotifications

/// Local-notification adapter for turn cues (no APNs, no server). The
/// delegate stays `NotificationService`; this only adds and removes requests.
@MainActor
final class UNTurnCueNotificationCenter: TurnCueNotificationCenter {
    nonisolated static let userInfoKind = "turnCue"

    private let center = UNUserNotificationCenter.current()

    func authorizationStatus() async -> TurnCueNotificationAuthorization {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined: return .notDetermined
        case .denied: return .denied
        default: return .authorized
        }
    }

    func requestAuthorization() async -> Bool {
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        guard granted else { return false }
        let status = await authorizationStatus()
        return status == .authorized
    }

    func post(_ n: TurnCueNotification) async {
        let content = UNMutableNotificationContent()
        content.title = n.title
        content.body = n.body
        content.threadIdentifier = n.threadIdentifier
        content.sound = n.silent ? nil : .default
        content.interruptionLevel = n.level == .timeSensitive ? .timeSensitive : .active
        content.userInfo = ["kind": Self.userInfoKind, "url": n.deepLink]
        // Same identifier replaces the previous pending/delivered cue.
        let request = UNNotificationRequest(identifier: n.identifier, content: content, trigger: nil)
        try? await center.add(request)
    }

    func remove(identifier: String) {
        center.removePendingNotificationRequests(withIdentifiers: [identifier])
        center.removeDeliveredNotifications(withIdentifiers: [identifier])
    }
}
