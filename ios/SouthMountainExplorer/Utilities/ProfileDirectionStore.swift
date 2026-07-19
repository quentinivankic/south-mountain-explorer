import Foundation

/// Per-trail overrides of the elevation profile's direction.
///
/// The profile normally orients itself by whichever trail END is nearest you,
/// latched when the chart opens (see `TrailProfile.startIsNearer`). That answer
/// is weakest precisely where you're most likely to be looking: browsing from
/// home, "nearest end" is near-arbitrary, and nothing on screen tells you which
/// way it went. This records the trails where you said otherwise.
///
/// The default is deliberately unchanged — CLAUDE.md pre-registered the flip as
/// an OVERRIDE, never a replacement, because a user-facing question about
/// direction is one most people don't have on most trails.
///
/// Storage is a single `[trailId: Bool]` dictionary rather than a key per trail,
/// so it stays one small UserDefaults entry no matter how many trails get
/// flipped, and can be inspected or cleared in one place.
enum ProfileDirectionStore {

    private static var all: [String: Bool] {
        get {
            UserDefaults.standard
                .dictionary(forKey: StorageKeys.profileDirectionOverrides)
                as? [String: Bool] ?? [:]
        }
        set {
            if newValue.isEmpty {
                UserDefaults.standard.removeObject(forKey: StorageKeys.profileDirectionOverrides)
            } else {
                UserDefaults.standard.set(newValue, forKey: StorageKeys.profileDirectionOverrides)
            }
        }
    }

    /// The user's chosen direction for this trail, or nil when they haven't
    /// expressed one and the automatic answer should stand.
    static func override(trailId: String) -> Bool? {
        all[trailId]
    }

    /// Record a direction for this trail. Always stores explicitly rather than
    /// deleting when it happens to match the automatic answer: "which end is
    /// nearer" changes as you travel, so a choice that agrees today would
    /// silently stop applying tomorrow.
    static func set(_ startIsNearer: Bool, trailId: String) {
        var d = all
        d[trailId] = startIsNearer
        all = d
    }

    /// Drop the override so the trail returns to orienting itself.
    static func clear(trailId: String) {
        var d = all
        d.removeValue(forKey: trailId)
        all = d
    }

    /// Resolve the direction to draw: the user's choice when they made one,
    /// otherwise the automatic answer the caller latched.
    static func resolved(trailId: String, automatic: Bool) -> Bool {
        override(trailId: trailId) ?? automatic
    }

    /// Testing seam — UserDefaults is process-wide, so tests must be able to
    /// reset it without reaching into the key directly.
    static func clearAll() {
        UserDefaults.standard.removeObject(forKey: StorageKeys.profileDirectionOverrides)
    }
}
