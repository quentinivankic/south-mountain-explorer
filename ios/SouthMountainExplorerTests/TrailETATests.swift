import Foundation
import CoreLocation
import Testing
@testable import SouthMountainExplorer

/// Tests for `TrailETA.compute` — the gating function the
/// recording panel reads to decide whether to render an ETA pill
/// and what number to put in it. Most cases are about the
/// short-circuits (loop / off-trail / no pace); the actual
/// arithmetic is just `(total - arcLength) / pace`.
struct TrailETATests {

    // MARK: - Fixtures

    /// 1 km long linear trail running north from (33.3, -112.0).
    /// 100 nodes spaced 0.0001° apart ≈ 11 m each → ~1100 m total
    /// (close enough to "1 km" for ETA-scale tests).
    private static let linearTrail = Trail(
        id: "linear",
        name: "Test Linear",
        distanceMi: 0.7,
        difficulty: .easy,
        segments: [
            (0..<100).map { i in
                [33.3 + Double(i) * 0.0001, -112.0]
            }
        ]
    )

    /// Loop trail — square ~11 m on each side, returning to start.
    private static let loopTrail = Trail(
        id: "loop",
        name: "Test Loop",
        distanceMi: 0.1,
        difficulty: .easy,
        segments: [[
            [33.3,        -112.0],
            [33.30001,    -112.0],
            [33.30001,    -111.99999],
            [33.3,        -111.99999],
            [33.3,        -112.0],   // back to start
        ]]
    )

    // MARK: - Linear trail: real ETAs

    @Test func eta_userAtStart_returnsNearTotalDuration() {
        // Walking pace 1 m/s; 1100 m trail; from start → ~1100 s.
        let eta = TrailETA.compute(
            currentLocation: CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            trail: Self.linearTrail,
            paceMetersPerSec: 1.0
        )
        let value = try? #require(eta)
        #expect(value ?? 0 > 1000)
        #expect(value ?? 0 < 1200)
    }

    @Test func eta_userAtEnd_returnsZero() {
        // Walking 1 m/s, user at last vertex → ~0 s remaining.
        let eta = TrailETA.compute(
            currentLocation: CLLocationCoordinate2D(latitude: 33.3 + 99 * 0.0001, longitude: -112.0),
            trail: Self.linearTrail,
            paceMetersPerSec: 1.0
        )
        let value = try? #require(eta)
        #expect(value ?? 100 < 5)
    }

    @Test func eta_userMidway_returnsAboutHalfTotal() {
        // Walking 1 m/s, user at midpoint → ~half of total.
        let eta = TrailETA.compute(
            currentLocation: CLLocationCoordinate2D(latitude: 33.3 + 50 * 0.0001, longitude: -112.0),
            trail: Self.linearTrail,
            paceMetersPerSec: 1.0
        )
        let value = try? #require(eta)
        // Total trail ≈ 1100s walking. Half ≈ 550s.
        #expect(value ?? 0 > 500)
        #expect(value ?? 0 < 600)
    }

    @Test func eta_doublePaceHalvesTime() {
        let coord = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        let slow = TrailETA.compute(currentLocation: coord, trail: Self.linearTrail, paceMetersPerSec: 1.0)
        let fast = TrailETA.compute(currentLocation: coord, trail: Self.linearTrail, paceMetersPerSec: 2.0)
        let s = try #require(slow)
        let f = try #require(fast)
        #expect(abs(f * 2 - s) < 1, "Expected fast×2 ≈ slow, got slow=\(s) fast=\(f)")
    }

    // MARK: - Short-circuit cases (return nil)

    @Test func eta_nilWhenLoopTrail() {
        let eta = TrailETA.compute(
            currentLocation: CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            trail: Self.loopTrail,
            paceMetersPerSec: 1.0
        )
        #expect(eta == nil, "Loop trails should return nil ETA")
    }

    @Test func eta_nilWhenUserOffTrail() {
        // Start coord shifted 100 m east — well past the 50 m
        // on-trail threshold.
        let off = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0 + 0.001)  // ~93 m east
        let eta = TrailETA.compute(
            currentLocation: off,
            trail: Self.linearTrail,
            paceMetersPerSec: 1.0
        )
        #expect(eta == nil, "Off-trail user should return nil ETA")
    }

    @Test func eta_nilWhenPaceMissing() {
        let eta = TrailETA.compute(
            currentLocation: CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            trail: Self.linearTrail,
            paceMetersPerSec: nil
        )
        #expect(eta == nil)
    }

    @Test func eta_nilWhenPaceNearZero() {
        let eta = TrailETA.compute(
            currentLocation: CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            trail: Self.linearTrail,
            paceMetersPerSec: 0.05
        )
        #expect(eta == nil, "Sub-threshold pace should return nil rather than a comically large ETA")
    }

    @Test func eta_nilWhenTrailTooShort() {
        let degenerate = Trail(
            id: "tiny", name: "T", distanceMi: 0, difficulty: .easy,
            segments: [[[33.3, -112.0]]]   // only 1 vertex
        )
        let eta = TrailETA.compute(
            currentLocation: CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            trail: degenerate,
            paceMetersPerSec: 1.0
        )
        #expect(eta == nil)
    }

    // MARK: - Label formatting

    @Test func formatLabel_subMinute() {
        #expect(TrailETA.formatLabel(20) == "<1 min")
        #expect(TrailETA.formatLabel(0) == "<1 min")
    }

    @Test func formatLabel_minutes() {
        #expect(TrailETA.formatLabel(60) == "1 min")
        #expect(TrailETA.formatLabel(720) == "12 min")
        #expect(TrailETA.formatLabel(59 * 60) == "59 min")
    }

    @Test func formatLabel_hoursAndMinutes() {
        #expect(TrailETA.formatLabel(60 * 60) == "1h 0m")
        #expect(TrailETA.formatLabel(65 * 60) == "1h 5m")
        #expect(TrailETA.formatLabel(125 * 60) == "2h 5m")
    }

    @Test func formatLabel_roundsToNearestMinute() {
        // 89 seconds → 1.48 min → rounds to 1
        #expect(TrailETA.formatLabel(89) == "1 min")
        // 91 seconds → 1.52 min → rounds to 2
        #expect(TrailETA.formatLabel(91) == "2 min")
    }
}
