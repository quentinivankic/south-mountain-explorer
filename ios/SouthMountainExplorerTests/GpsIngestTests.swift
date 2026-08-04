import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `GpsIngest` — the GPS-fix ingestion rules that fix the
/// screen-lock bug (a gap in recording used to snap a straight line and
/// either credit false distance or stall the recording), plus the
/// elevation profile's handling of the same gaps.
struct GpsIngestTests {
    private let degPerMeter = 1.0 / 111_320.0
    /// A point `metersNorth` north of (33.3, -112.0) at `tsMs`.
    private func pt(_ metersNorth: Double, _ tsMs: Double, alt: Double? = nil) -> GpsPoint {
        var p: GpsPoint = [33.3 + metersNorth * degPerMeter, -112.0, tsMs]
        if let alt { p.append(alt) }
        return p
    }

    @Test func firstPointIsAlwaysKept() {
        let d = GpsIngest.decide(prev: nil, lat: 33.3, lon: -112.0, tsMs: 0, priorCount: 0)
        #expect(d.keep)
        #expect(d.addMeters == 0)
        #expect(!d.startsNewRun)
    }

    @Test func steadyMoveCreditsRealDistance() {
        // 10 m step (above the 3 m jitter floor) over 2 s — a normal walking
        // sample: kept, credited as real distance, same run.
        let prev = pt(0, 0)
        let d = GpsIngest.decide(prev: prev, lat: pt(10, 2000)[0], lon: -112.0,
                                 tsMs: 2000, priorCount: 10)
        #expect(d.keep)
        #expect(abs(d.addMeters - 10) < 0.5)
        #expect(!d.startsNewRun)
    }

    @Test func stationaryJitterDroppedAfterWarmup() {
        let d = GpsIngest.decide(prev: pt(0, 0), lat: pt(1, 2000)[0], lon: -112.0,
                                 tsMs: 2000, priorCount: 10)
        #expect(!d.keep)   // 1 m < jitterMeters
    }

    @Test func earlyJitterIsKeptWhileWarmingUp() {
        let d = GpsIngest.decide(prev: pt(0, 0), lat: pt(1, 2000)[0], lon: -112.0,
                                 tsMs: 2000, priorCount: 2)
        #expect(d.keep)    // first few points bypass the jitter filter
    }

    /// A 300 m jump in 2 s is impossible (150 m/s) — a bad fix, rejected.
    @Test func impossibleSpeedFixRejected() {
        let d = GpsIngest.decide(prev: pt(0, 0), lat: pt(300, 2000)[0], lon: -112.0,
                                 tsMs: 2000, priorCount: 10)
        #expect(!d.keep)
    }

    /// THE regression: a 300 m move over a 2-minute gap (screen locked) is a
    /// legitimate resume — kept, starting a new run, crediting NO straight-line
    /// distance. The old rule rejected this forever (stalling the recording).
    @Test func gapResumeKeptAsNewRunWithNoDistance() {
        let d = GpsIngest.decide(prev: pt(0, 0), lat: pt(300, 120_000)[0], lon: -112.0,
                                 tsMs: 120_000, priorCount: 10)
        #expect(d.keep)
        #expect(d.addMeters == 0)
        #expect(d.startsNewRun)
    }

    @Test func continuousRunsSplitAtTimeGap() {
        let path = [pt(0, 0), pt(3, 2000), pt(6, 4000),          // run 1
                    pt(300, 134_000), pt(303, 136_000)]          // run 2 after a 130 s gap
        let runs = GpsIngest.continuousRuns(path)
        #expect(runs.count == 2)
        #expect(runs[0].count == 3)
        #expect(runs[1].count == 2)
    }

    /// Elevation profile must not inflate distance or count climb across a gap.
    @Test func elevationStatsIgnoresGapJump() throws {
        // run 1: gentle 4 m climb over ~40 m; then a 2-minute gap to a point
        // 300 m away that is 96 m higher (elevation change that happened while
        // NOT recording); then one more sample.
        var path: [GpsPoint] = []
        for i in 0..<5 { path.append(pt(Double(i) * 10, Double(i) * 2000, alt: 100 + Double(i))) }
        path.append(pt(300, 130_000, alt: 200))
        path.append(pt(310, 132_000, alt: 201))
        let stats = try #require(elevationStats(path: path))
        // The 300 m teleport is not distance: x stays tens of meters, not ~340.
        #expect((stats.samples.last?.distanceMeters ?? 999) < 80)
        // The 96 m gap jump is not counted as ascent — only the real ~4 m is.
        #expect(stats.totalAscentMeters < 10)
        // The post-gap samples belong to a later run.
        #expect(stats.samples.contains { $0.run == 1 })
    }
}
