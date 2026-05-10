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
