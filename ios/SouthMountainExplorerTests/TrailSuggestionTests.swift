import Foundation
import CoreLocation
import Testing
@testable import SouthMountainExplorer

/// Tests for `TrailSuggestionEngine.candidates` — the pure ranker
/// behind the build-12 mid-recording suggestion banner. The
/// gating rules are what's interesting; the arithmetic is just
/// `(detour + remaining) / pace`.
struct TrailSuggestionTests {

    // MARK: - Fixtures

    /// Trail that passes within ~10 m of (33.30005, -112.0). 100
    /// nodes spaced 0.0001° (~11 m) north. ~1.1 km long.
    private static let nearbyTrail = Trail(
        id: "nearby",
        name: "Nearby Trail",
        distanceMi: 0.7,
        difficulty: .easy,
        segments: [(0..<100).map { i in [33.3 + Double(i) * 0.0001, -112.0] }]
    )

    /// Trail well to the east — perpendicular ~930 m from
    /// (33.3, -112.0). Used to verify the detour filter excludes
    /// far-away trails.
    private static let farTrail = Trail(
        id: "far",
        name: "Far Trail",
        distanceMi: 0.7,
        difficulty: .easy,
        segments: [(0..<100).map { i in [33.3 + Double(i) * 0.0001, -111.99] }]
    )

    /// Long trail (5 mi) — used to verify the remaining-length
    /// filter excludes trails that wouldn't be "easily complete."
    private static let longTrail = Trail(
        id: "long",
        name: "Long Trail",
        distanceMi: 5.0,
        difficulty: .moderate,
        segments: [(0..<100).map { i in [33.3 + Double(i) * 0.0001, -112.0] }]
    )

    /// User location near (but not on) `nearbyTrail`.
    private static let userLocation = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)

    // MARK: - Happy path

    @Test func picksNearbyIncompleteTrail() throws {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [Self.nearbyTrail],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0
        )
        let first = try #require(candidates.first)
        #expect(first.trail.id == "nearby")
        #expect(first.detourMeters < 1.0, "User is on the trail — detour ~0")
        #expect(first.remainingMeters > 1000, "Full trail uncovered")
        #expect(first.extraSeconds > 1000)
    }

    @Test func ranksByLowestExtraTime() throws {
        // Two qualifying trails, the close one wins.
        let closer = Self.nearbyTrail
        let slightlyFarther = Trail(
            id: "slightly-farther",
            name: "Slightly Farther",
            distanceMi: 0.7,
            difficulty: .easy,
            // ~6 m east of the closer trail (still inside detour cap)
            segments: [(0..<100).map { i in [33.3 + Double(i) * 0.0001, -111.99994] }]
        )
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [slightlyFarther, closer],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0,
            maxResults: 2
        )
        #expect(candidates.count == 2)
        #expect(candidates[0].trail.id == "nearby")
        #expect(candidates[1].trail.id == "slightly-farther")
        #expect(candidates[0].extraSeconds <= candidates[1].extraSeconds)
    }

    // MARK: - Filter rules

    @Test func skipsCurrentTrail() {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: "nearby",   // user is recording on it
            trails: [Self.nearbyTrail],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0
        )
        #expect(candidates.isEmpty, "Shouldn't suggest the trail the user is on")
    }

    @Test func skipsCompletedTrail() {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [Self.nearbyTrail],
            coverageByTrailId: ["nearby": 0.95],   // already complete
            paceMetersPerSec: 1.0
        )
        #expect(candidates.isEmpty, "Trails ≥ 0.9 coverage shouldn't be suggested")
    }

    @Test func skipsFarTrail() {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [Self.farTrail],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0
        )
        #expect(candidates.isEmpty, "~930 m detour exceeds the 200 m default cap")
    }

    @Test func skipsLongTrail() {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [Self.longTrail],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0
        )
        #expect(candidates.isEmpty, "5 mi remaining exceeds the 1 mi default cap")
    }

    @Test func skipsWhenPaceUnavailable() {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [Self.nearbyTrail],
            coverageByTrailId: [:],
            paceMetersPerSec: nil
        )
        #expect(candidates.isEmpty, "No pace ⇒ no extraSeconds to rank or display")
    }

    @Test func skipsWhenPaceNearZero() {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [Self.nearbyTrail],
            coverageByTrailId: [:],
            paceMetersPerSec: 0.05
        )
        #expect(candidates.isEmpty, "Sub-threshold pace shouldn't produce silly-large ETAs")
    }

    @Test func partialCoverageReducesRemaining() throws {
        // Same trail with 50% coverage — remaining halved, should
        // still qualify (well under 1 mi remaining).
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [Self.nearbyTrail],
            coverageByTrailId: ["nearby": 0.5],
            paceMetersPerSec: 1.0
        )
        let first = try #require(candidates.first)
        let totalMeters = Self.nearbyTrail.distanceMi * 1609.344
        let expectedRemaining = totalMeters * 0.5
        #expect(abs(first.remainingMeters - expectedRemaining) < 1)
    }

    @Test func emptyTrailsReturnsEmpty() {
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0
        )
        #expect(candidates.isEmpty)
    }

    @Test func skipsTrailWithLessThanTwoVertices() {
        let degenerate = Trail(
            id: "tiny", name: "T", distanceMi: 0.1, difficulty: .easy,
            segments: [[[33.3, -112.0]]]   // single vertex
        )
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [degenerate],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0
        )
        #expect(candidates.isEmpty)
    }

    @Test func respectsMaxResults() {
        // Three qualifying trails offset slightly so they're all
        // within the detour cap. maxResults: 1 returns only the
        // closest.
        let a = Self.nearbyTrail
        let b = Trail(
            id: "b", name: "B", distanceMi: 0.7, difficulty: .easy,
            segments: [(0..<100).map { i in [33.3 + Double(i) * 0.0001, -111.99996] }]
        )
        let c = Trail(
            id: "c", name: "C", distanceMi: 0.7, difficulty: .easy,
            segments: [(0..<100).map { i in [33.3 + Double(i) * 0.0001, -111.99994] }]
        )
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: Self.userLocation,
            currentTrailId: nil,
            trails: [c, b, a],
            coverageByTrailId: [:],
            paceMetersPerSec: 1.0,
            maxResults: 1
        )
        #expect(candidates.count == 1)
        #expect(candidates.first?.trail.id == "nearby")
    }

    // MARK: - Label formatting

    @Test func detourMilesLabelRoundsToOneDecimal() {
        let s = TrailSuggestion(
            trail: Self.nearbyTrail,
            detourMeters: 161.0,    // ≈ 0.1 mi
            remainingMeters: 500,
            extraSeconds: 600
        )
        #expect(s.detourMilesLabel == "0.1 mi")
    }
}
