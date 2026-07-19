import Testing
@testable import SouthMountainExplorer

/// Tests for `ProfileDirectionStore` — the per-trail override of the elevation
/// profile's direction.
///
/// The profile normally orients by whichever trail END is nearest you. This
/// store records the trails where the user said otherwise, and the override
/// must win over the automatic answer without disturbing any other trail.
struct ProfileDirectionStoreTests {

    /// UserDefaults is process-wide, so each test starts from a known state.
    private func fresh() {
        ProfileDirectionStore.clearAll()
    }

    @Test func noOverrideByDefault() {
        fresh()
        #expect(ProfileDirectionStore.override(trailId: "national-trail") == nil,
                "a trail the user never flipped must have no override")
    }

    @Test func resolvedFallsBackToTheAutomaticAnswer() {
        fresh()
        #expect(ProfileDirectionStore.resolved(trailId: "t", automatic: true) == true)
        #expect(ProfileDirectionStore.resolved(trailId: "t", automatic: false) == false,
                "with no override, the latched automatic answer stands")
    }

    @Test func overrideWinsOverTheAutomaticAnswer() {
        fresh()
        ProfileDirectionStore.set(false, trailId: "t")
        #expect(ProfileDirectionStore.resolved(trailId: "t", automatic: true) == false,
                "the user's choice must beat the automatic answer")
    }

    /// A choice that happens to agree with today's automatic answer must still
    /// be stored: "which end is nearer" changes as you travel, so dropping the
    /// override on agreement would silently stop applying tomorrow.
    @Test func agreeingChoiceIsStillPersisted() {
        fresh()
        ProfileDirectionStore.set(true, trailId: "t")
        #expect(ProfileDirectionStore.override(trailId: "t") == true)
        #expect(ProfileDirectionStore.resolved(trailId: "t", automatic: false) == true,
                "an explicit choice must survive the automatic answer changing")
    }

    @Test func overridesAreIsolatedPerTrail() {
        fresh()
        ProfileDirectionStore.set(false, trailId: "a")
        #expect(ProfileDirectionStore.override(trailId: "b") == nil,
                "flipping one trail must not touch another")
        #expect(ProfileDirectionStore.resolved(trailId: "b", automatic: true) == true)
    }

    @Test func clearReturnsTheTrailToAutomatic() {
        fresh()
        ProfileDirectionStore.set(false, trailId: "t")
        ProfileDirectionStore.clear(trailId: "t")
        #expect(ProfileDirectionStore.override(trailId: "t") == nil)
        #expect(ProfileDirectionStore.resolved(trailId: "t", automatic: true) == true,
                "clearing restores the automatic answer")
    }

    @Test func flippingTwiceReturnsToTheOriginal() {
        fresh()
        ProfileDirectionStore.set(false, trailId: "t")
        ProfileDirectionStore.set(true, trailId: "t")
        #expect(ProfileDirectionStore.override(trailId: "t") == true,
                "a second flip overwrites rather than accumulating")
    }
}
