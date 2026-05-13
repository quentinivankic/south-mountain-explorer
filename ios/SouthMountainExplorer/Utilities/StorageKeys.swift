import Foundation

/// All `@AppStorage` / `UserDefaults` keys used by the app, in one
/// place so the strings don't drift across services and the Reset
/// All Progress action can't quietly leave anything behind.
enum StorageKeys {
    // MARK: - App preferences (never cleared)

    static let onboarded = "summit:onboarded"
    static let theme = "summit:theme"

    /// Developer-facing debug HUD toggle. When true, TrailMapView
    /// overlays a translucent box showing FPS, MKMapView overlay
    /// count, last `updateUIView` duration, and resident memory.
    /// Off by default; flipped from Settings → Developer.
    static let debugHUD = "summit:debug-hud"

    // MARK: - User progress (cleared by Reset All Progress)

    static let completedTrails = "summit:completed"
    static let coverage = "summit:coverage"
    static let favorites = "summit:favorites"
    static let activeRecording = "summit:active-recording"
    static let userLocationLat = "location.lat"
    static let userLocationLon = "location.lon"

    // MARK: - Telemetry (kept across resets)

    static let areaOpenedAt = "summit:area-opened-at"
    static let appSessions = "summit:app-sessions"

    // MARK: - Internal caches (cleared by their own paths,
    //         e.g. Clear All Downloads also clears the prefetch cooldown)

    static let prefetchNearbyLastLat = "prefetch.nearby.lastLat"
    static let prefetchNearbyLastLon = "prefetch.nearby.lastLon"

    // MARK: - One-time migrations

    /// Schema version for `hike-history.json` classification fields
    /// (`completedTrailIds` vs `revisitedTrailIds`). v1 backfills the
    /// shuffle introduced by the pre-build-6 mergeCoverage bug. Bump
    /// if a future migration touches the same fields.
    static let hikeHistoryMigrationVersion = "summit:history-migration-version"

    /// Keys wiped by the "Reset All Progress" action in Settings.
    /// Onboarding, theme, telemetry, and prefetch cooldowns stay
    /// untouched — see comments above.
    static let resetAllKeys: [String] = [
        completedTrails,
        coverage,
        favorites,
        activeRecording,
        userLocationLat,
        userLocationLon,
    ]
}
