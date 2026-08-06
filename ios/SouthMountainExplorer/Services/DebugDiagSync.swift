#if DEBUG
import Foundation

/// DEBUG-ONLY. When the hidden "Auto-sync Diagnostics" toggle (Settings →
/// Developer) is on, this uploads the full backup bundle — hike-history,
/// activity-log, and the completion/coverage state — to the developer's PRIVATE
/// Tailscale endpoint every time the app comes to the foreground. It lets
/// trail-completion / tracking bugs be reproduced from real device data without
/// asking the user to export by hand.
///
/// It is wrapped in `#if DEBUG` and never compiled into a release build,
/// because it uploads GPS history. The endpoint is reachable only from inside
/// the developer's tailnet (`tailscale serve`, not Funnel — nothing public),
/// and is gated by a shared secret. Fire-and-forget, lightly throttled.
enum DebugDiagSync {
    /// Private tailnet URL served by the homelab listener (HTTPS via MagicDNS).
    private static let endpoint = URL(string: "https://servicespc.osiris-chimaera.ts.net/diag")!
    private static let secret = "N2PDhRlPdI0fdxzaGzEak2h1mPn9rGCQ"
    /// Don't re-upload on every rapid foreground bounce (share sheets etc.).
    private static let minInterval: TimeInterval = 45
    private static var lastUpload = Date.distantPast

    /// Upload the current backup bundle if the toggle is on and we haven't
    /// uploaded in the last `minInterval`. Safe to call on every foreground.
    static func uploadIfEnabled() {
        guard UserDefaults.standard.bool(forKey: StorageKeys.debugDiagAutoSync) else { return }
        let now = Date()
        guard now.timeIntervalSince(lastUpload) > minInterval else { return }
        lastUpload = now
        Task.detached(priority: .background) {
            guard let body = try? DataBackupManager.collectExport() else { return }
            var req = URLRequest(url: endpoint)
            req.httpMethod = "POST"
            req.setValue(secret, forHTTPHeaderField: "X-Diag-Secret")
            req.setValue("backup", forHTTPHeaderField: "X-Diag-Kind")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
            _ = try? await URLSession.shared.data(for: req)
        }
    }
}
#endif
