import Foundation
import CoreLocation
import Testing
@testable import SouthMountainExplorer

/// Tests for `PolylineMath` — the pure projection / arc-length /
/// loop-detection helpers used by `TrailETA` to produce the
/// recording-panel ETA. Math is flat-earth (1° lat ≈ 111 km), so
/// the test tolerances reflect that approximation.
struct PolylineMathTests {

    // MARK: - arcLength

    @Test func arcLength_emptyOrSingleVertex() {
        #expect(PolylineMath.arcLength([]) == 0)
        #expect(PolylineMath.arcLength([
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        ]) == 0)
    }

    @Test func arcLength_oneDegreeLatIsAbout111Km() {
        let coords = [
            CLLocationCoordinate2D(latitude: 0, longitude: 0),
            CLLocationCoordinate2D(latitude: 1, longitude: 0),
        ]
        let length = PolylineMath.arcLength(coords)
        // Within 0.5% of 111 km — same tolerance MapMath uses.
        #expect(abs(length - 111_000) < 555)
    }

    @Test func arcLength_chainedSegments() {
        // Three vertices forming two 1.1 m segments along a north
        // line at lat 33.3. Total ≈ 2.2 m.
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,        longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.30001,    longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.30002,    longitude: -112.0),
        ]
        let length = PolylineMath.arcLength(coords)
        #expect(abs(length - 2.22) < 0.05, "Got \(length) m, expected ~2.22 m")
    }

    // MARK: - project

    @Test func project_returnsNilForUnderTwoVertices() {
        let pt = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        #expect(PolylineMath.project(pt, onto: []) == nil)
        #expect(PolylineMath.project(pt, onto: [pt]) == nil)
    }

    @Test func project_pointAtFirstVertexHasZeroArcLength() {
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,     longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.31,    longitude: -112.0),
        ]
        let projection = try? #require(PolylineMath.project(coords[0], onto: coords))
        #expect(projection?.arcLengthFromStart ?? -1 < 0.5)
        #expect(projection?.perpendicularDistance ?? -1 < 0.5)
        #expect(projection?.segmentIndex == 0)
    }

    @Test func project_pointAtLastVertexHasFullArcLength() {
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,     longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.31,    longitude: -112.0),
        ]
        let total = PolylineMath.arcLength(coords)
        let projection = try? #require(PolylineMath.project(coords.last!, onto: coords))
        #expect(abs((projection?.arcLengthFromStart ?? -1) - total) < 0.5)
        #expect(projection?.perpendicularDistance ?? -1 < 0.5)
    }

    @Test func project_pointOffSegmentMeasuresPerpendicular() throws {
        // Polyline runs north along longitude -112.0. Tap at
        // longitude -111.9999 (about 9.3 m east at lat 33.3).
        // Arc length should be midway, perpendicular ~9.3 m.
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,    longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.31,   longitude: -112.0),
        ]
        let off = CLLocationCoordinate2D(latitude: 33.305, longitude: -111.9999)
        let projection = try #require(PolylineMath.project(off, onto: coords))
        let total = PolylineMath.arcLength(coords)
        #expect(abs(projection.arcLengthFromStart - total / 2) < 5,
                "Expected midway arc, got \(projection.arcLengthFromStart) of \(total)")
        #expect(abs(projection.perpendicularDistance - 9.28) < 1.5,
                "Expected ~9.28 m perpendicular, got \(projection.perpendicularDistance)")
    }

    @Test func project_picksClosestSegmentOnMultiSegmentPath() throws {
        // Two segments — one near origin, one far east. Tap near
        // the second one and verify it's picked, with arc length
        // > first segment's length.
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99),  // ~930 m east
            CLLocationCoordinate2D(latitude: 33.3, longitude: -111.98),  // ~1860 m east
        ]
        let firstSegLen = PolylineMath.arcLength([coords[0], coords[1]])
        let nearSecond = CLLocationCoordinate2D(latitude: 33.3, longitude: -111.985)
        let projection = try #require(PolylineMath.project(nearSecond, onto: coords))
        #expect(projection.segmentIndex == 1)
        #expect(projection.arcLengthFromStart > firstSegLen)
    }

    // MARK: - isLoop

    @Test func isLoop_trueWhenEndpointsClose() {
        // Triangle: 3 vertices, last ≈ first.
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,    longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.31,   longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3,    longitude: -111.99),
            CLLocationCoordinate2D(latitude: 33.3,    longitude: -112.0),  // back to start
        ]
        #expect(PolylineMath.isLoop(coords))
    }

    @Test func isLoop_falseForLinearTrail() {
        // Linear north-going trail; first ≠ last.
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,    longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.31,   longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.32,   longitude: -112.0),
        ]
        #expect(!PolylineMath.isLoop(coords))
    }

    @Test func isLoop_falseForDegenerateTwoVertex() {
        // Need at least 3 vertices to form a loop.
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
        ]
        #expect(!PolylineMath.isLoop(coords))
    }

    @Test func isLoop_falseForOutAndBackWith30mEndpointGap() {
        // Regression test for the build-12 device-test bug: an
        // out-and-back trail whose OSM polyline starts at the
        // trailhead and ends at the summit had a ~30m gap between
        // first and last vertex. Under the old 50m threshold this
        // misclassified as a loop and short-circuited TrailETA to
        // nil. The new 10m threshold lets it pass as linear.
        // 0.00027° lat ≈ 30 m at lat 33.3.
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,        longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.305,      longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.30027,    longitude: -112.0),
        ]
        #expect(!PolylineMath.isLoop(coords))
    }

    // MARK: - Trail.flattenedCoords

    @Test func flattenedCoords_concatenatesSegments() {
        let trail = Trail(
            id: "t1", name: "T", distanceMi: 1, difficulty: .easy,
            segments: [
                [[33.3, -112.0], [33.31, -112.0]],
                [[33.31, -112.0], [33.32, -112.0]],
            ]
        )
        let flat = trail.flattenedCoords
        #expect(flat.count == 4)
        #expect(flat.first?.latitude == 33.3)
        #expect(flat.last?.latitude == 33.32)
    }

    @Test func flattenedCoords_skipsMalformedNodes() {
        let trail = Trail(
            id: "t1", name: "T", distanceMi: 1, difficulty: .easy,
            segments: [
                [[33.3, -112.0], [33.31]],   // 2nd node has only 1 coord — drop
            ]
        )
        let flat = trail.flattenedCoords
        #expect(flat.count == 1)
    }

    // MARK: - distanceToNextTurn

    /// Polyline shaped like an "L": ~104 m east, then sharp 90° turn
    /// south. The user is walking east; the turn is the corner.
    @Test func distanceToNextTurn_straightSegmentThenSharpRight() {
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99888),
            CLLocationCoordinate2D(latitude: 33.29900, longitude: -111.99888),
        ]
        let current = CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99946)
        let prior   = CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99970)
        let d = PolylineMath.distanceToNextTurn(
            currentPoint: current, priorPoint: prior, coords: coords
        )
        #expect(d != nil)
        if let d { #expect(abs(d - 50) < 15, "Got \(d), expected ~50") }
    }

    @Test func distanceToNextTurn_returnsNilOnLineWithNoTurn() {
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3,    longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.301,  longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.302,  longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.303,  longitude: -112.0),
        ]
        let current = CLLocationCoordinate2D(latitude: 33.3015, longitude: -112.0)
        let prior   = CLLocationCoordinate2D(latitude: 33.3010, longitude: -112.0)
        #expect(PolylineMath.distanceToNextTurn(
            currentPoint: current, priorPoint: prior, coords: coords
        ) == nil)
    }

    @Test func distanceToNextTurn_returnsNilWhenStationary() {
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99888),
            CLLocationCoordinate2D(latitude: 33.29900, longitude: -111.99888),
        ]
        let pt = CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99946)
        #expect(PolylineMath.distanceToNextTurn(
            currentPoint: pt, priorPoint: pt, coords: coords
        ) == nil)
    }

    @Test func distanceToNextTurn_returnsNilOffTrail() {
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99888),
            CLLocationCoordinate2D(latitude: 33.29900, longitude: -111.99888),
        ]
        let current = CLLocationCoordinate2D(latitude: 33.302, longitude: -111.99946)
        let prior   = CLLocationCoordinate2D(latitude: 33.302, longitude: -111.99970)
        #expect(PolylineMath.distanceToNextTurn(
            currentPoint: current, priorPoint: prior, coords: coords
        ) == nil)
    }

    @Test func distanceToNextTurn_walkingBackwardFindsTurnBehind() {
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3, longitude: -111.99888),
            CLLocationCoordinate2D(latitude: 33.29900, longitude: -111.99888),
        ]
        // User is ~50 m south of the corner, walking north toward it.
        let current = CLLocationCoordinate2D(latitude: 33.29955, longitude: -111.99888)
        let prior   = CLLocationCoordinate2D(latitude: 33.29930, longitude: -111.99888)
        let d = PolylineMath.distanceToNextTurn(
            currentPoint: current, priorPoint: prior, coords: coords
        )
        #expect(d != nil)
        if let d { #expect(abs(d - 50) < 15, "Got \(d), expected ~50") }
    }
}
