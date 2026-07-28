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
    private let trailSearch = TrailSearchService.shared
    private let parkingPool = ParkingPoolService.shared
    private let trailShapes = TrailShapeService.shared

    init() {
        // DEBUG-only: seed a deterministic demo state for the App Store
        // screenshot UI test before anything reads persisted state.
        // Compiled out of Release/TestFlight — see UITestSupport.
        #if DEBUG
        UITestSupport.handleLaunch()
        #endif
        ActivityLogService.shared.log(category: "app", action: "launch")
        // Install the PostHog backend ONLY in Release builds. Debug
        // builds — local dev + the CI test suite launching in a
        // throwaway simulator — would otherwise flood the production
        // PostHog project with junk app_launched events (a fresh
        // anonymous person per run, on the placeholder build "1").
        // Release = TestFlight / App Store = real users. Left on the
        // no-op backend in Debug, so events are captured but dropped.
        #if !DEBUG
        if let backend = PostHogBackend.fromInfoPlist() {
            AnalyticsService.shared.configure(backend: backend)
        }
        #endif
        // Subscribe to MetricKit so field crash/hang counts get
        // forwarded to analytics (delivered aggregated on a later launch).
        CrashReporter.shared.start()
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
                .environment(trailSearch)
                .environment(trailShapes)
        }
    }
}
