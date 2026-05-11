import Foundation
import UserNotifications

/// Owns the UNUserNotificationCenter integration for trail-completion
/// alerts. Local notifications (no APNs / server needed) so this works
/// offline. Permission is requested lazily on first hike start so the
/// system prompt appears alongside the already-familiar location prompt
/// instead of at cold launch with no context.
@MainActor
final class NotificationService: NSObject {
    static let shared = NotificationService()

    /// Posted on the main NotificationCenter when the user taps a trail-
    /// completion notification. ContentView observes this to deep-link
    /// into the area and run a celebration overlay.
    static let celebrateNotification = Notification.Name("summit.celebrateTrailCompletion")

    private let center = UNUserNotificationCenter.current()
    private var didRequest = false

    private override init() {
        super.init()
        center.delegate = self
    }

    /// Idempotent — safe to call before every hike. The system caches the
    /// answer so the user only sees the OS prompt once.
    func ensurePermission() async {
        if didRequest { return }
        didRequest = true
        _ = try? await center.requestAuthorization(options: [.alert, .sound])
    }

    func notifyTrailComplete(areaId: String, areaName: String, trailId: String, trailName: String) {
        let content = UNMutableNotificationContent()
        content.title = "Trail Complete!"
        content.body = "You finished \(trailName) in \(areaName)."
        content.sound = .default
        content.userInfo = [
            "kind": "trailComplete",
            "areaId": areaId,
            "trailId": trailId,
            "trailName": trailName
        ]

        // Unique id per fire so two completions in the same hike don't
        // collide on the same identifier.
        let id = "trail-complete-\(areaId)-\(trailId)-\(Int(Date().timeIntervalSince1970))"
        let req = UNNotificationRequest(identifier: id, content: content, trigger: nil)
        center.add(req) { _ in }
    }
}

extension NotificationService: UNUserNotificationCenterDelegate {
    /// Show the banner even when the app is foreground so the user gets the
    /// same congratulatory beat whether they're looking at the screen or
    /// have the phone in their pocket.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }

    /// Tap → broadcast on NotificationCenter so ContentView (which already
    /// owns the fullScreenCover routing for active recordings) can pick it
    /// up, deep-link into the area, and trigger the celebration animation.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let info = response.notification.request.content.userInfo
        guard
            (info["kind"] as? String) == "trailComplete",
            let areaId = info["areaId"] as? String,
            let trailId = info["trailId"] as? String,
            let trailName = info["trailName"] as? String
        else {
            completionHandler()
            return
        }
        // Post on the main actor so SwiftUI subscribers see it on the
        // expected thread, then signal the system. completionHandler is
        // task-isolated (the delegate method is nonisolated), so calling
        // it from inside the @MainActor Task trips Swift 6 strict
        // concurrency. Calling it after dispatching the post is safe —
        // post is synchronous from the caller's perspective and the
        // system only needs the handler called within ~30s.
        Task { @MainActor in
            NotificationCenter.default.post(
                name: NotificationService.celebrateNotification,
                object: nil,
                userInfo: [
                    "areaId": areaId,
                    "trailId": trailId,
                    "trailName": trailName
                ]
            )
        }
        completionHandler()
    }
}
