import Foundation
import PostHog

/// PostHog implementation of `AnalyticsBackend`. The ONLY file in the
/// app that imports the PostHog SDK — everything else talks to
/// `AnalyticsService`, so a vendor swap stays contained here.
///
/// Configured privacy-consciously (see docs/backend-pipelines.md):
/// no session replay, no screen-view autocapture, no automatic
/// lifecycle events. TrekDex emits only its own explicit, bucketed
/// events + the user-submitted feedback event. Users stay anonymous —
/// we never call `identify`, so PostHog only ever sees its own
/// generated anonymous id, no TrekDex account linkage.
@MainActor
final class PostHogBackend: AnalyticsBackend {
    /// Build a backend from the PostHog config baked into Info.plist by
    /// project.yml, or `nil` when the key is absent/blank — which keeps
    /// the app on the no-op backend (e.g. a local build that doesn't
    /// carry the key). Callers only `configure` when this is non-nil.
    static func fromInfoPlist() -> PostHogBackend? {
        let info = Bundle.main.infoDictionary
        guard let apiKey = info?["PostHogAPIKey"] as? String, !apiKey.isEmpty,
              let host = info?["PostHogHost"] as? String, !host.isEmpty
        else { return nil }
        return PostHogBackend(apiKey: apiKey, host: host)
    }

    init(apiKey: String, host: String) {
        let config = PostHogConfig(apiKey: apiKey, host: host)
        // Explicit events only — turn off every passive capture path.
        // (Session replay is off by default, so it's left unset.)
        config.captureScreenViews = false
        config.captureApplicationLifecycleEvents = false
        PostHogSDK.shared.setup(config)
    }

    func capture(_ event: AnalyticsEvent) {
        PostHogSDK.shared.capture(event.name, properties: event.properties)
    }

    func flush() {
        PostHogSDK.shared.flush()
    }
}
