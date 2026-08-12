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

    // MARK: - nextTurn
    //
    // Fixture is an L at 33.34 N: 300 m due north from A to B, then 300 m due
    // east (or west) from B to C. Every expected number below was computed
    // independently in Python against the same haversine and bearing formulas
    // before being written here, so a passing test means the Swift agrees with
    // an outside answer, not with itself.

    private static let cornerA = CLLocationCoordinate2D(latitude: 33.34, longitude: -112.0)
    private static let cornerB = CLLocationCoordinate2D(latitude: 33.34270270270271, longitude: -112.0)
    private static let cornerEast = CLLocationCoordinate2D(latitude: 33.34270270270271,
                                                           longitude: -111.99676487253011)
    private static let cornerWest = CLLocationCoordinate2D(latitude: 33.34270270270271,
                                                           longitude: -112.00323512746989)

    /// A point `meters` north of A, on the A-to-B leg.
    private static func northOfA(_ meters: Double) -> CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: 33.34 + meters / 111_000.0, longitude: -112.0)
    }

    /// A point `meters` east of B, on the B-to-C leg.
    private static func eastOfB(_ meters: Double) -> CLLocationCoordinate2D {
        CLLocationCoordinate2D(
            latitude: Self.cornerB.latitude,
            longitude: -112.0 + meters / (111_000.0 * cos(33.34 * .pi / 180))
        )
    }

    @Test func nextTurn_straightLineHasNone() {
        let straight = [Self.cornerA, Self.northOfA(150), Self.cornerB]
        #expect(PolylineMath.nextTurn(from: Self.northOfA(100),
                                      priorPoint: Self.northOfA(90),
                                      along: straight) == nil)
    }

    @Test func nextTurn_readsARightBend() throws {
        let path = [Self.cornerA, Self.cornerB, Self.cornerEast]
        let turn = try #require(PolylineMath.nextTurn(from: Self.northOfA(100),
                                                      priorPoint: Self.northOfA(90),
                                                      along: path))
        #expect(turn.side == .right)
        // Python, running the same algorithm: 200.351 m.
        #expect(abs(turn.distanceMeters - 200.351) < 0.5)
    }

    @Test func nextTurn_readsALeftBend() throws {
        let path = [Self.cornerA, Self.cornerB, Self.cornerWest]
        let turn = try #require(PolylineMath.nextTurn(from: Self.northOfA(100),
                                                      priorPoint: Self.northOfA(90),
                                                      along: path))
        #expect(turn.side == .left)
    }

    /// Walking the same L from the far end. The stored order runs A to C, the
    /// hiker runs C to A, and the bend that was a right turn one way is a left
    /// turn the other. Getting this wrong would tell half of all hikers to go
    /// the wrong way, which is worse than saying nothing.
    @Test func nextTurn_flipsWhenWalkingAgainstStoredOrder() throws {
        let path = [Self.cornerA, Self.cornerB, Self.cornerEast]
        let turn = try #require(PolylineMath.nextTurn(from: Self.eastOfB(200),
                                                      priorPoint: Self.eastOfB(210),
                                                      along: path))
        #expect(turn.side == .left)
        // Python, running the same algorithm: 200.345 m.
        #expect(abs(turn.distanceMeters - 200.345) < 0.5)
    }

    @Test func nextTurn_ignoresAGentleBend() {
        // 20 degrees at B, under the 60-degree threshold.
        let gentle = CLLocationCoordinate2D(
            latitude: Self.cornerB.latitude + 300 * cos(20 * .pi / 180) / 111_000.0,
            longitude: -112.0 + 300 * sin(20 * .pi / 180) / (111_000.0 * cos(33.34 * .pi / 180))
        )
        let path = [Self.cornerA, Self.cornerB, gentle]
        #expect(PolylineMath.nextTurn(from: Self.northOfA(100),
                                      priorPoint: Self.northOfA(90),
                                      along: path) == nil)
    }

    @Test func nextTurn_silentWhenTooFarOffTrail() {
        let path = [Self.cornerA, Self.cornerB, Self.cornerEast]
        // Inside the L but off both legs. Python puts the perpendicular at
        // 189 m, well past the 50 m gate, so there is no honest answer.
        let lon = Self.eastOfB(200).longitude
        #expect(PolylineMath.nextTurn(
            from: CLLocationCoordinate2D(latitude: 33.3410, longitude: lon),
            priorPoint: CLLocationCoordinate2D(latitude: 33.3409, longitude: lon),
            along: path) == nil)
    }

    @Test func nextTurn_silentWhenStandingStill() {
        let path = [Self.cornerA, Self.cornerB, Self.cornerEast]
        let here = Self.northOfA(100)
        // Under the 3 m floor: GPS jitter, not travel. Without the floor the
        // inferred direction would flip back and forth and swap left for right.
        #expect(PolylineMath.nextTurn(from: here,
                                      priorPoint: Self.northOfA(101),
                                      along: path) == nil)
    }

    @Test func nextTurn_silentWithoutAPriorFix() {
        let path = [Self.cornerA, Self.cornerB, Self.cornerEast]
        #expect(PolylineMath.nextTurn(from: Self.northOfA(100),
                                      priorPoint: nil,
                                      along: path) == nil)
    }

    // MARK: - signedTurn

    @Test func signedTurn_foldsAcrossNorth() {
        // 350 to 10 is a 20-degree RIGHT turn, not a 340-degree left one.
        #expect(abs(PolylineMath.signedTurn(from: 350, to: 10) - 20) < 0.001)
        #expect(abs(PolylineMath.signedTurn(from: 10, to: 350) + 20) < 0.001)
    }

    @Test func signedTurn_signsLeftNegativeAndRightPositive() {
        #expect(PolylineMath.signedTurn(from: 0, to: 90) > 0)
        #expect(PolylineMath.signedTurn(from: 0, to: 270) < 0)
    }
}
