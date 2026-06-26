import Foundation

/// A single Dex badge for an area. Pure value type produced by
/// `AchievementEngine.evaluate` from existing data (recorded hikes +
/// trail completions + the area's trail set), so the whole Dex
/// populates retroactively with no persistence or migration.
///
/// Display lives in `DexView`; this model carries everything the grid
/// cell needs to render earned vs locked, the earn date, and partial
/// progress toward the ones that accumulate.
struct Achievement: Identifiable, Equatable {
    let id: String
    let title: String
    /// One-line "how you get this" blurb. For distance badges this is
    /// left to the view to synthesize through `UnitFormatter` so it
    /// honors the imperial/metric toggle; everywhere else the engine
    /// fills it in.
    let detail: String
    /// SF Symbol name for the badge face.
    let symbol: String
    let category: AchievementCategory
    let isEarned: Bool
    /// When the badge was earned, derived by replaying history in
    /// chronological order (the hike or completion that crossed the
    /// threshold). `nil` while locked, or when the date can't be
    /// resolved (e.g. a completion with an unparseable timestamp).
    let earnedDate: Date?
    /// Accumulation state for badges that fill up over time (distance
    /// tiers, season collection, day count, trail count). `nil` for
    /// purely binary badges where a half-filled bar would be noise.
    let progress: AchievementProgress?
}

/// Grid sections in the Dex, top to bottom. Raw value is the section
/// header string shown in the UI.
enum AchievementCategory: String, CaseIterable {
    case milestones = "Milestones"
    case difficulty = "Difficulty"
    case distance = "Distance"
    case dedication = "Dedication"
}

/// How a badge's `progress.target`/`current` should be read. Distance
/// values are canonical meters and get formatted through
/// `UnitFormatter` at render time so they honor the units toggle;
/// counts carry their own noun ("trails", "seasons", "days").
enum AchievementMetric: Equatable {
    case distanceMeters
    case count(unit: String)
}

/// Partial-completion state for an accumulating badge. `current` and
/// `target` are in the unit implied by `metric`.
struct AchievementProgress: Equatable {
    let current: Double
    let target: Double
    let metric: AchievementMetric

    /// Clamped 0...1 fill fraction for the progress bar / ring.
    var fraction: Double {
        guard target > 0 else { return 0 }
        return min(1, max(0, current / target))
    }
}
