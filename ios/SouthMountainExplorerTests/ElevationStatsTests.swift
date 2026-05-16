import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `elevationStats` — the smoothing + ascent/descent
/// summation used by the elevation profile chart and stat grid.
struct ElevationStatsTests {

    /// Build a synthetic path where each point carries an altitude.
    /// Distances are computed via haversine in `elevationStats`, so
    /// adjacent points must be physically separated for the
    /// chart's x-axis to grow; 0.00001° lat ≈ 1.1 m suffices.
    private func path(altitudes: [Double?], startLat: Double = 33.3, startLon: Double = -112.0) -> [GpsPoint] {
        altitudes.enumerated().map { i, alt in
            let lat = startLat + Double(i) * 0.00001
            var p: [Double] = [lat, startLon, Double(i)]
            if let alt { p.append(alt) }
            return p
        }
    }

    /// Wrap an `[Double]` into the optional-element shape `path`
    /// wants. Swift 6 strict mode doesn't auto-promote
    /// `[Double] → [Double?]` so callers go through this helper
    /// instead of relying on implicit conversion.
    private func wrap(_ values: [Double]) -> [Double?] {
        values.map { Optional($0) }
    }

    @Test func emptyPathReturnsNil() {
        #expect(elevationStats(path: []) == nil)
    }

    @Test func threeElementPointsReturnNil() {
        // All three-element (no altitude) → no stats.
        let p: [GpsPoint] = (0..<20).map { i in
            [33.3 + Double(i) * 0.00001, -112.0, Double(i)]
        }
        #expect(elevationStats(path: p) == nil)
    }

    @Test func steadyClimbAccumulatesAscent() throws {
        // 21 points climbing 5m per sample = 100m total gain.
        let p = path(altitudes: wrap((0..<21).map { Double($0) * 5 }))
        let stats = try #require(elevationStats(path: p))
        // After smoothing the endpoints lose a touch — accept within
        // 5m of the ideal 100m. Smoothing of a strict linear ramp
        // preserves the trend almost exactly.
        #expect(stats.totalAscentMeters > 95 && stats.totalAscentMeters <= 105,
                "ascent ~100m, got \(stats.totalAscentMeters)")
        #expect(stats.totalDescentMeters < 1,
                "no descent on a pure climb, got \(stats.totalDescentMeters)")
        #expect(abs(stats.maxAltitudeMeters - 100) < 5)
        #expect(abs(stats.minAltitudeMeters - 0) < 5)
        #expect(stats.samples.count == 21)
    }

    @Test func smoothingFlattensGpsNoise() throws {
        // Stationary altitude ±2m noise alternating. Without
        // smoothing this yields ~40m of "ascent" across 21 samples;
        // smoothing should collapse that to near zero.
        let p = path(altitudes: wrap((0..<21).map { $0 % 2 == 0 ? 100.0 : 102.0 }))
        let stats = try #require(elevationStats(path: p))
        #expect(stats.totalAscentMeters < 5,
                "smoothed ±2m noise should be near zero, got \(stats.totalAscentMeters)")
        #expect(stats.totalDescentMeters < 5)
    }

    @Test func mixedThreeAndFourElementPointsContributePartial() throws {
        // 10 points with altitude, 10 without, 10 more with. The
        // gap-bearing points are skipped from the elevation sequence;
        // distance still accumulates so the resulting samples span
        // the full hike (gap-only segment shows an x-axis jump).
        var altitudes: [Double?] = wrap((0..<10).map { Double($0) })
        altitudes.append(contentsOf: Array<Double?>(repeating: nil, count: 10))
        altitudes.append(contentsOf: wrap((0..<10).map { 100.0 + Double($0) }))
        let p = path(altitudes: altitudes)
        let stats = try #require(elevationStats(path: p))
        // Only 20 of the 30 points contributed altitude samples.
        #expect(stats.samples.count == 20)
        // The 100m jump between segments contributes ascent; total
        // should be the first 9m climb + ~100m jump + last 9m climb
        // ≈ 110-120m depending on smoothing.
        #expect(stats.totalAscentMeters > 100,
                "expected ~110m+ across gap, got \(stats.totalAscentMeters)")
    }
}
