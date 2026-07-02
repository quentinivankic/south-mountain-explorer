import SwiftUI

@main
struct SouthMountainExplorerApp: App {
    // Eagerly initialise all services so they start syncing immediately
    private let auth = AuthService.shared
    private let areas = AreaDataService.shared
    private let silhouettes = AreaSilhouetteService.shared
    private let location = LocationService.shared
    private let recording = RecordingService.shared
    private let progress = ProgressService.shared
    private let coverage = CoverageService.shared
    private let favorites = FavoritesService.shared
    private let activity = ActivityService.shared
    private let activityLog = ActivityLogService.shared

    init() {
        ActivityLogService.shared.log(category: "app", action: "launch")
        // Install the PostHog backend before the first capture so the
        // launch event isn't dropped by the no-op default. No-ops (stays
        // on the no-op backend) if the Info.plist key is absent.
        if let backend = PostHogBackend.fromInfoPlist() {
            AnalyticsService.shared.configure(backend: backend)
        }
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
        AnalyticsService.shared.capture(.appLaunched(build: build))
    }
    // Force the singleton init to register the UNUserNotificationCenter
    // delegate before any notification can be tapped — without this the
    // delegate is set lazily on first ensurePermission() call, which
    // could miss a cold-start tap on a queued trail-complete notification.
    private let notifications = NotificationService.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(auth)
                .environment(areas)
                .environment(silhouettes)
                .environment(location)
                .environment(recording)
                .environment(progress)
                .environment(coverage)
                .environment(favorites)
                .environment(activity)
        }
    }
}
