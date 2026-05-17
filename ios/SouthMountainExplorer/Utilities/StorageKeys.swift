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

    /// Map style for `MapKitMapView`. Raw value matches
    /// `MapStylePreference` enum (`standard` / `satellite` / `hybrid`).
    /// Default `standard` — same as the prior hardcoded `mv.mapType`.
    static let mapStyle = "summit:map-style"

    /// Distance + elevation unit preference. Raw value matches
    /// `UnitsPreference` (`imperial` / `metric`). Default `imperial`
    /// (Arizona dev). Affects every distance / elevation display
    /// site via `UnitFormatter`.
    static let units = "summit:units"

    // MARK: - User progress (cleared by Reset All Progress)

    static let completedTrails = "summit:completed"
    static let coverage = "summit:coverage"
    /// Per-trail coverage *since the last completion* — resets to 0
    /// when a trail completes, then climbs as the user re-walks it.
    /// Drives the "X% remaining" copy and the map's walked-since-
    /// completion overlay. Parallel to `coverage` (lifetime); both
    /// are needed because lifetime stays pinned near 1.0 after a
    /// completion and can't represent "how much you've walked of
    /// the upcoming completion cycle."
    static let coverageSinceCompletion = "summit:coverage-since-completion"
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
        coverageSinceCompletion,
        favorites,
        activeRecording,
        userLocationLat,
        userLocationLon,
    ]
}
