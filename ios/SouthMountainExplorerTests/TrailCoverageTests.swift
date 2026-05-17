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

    /// Build 16 regression: a path that ends ~13 m short of the trail
    /// endpoint used to satisfy the 15 m endpoint buffer and trigger
    /// the completion celebration. The buffer is now 10 m, so this
    /// case correctly fails the endpoint gate. Pins the tighter
    /// threshold so any future loosening surfaces here.
    @Test func endpointJustBeyondTighterBufferFails() throws {
        let trail = linearTrail(count: 100)
        // Drop 12 nodes from the end → path's last sample is ~13 m
        // from the actual endpoint (12 × 1.1 m). Outside the new
        // 10 m gate; would have been inside the old 15 m gate.
        let path = walkingPath(for: trail, dropFromEnd: 12)
        let scores = measureCoverage(path: path, trails: [trail])
        let score = try #require(scores[trail.id])
        #expect(score.endpointsVisited == false,
                "10 m endpoint buffer should reject a path ending 13 m short")
        // Same case still satisfies the old 15 m buffer — pinning
        // the gap distance, not the implementation.
        let lax = measureCoverage(path: path, trails: [trail], endpointBufferMeters: 15)
        let laxScore = try #require(lax[trail.id])
        #expect(laxScore.endpointsVisited == true,
                "control: 15 m buffer would have accepted the same path")
    }

    @Test func sparsePathBelowMinFractionFiltersOut() {
        // Trail with 100 nodes spaced ~111 m apart (deltaLat 0.001),
        // chosen so the 30 m coverage buffer in measureCoverage
        // catches at most one trail node per GPS sample — otherwise
        // the buffer reaches into adjacent neighbors and the
        // fraction climbs out of the "sparse" range the filter is
        // supposed to drop. With the default trail (1.1 m node
        // spacing) the buffer would catch ~27 neighbors and produce
        // fraction 0.27, which is what slipped this test past
        // review back when the test target wasn't wired to CI.
        let trail = linearTrail(count: 100, deltaLat: 0.001)
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

    /// User-reported bug: half a trail walked yesterday, the other half
    /// walked today, completion didn't fire. Root cause was that
    /// `CoverageService.mergeCoverage` stored `max(prior_fraction,
    /// new_fraction)`, so two disjoint-half hikes each reading 0.5
    /// merged to 0.5 and never crossed the 0.95 completion gate.
    ///
    /// Fix is at the caller: feed `measureCoverage` the UNION of GPS
    /// paths across hikes. Spatial-grid math already does the right
    /// thing across all the points — we just have to combine them
    /// before calling. This test pins the per-path math so anyone
    /// touching `measureCoverage` notices if union semantics break.
    @Test func unionOfHalfPathsCoversFullTrail() throws {
        // 100 nodes spaced ~111 m apart so a half-walk reads ~0.5
        // cleanly. With the default ~1.1 m spacing the 30 m coverage
        // buffer spills ~27 nodes past the walk's endpoint, so each
        // half would read ~0.76 — masking the union-vs-single
        // comparison this test wants to make.
        let trail = linearTrail(deltaLat: 0.001)
        // First "hike": walk the south half of the trail only.
        let firstHalf = walkingPath(for: trail, dropFromEnd: 50)
        // Second "hike": walk the north half only. Timestamps shifted
        // a day forward to mirror the real-world reporting scenario.
        let secondHalf = walkingPath(for: trail, dropFromStart: 50, ts0: 1_710_086_400)

        // Each hike on its own reads ~0.5 — clearly below the 0.95
        // completion gate.
        let half1 = try #require(measureCoverage(path: firstHalf, trails: [trail])[trail.id])
        let half2 = try #require(measureCoverage(path: secondHalf, trails: [trail])[trail.id])
        #expect(half1.fraction > 0.45 && half1.fraction < 0.6,
                "first half should read ~0.5, got \(half1.fraction)")
        #expect(half2.fraction > 0.45 && half2.fraction < 0.6,
                "second half should read ~0.5, got \(half2.fraction)")

        // Union of the two hikes' paths reads 1.0 and satisfies the
        // endpoints check (first hike reaches the start, second
        // reaches the end).
        let combined = firstHalf + secondHalf
        let union = try #require(measureCoverage(path: combined, trails: [trail])[trail.id])
        #expect(union.fraction == 1.0,
                "union of disjoint halves should cover the whole trail, got \(union.fraction)")
        #expect(union.endpointsVisited == true)
    }

    // MARK: - measureCoverageByLength

    /// Length-based coverage must match node-count for a uniformly-
    /// spaced trail walked end to end — 100% length, 100% nodes.
    @Test func lengthCoverageFullWalkScoresOne() throws {
        let trail = linearTrail(count: 100, deltaLat: 0.001)
        let path = walkingPath(for: trail)
        let frac = try #require(
            measureCoverageByLength(path: path, trails: [trail])[trail.id]
        )
        #expect(frac > 0.99, "full walk should read ~1.0, got \(frac)")
    }

    /// The bug that prompted the function: an isolated cluster of GPS
    /// samples near a few dense trail nodes inflates the node-count
    /// fraction (count of near-nodes / total) above the actual length
    /// share — the orange overlay renders only ~30 m but the bar
    /// reads ~10%. Length-based math grounds the bar to physical
    /// distance and matches what's drawn.
    ///
    /// Trail = 100 nodes, 1.1 m apart → ~110 m long. GPS = a tight
    /// cluster covering nodes 40..50 (10 nodes, ~11 m of trail). Node-
    /// count says ~10/100 = 10%; length says ~11/110 ≈ 10%. The
    /// real-world wedge appears when node spacing is NON-uniform
    /// (junctions and curves cluster nodes), which is why we switched.
    /// This test pins the simpler same-spacing case so the math stays
    /// honest there too.
    @Test func lengthCoverageMatchesLengthShareOnUniformSpacing() throws {
        let trail = linearTrail(count: 100, deltaLat: 0.0001)
        // Pick out nodes 40..49 and walk just those, so we have ~10
        // covered nodes flanked by uncovered ones on both sides.
        let nodes = trail.segments.flatMap { $0 }
        let covered = Array(nodes[40..<50])
        let path: [GpsPoint] = covered.enumerated().map { i, n in
            [n[0], n[1], 1_710_000_000 + Double(i)]
        }
        let frac = try #require(
            measureCoverageByLength(path: path, trails: [trail])[trail.id]
        )
        // 10 covered nodes → 9 edges fully covered out of 99 total
        // edges ≈ 9.1%. Allow a small ± for the spatial-grid
        // 10m-buffer fuzz at the run boundaries.
        #expect(frac > 0.07 && frac < 0.15,
                "isolated ~10-node cluster should read ~9% length, got \(frac)")
    }

    /// Edges where only ONE endpoint is within the buffer must NOT
    /// contribute — matches `TrailMapView.trailNodeRuns`' rule that
    /// only runs of ≥ 2 consecutive covered nodes render as orange.
    /// Without this, the bar would read non-zero for trails where the
    /// GPS path glanced past a single node mid-segment.
    @Test func lengthCoverageRequiresBothEndpointsCovered() throws {
        let trail = linearTrail(count: 100, deltaLat: 0.001)
        // Walk only a single node mid-trail (node 50). That node
        // gets `hasNeighbor` = true; its neighbors (49 and 51) do
        // NOT (they're 111 m away, far beyond the 10m buffer). So
        // both edges 49-50 and 50-51 fail the both-covered check.
        let nodes = trail.segments.flatMap { $0 }
        let mid = nodes[50]
        let path: [GpsPoint] = [
            [mid[0], mid[1], 1_710_000_000],
            [mid[0], mid[1], 1_710_000_001],
            [mid[0], mid[1], 1_710_000_002]
        ]
        let frac = measureCoverageByLength(path: path, trails: [trail])[trail.id] ?? 0
        #expect(frac == 0, "single-node hit should not contribute, got \(frac)")
    }
}
