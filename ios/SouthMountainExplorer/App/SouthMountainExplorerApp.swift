import SwiftUI

@main
struct SouthMountainExplorerApp: App {
    // Eagerly initialise all services so they start syncing immediately
    private let auth = AuthService.shared
    private let areas = AreaDataService.shared
    private let location = LocationService.shared
    private let recording = RecordingService.shared
    private let progress = ProgressService.shared
    private let coverage = CoverageService.shared
    private let favorites = FavoritesService.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(auth)
                .environment(areas)
                .environment(location)
                .environment(recording)
                .environment(progress)
                .environment(coverage)
                .environment(favorites)
        }
    }
}
