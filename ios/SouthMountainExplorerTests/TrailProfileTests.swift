import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `TrailProfile` — snapping a position onto a trail and reading the
/// baked elevation series from there.
///
/// These assert POSITION and ORIENTATION behaviour, never "index 0 is the
/// trailhead". OSM way order is arbitrary, so any test that assumed a start
/// would encode a bug.
struct TrailProfileTests {

    /// A straight ~1,000 m northward trail from (33.0, -112.0), one vertex
    /// every ~100 m so segment projection has something to bite on.
    private var northTrail: [[[Double]]] {
        let step = 100.0 / 111_132.0
        return [(0...10).map { [33.0 + Double($0) * step, -112.0] }]
    }

    // MARK: - Geometry

    @Test func cumulativeMetersGrowsMonotonically() {
        let pts = TrailProfile.polyline(northTrail)
        let cum = TrailProfile.cumulativeMeters(pts)
        #expect(cum.count == pts.count)
        #expect(cum.first == 0)
        #expect(zip(cum, cum.dropFirst()).allSatisfy { $0 < $1 })
        #expect(abs((cum.last ?? 0) - 1000) < 20)   // ~1 km, haversine vs flat
    }

    @Test func polylineFlattensMultipleSegmentsAndSkipsMalformedPoints() {
        let segs: [[[Double]]] = [[[1, 2], [3, 4]], [[5, 6]], [[7]]]
        let pts = TrailProfile.polyline(segs)
        #expect(pts.count == 3)                      // the 1-element point is dropped
        #expect(pts[2] == TrailProfile.Point(lat: 5, lon: 6))
    }

    // MARK: - Snapping

    @Test func snapFindsMidpointOfTrail() throws {
        // Standing on the trail at its halfway vertex.
        let mid = 33.0 + (500.0 / 111_132.0)
        let s = try #require(TrailProfile.snap(lat: mid, lon: -112.0, segments: northTrail))
        #expect(abs(s.fraction - 0.5) < 0.02)
        #expect(s.offTrailMeters < 5)
    }

    @Test func snapProjectsBetweenVerticesNotOntoThem() throws {
        // 50 m past a vertex — vertex-snapping would quantise this to 0.5 or
        // 0.6; segment projection must land between them.
        let p = 33.0 + (550.0 / 111_132.0)
        let s = try #require(TrailProfile.snap(lat: p, lon: -112.0, segments: northTrail))
        #expect(s.fraction > 0.52 && s.fraction < 0.58, "got \(s.fraction)")
    }

    @Test func snapReportsOffTrailDistance() throws {
        // ~100 m east of the halfway point.
        let mid = 33.0 + (500.0 / 111_132.0)
        let east = -112.0 + (100.0 / (111_320.0 * cos(33.0 * .pi / 180)))
        let s = try #require(TrailProfile.snap(lat: mid, lon: east, segments: northTrail))
        #expect(abs(s.offTrailMeters - 100) < 10, "got \(s.offTrailMeters)")
        #expect(abs(s.fraction - 0.5) < 0.02)        // still halfway ALONG
    }

    @Test func snapClampsBeyondEitherEnd() throws {
        let before = try #require(TrailProfile.snap(lat: 32.99, lon: -112.0, segments: northTrail))
        let after = try #require(TrailProfile.snap(lat: 33.02, lon: -112.0, segments: northTrail))
        #expect(before.fraction == 0)
        #expect(after.fraction == 1)
    }

    @Test func snapReturnsNilForUnusableGeometry() {
        #expect(TrailProfile.snap(lat: 0, lon: 0, segments: []) == nil)
        #expect(TrailProfile.snap(lat: 0, lon: 0, segments: [[[1, 2]]]) == nil)
        // Zero-length trail: every vertex identical, so "fraction along" is
        // undefined rather than 0.
        #expect(TrailProfile.snap(lat: 1, lon: 2, segments: [[[1, 2], [1, 2]]]) == nil)
    }

    // MARK: - Reading the series

    @Test func elevationInterpolatesBetweenSamples() {
        let p = [100, 200, 300]           // 0.0, 0.5, 1.0
        #expect(TrailProfile.elevationFt(p, at: 0.0) == 100)
        #expect(TrailProfile.elevationFt(p, at: 0.5) == 200)
        #expect(TrailProfile.elevationFt(p, at: 1.0) == 300)
        #expect(TrailProfile.elevationFt(p, at: 0.25) == 150)   // interpolated
    }

    @Test func elevationHandlesEdgeCases() {
        #expect(TrailProfile.elevationFt([], at: 0.5) == nil)
        #expect(TrailProfile.elevationFt([42], at: 0.7) == 42)
        #expect(TrailProfile.elevationFt([10, 20], at: -5) == 10)   // clamped
        #expect(TrailProfile.elevationFt([10, 20], at: 99) == 20)   // clamped
    }

    // MARK: - Orientation

    @Test func startIsNearerWhenStandingAtTheStoredStart() throws {
        let near = try #require(TrailProfile.startIsNearer(
            lat: 32.995, lon: -112.0, segments: northTrail))
        #expect(near)
    }

    @Test func startIsNotNearerWhenStandingBeyondTheStoredEnd() throws {
        let north = 33.0 + (1200.0 / 111_132.0)
        let near = try #require(TrailProfile.startIsNearer(
            lat: north, lon: -112.0, segments: northTrail))
        #expect(!near)
    }

    @Test func startIsNearerWorksAtAnyDistanceNoCutoff() throws {
        // The whole point of this rule: it always has an answer. 60 km south
        // of the trail still resolves to the southern (stored start) end.
        let far = 33.0 - (60_000.0 / 111_132.0)
        let near = try #require(TrailProfile.startIsNearer(
            lat: far, lon: -112.0, segments: northTrail))
        #expect(near)
    }

    @Test func startIsNearerNilWithoutGeometry() {
        #expect(TrailProfile.startIsNearer(lat: 33, lon: -112, segments: []) == nil)
    }

    @Test func orientedLeavesNearStartUntouched() {
        let p = [100, 200, 300, 400]
        let o = TrailProfile.oriented(p, fraction: 0.25, startIsNearer: true)
        #expect(o.samples == p)
        #expect(o.fraction == 0.25)
    }

    @Test func orientedMirrorsBothSeriesAndPositionWhenEndIsNearer() {
        // Stored order runs toward the user: the series flips AND the marker
        // moves with it, so the two stay consistent.
        let p = [100, 200, 300, 400]
        let o = TrailProfile.oriented(p, fraction: 0.25, startIsNearer: false)
        #expect(o.samples == [400, 300, 200, 100])
        #expect(o.fraction == 0.75)
        // The elevation under the marker must not change just because we
        // re-drew the chart — that would be a visible jump on screen.
        #expect(TrailProfile.elevationFt(p, at: 0.25)
                == TrailProfile.elevationFt(o.samples, at: o.fraction))
    }

    // MARK: - Decoding

    @Test func trailDecodesProfileAndToleratesItsAbsence() throws {
        let with = #"{"id":"t","name":"T","distanceMi":1,"difficulty":"Easy","segments":[],"profileFt":[10,20]}"#
        let without = #"{"id":"t","name":"T","distanceMi":1,"difficulty":"Easy","segments":[]}"#
        let a = try JSONDecoder().decode(Trail.self, from: Data(with.utf8))
        let b = try JSONDecoder().decode(Trail.self, from: Data(without.utf8))
        #expect(a.profileFt == [10, 20])
        #expect(b.profileFt == nil)     // pre-profile geom stays valid
    }
}
