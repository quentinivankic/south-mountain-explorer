import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for the pure `measureCoverage` function in `TrailCoverage.swift`.
/// The function is the core completion math for the app — every trail
/// celebration, every coverage halo, every Settings stats number flows
/// through it. Validates the two-gate completion logic (fraction ≥
/// threshold AND both endpoints visited) and the sparse-trail filter.
struct TrailCoverageTests {

    // MARK: - Fixtures

    /// Builds a synthetic linear trail running north along a single
    /// segment of `count` evenly-spaced nodes. Each node is ~1.1 m
    /// north of the previous (0.00001° lat ≈ 1.1 m at temperate
    /// latitudes), so a 100-node trail is ~110 m long.
    private func linearTrail(
        id: String = "t1",
        count: Int = 100,
        startLat: Double = 33.3,
        startLon: Double = -112.0,
        deltaLat: Double = 0.00001
    ) -> Trail {
        let nodes = (0..<count).map { i in
            [startLat + Double(i) * deltaLat, startLon]
        }
        return Trail(
            id: id,
            name: "Test Trail",
            distanceMi: 0.07,
            difficulty: .easy,
            segments: [nodes]
        )
    }

    /// Builds a GPS path that walks the same coordinates as `trail`.
    /// Optionally trim from either end to simulate a hiker who stopped
    /// short of one terminus.
    private func walkingPath(
        for trail: Trail,
        dropFromStart: Int = 0,
        dropFromEnd: Int = 0,
        ts0: TimeInterval = 1_710_000_000
    ) -> [GpsPoint] {
        let nodes = trail.segments.flatMap { $0 }
        let trimmed = Array(nodes.dropFirst(dropFromStart).dropLast(dropFromEnd))
        return trimmed.enumerated().map { i, node in
            [node[0], node[1], ts0 + Double(i)]
        }
    }

    // MARK: - Tests

    @Test func fullWalkScoresFullCoverageAndBothEndpoints() throws {
        let trail = linearTrail()
        let path = walkingPath(for: trail)
        let scores = measureCoverage(path: path, trails: [trail])

        let score = try #require(scores[trail.id])
        #expect(score.fraction == 1.0)
        #expect(score.endpointsVisited == true)
    }

    @Test func walkingMissingEndDoesNotSatisfyEndpointGate() throws {
        let trail = linearTrail(count: 100)
        // Walk the first 50 nodes only. The end node is ~55 m past
        // where the hiker stopped (50 nodes × ~1.1 m), well outside
        // the default 30 m buffer → endpointsVisited must be false.
        let path = walkingPath(for: trail, dropFromEnd: 50)
        let scores = measureCoverage(path: path, trails: [trail])

        let score = try #require(scores[trail.id])
        #expect(score.endpointsVisited == false)
    }

    @Test func sparsePathBelowMinFractionFiltersOut() {
        let trail = linearTrail(count: 100)
        // Three GPS samples clustered around a single node — fraction
        // is ~0.01 (1 node out of 100), below the default 0.02 sparse
        // filter, so the trail drops out of the result entirely.
        let oneNode = trail.segments[0][0]
        let path: [GpsPoint] = [
            [oneNode[0], oneNode[1], 0],
            [oneNode[0] + 0.0000001, oneNode[1], 1],
            [oneNode[0] + 0.0000002, oneNode[1], 2],
        ]
        let scores = measureCoverage(path: path, trails: [trail])
        #expect(scores[trail.id] == nil)
    }

    @Test func tooShortPathReturnsEmpty() {
        let trail = linearTrail()
        let path: [GpsPoint] = [[33.3, -112.0, 0], [33.3, -112.0, 1]]
        let scores = measureCoverage(path: path, trails: [trail])
        #expect(scores.isEmpty)
    }

    @Test func endpointsCheckSpansFirstAndLastSegment() throws {
        // Two-segment "L"-shaped trail. Endpoints are the first node
        // of segment 0 and the last node of segment 1. Both must be
        // visited for endpointsVisited to be true.
        let seg1 = (0..<50).map { i in [33.3 + Double(i) * 0.00001, -112.0] }
        let seg2 = (0..<50).map { i in [33.3 + 50 * 0.00001, -112.0 + Double(i) * 0.00001] }
        let trail = Trail(
            id: "L",
            name: "L Trail",
            distanceMi: 0.1,
            difficulty: .moderate,
            segments: [seg1, seg2]
        )

        // Walk only seg1 — hits the start endpoint but not the end of
        // seg2.
        let path1 = seg1.enumerated().map { i, node in
            [node[0], node[1], Double(i)]
        }
        let scores1 = measureCoverage(path: path1, trails: [trail])
        let score1 = try #require(scores1[trail.id])
        #expect(score1.endpointsVisited == false)

        // Walk both segments — endpoints satisfied.
        let allNodes = seg1 + seg2
        let path2 = allNodes.enumerated().map { i, node in
            [node[0], node[1], Double(i)]
        }
        let scores2 = measureCoverage(path: path2, trails: [trail])
        let score2 = try #require(scores2[trail.id])
        #expect(score2.endpointsVisited == true)
    }
}
