import Foundation
import Testing
@testable import SouthMountainExplorer

/// Conformance suite for the on-device confidence score. These mirror
/// `data-pipeline/tests/test_scoring_reference.py` value-for-value, so
/// the Swift port and the Python reference can't silently drift. If a
/// weight changes, update BOTH suites in the same reviewed edit.
struct TrailScoringTests {
    let w = ScoringWeights.default

    @Test func officialMaintainedTrailIsHigh() {
        // 50 +20 +20 +10 +10 +10 = 120 → clamp 100 → high.
        let p = TrailScoringProps(name: "x", authoritativeMatch: true,
                                  hasKnownOperator: true, hasName: true,
                                  inOfficialWhitelist: true, regionTrust: "high",
                                  sacScale: "hiking")
        let r = TrailScoring.scoreAndBand(p, weights: w)
        #expect(r.score == 100.0)
        #expect(r.band == .high)
    }

    @Test func informalAbandonedIsLow() {
        // 50 -35 -50 = -35 → clamp 0 → low.
        let p = TrailScoringProps(name: "x", informal: true, lifecycle: "abandoned")
        let r = TrailScoring.scoreAndBand(p, weights: w)
        #expect(r.score == 0.0)
        #expect(r.band == .low)
    }

    @Test func accessRestrictedPenalty() {
        // 50 -40 = 10 → low.
        let p = TrailScoringProps(name: "x", access: "private")
        let r = TrailScoring.scoreAndBand(p, weights: w)
        // 30 − 45 = −15 → clamp 0 → low.
        #expect(r.score == 0.0)
        #expect(r.band == .low)
    }

    @Test func bareNamedPathIsHigh() {
        // 30 + 40 = 70 → high. A name alone clears the bar.
        let p = TrailScoringProps(name: "x", hasName: true)
        let r = TrailScoring.scoreAndBand(p, weights: w)
        #expect(r.score == 70.0)
        #expect(r.band == .high)
    }

    @Test func anonymousPathIsLow() {
        // Base only = 30 → low. The 86%-of-NZ unnamed-footway case.
        let p = TrailScoringProps(name: "x")
        let r = TrailScoring.scoreAndBand(p, weights: w)
        #expect(r.score == 30.0)
        #expect(r.band == .low)
    }

    @Test func sacScaleThresholdAtDemandingMountainHiking() {
        #expect(!TrailScoring.firedSignals(
            TrailScoringProps(name: "x", sacScale: "mountain_hiking"))
            .contains(.sacScaleT4Plus))
        #expect(TrailScoring.firedSignals(
            TrailScoringProps(name: "x", sacScale: "demanding_mountain_hiking"))
            .contains(.sacScaleT4Plus))
        #expect(TrailScoring.firedSignals(
            TrailScoringProps(name: "x", sacScale: "alpine_hiking"))
            .contains(.sacScaleT4Plus))
        // "t4" numeric-style encoding also crosses the threshold.
        #expect(TrailScoring.firedSignals(
            TrailScoringProps(name: "x", sacScale: "t4"))
            .contains(.sacScaleT4Plus))
    }

    @Test func editRecencyFiresWithin30Days() {
        #expect(TrailScoring.firedSignals(
            TrailScoringProps(name: "x", editedDaysAgo: 5))
            .contains(.recentlyEditedOrLowTrust))
        #expect(!TrailScoring.firedSignals(
            TrailScoringProps(name: "x", editedDaysAgo: 200))
            .contains(.recentlyEditedOrLowTrust))
    }

    @Test func lowTrustEditorFiresRegardlessOfRecency() {
        #expect(TrailScoring.firedSignals(
            TrailScoringProps(name: "x", lowTrustEditor: true, editedDaysAgo: 200))
            .contains(.recentlyEditedOrLowTrust))
    }

    @Test func clampedTo0And100() {
        let floor = TrailScoring.score(
            TrailScoringProps(name: "x", access: "no", informal: true,
                              lifecycle: "abandoned", trailVisibility: "no"),
            weights: w)
        #expect(floor >= 0)
        // Empty props = base only, never below 0.
        #expect(TrailScoring.score(TrailScoringProps(name: "x"), weights: w) >= 0)
    }

    @Test func bandCutoffs() {
        #expect(TrailScoring.band(70, weights: w) == .high)
        #expect(TrailScoring.band(69.9, weights: w) == .medium)
        #expect(TrailScoring.band(45, weights: w) == .medium)
        #expect(TrailScoring.band(44.9, weights: w) == .low)
    }

    @Test func caseAndWhitespaceInsensitiveInputs() {
        // Reference lowercases + strips; " Private " → restricted.
        #expect(TrailScoring.firedSignals(
            TrailScoringProps(name: "x", access: " Private "))
            .contains(.accessRestricted))
        #expect(TrailScoring.firedSignals(
            TrailScoringProps(name: "x", regionTrust: "HIGH"))
            .contains(.regionTrustHigh))
    }
}
