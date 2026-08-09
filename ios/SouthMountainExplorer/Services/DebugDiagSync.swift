import Foundation
import UIKit

/// Uploads the full backup bundle — hike-history, activity-log, and the
/// completion/coverage state — to the developer's PRIVATE Tailscale endpoint
/// every time the app comes to the foreground, when the hidden "Auto-sync
/// Diagnostics" toggle (Settings → Developer) is on. It lets trail-completion /
/// tracking bugs be reproduced from real device data without asking the user to
/// export by hand.
///
/// Gating (defence in depth, because it uploads GPS history):
/// 1. `BuildEnv.isTestFlight` — never runs in an App Store production install,
///    even though the code IS compiled into the Release binary (it must be, or
///    the toggle wouldn't exist in the TestFlight build, which is Release).
/// 2. The `debugDiagAutoSync` toggle, off by default.
/// 3. The endpoint is reachable only from inside the developer's tailnet
///    (`tailscale serve`, not Funnel — nothing public) and is secret-gated, so
///    a non-tailnet device can't even resolve the host.
/// Fire-and-forget, lightly throttled.
enum DebugDiagSync {
    /// Private tailnet URL served by the homelab listener (HTTPS via MagicDNS).
    private static let endpoint = URL(string: "https://servicespc.osiris-chimaera.ts.net/diag")!
    private static let secret = "N2PDhRlPdI0fdxzaGzEak2h1mPn9rGCQ"
    /// Don't re-upload on every rapid foreground bounce (share sheets etc.).
    private static let minInterval: TimeInterval = 45
    /// Main-actor isolated: the throttle is only ever read/written from the
    /// scene-phase handler, which runs on the main actor. Isolating it satisfies
    /// Swift 6 strict concurrency (a bare mutable static is a global-state error)
    /// without a `nonisolated(unsafe)` opt-out.
    @MainActor private static var lastUpload = Date.distantPast

    /// Upload the current backup bundle if this is a TestFlight build, the
    /// toggle is on, and we haven't uploaded in the last `minInterval`. Safe to
    /// call on every foreground. Main-actor isolated; called from ContentView's
    /// scene-phase change, which is already on the main actor.
    @MainActor
    static func uploadIfEnabled() {
        guard BuildEnv.isTestFlight else { return }
        guard UserDefaults.standard.bool(forKey: StorageKeys.debugDiagAutoSync) else { return }
        let now = Date()
        guard now.timeIntervalSince(lastUpload) > minInterval else { return }
        lastUpload = now
        Task.detached(priority: .background) {
            guard let body = try? DataBackupManager.collectExport() else { return }
            // Keep the app alive to finish the upload if the user backgrounds or
            // locks the screen right after foregrounding, so iOS does not suspend
            // the app mid-POST and leave the server with an incomplete body.
            let bg = await UIApplication.shared.beginBackgroundTask(withName: "diag-upload")
            var req = URLRequest(url: endpoint)
            req.httpMethod = "POST"
            req.setValue(secret, forHTTPHeaderField: "X-Diag-Secret")
            req.setValue("backup", forHTTPHeaderField: "X-Diag-Kind")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
            _ = try? await URLSession.shared.data(for: req)
            if bg != .invalid {
                await UIApplication.shared.endBackgroundTask(bg)
            }
        }
    }
}
