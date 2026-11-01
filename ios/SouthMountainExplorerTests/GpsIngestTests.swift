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

    @Test func exactGapBoundaryRemainsContinuous() {
        let path = [pt(0, 0), pt(10, GpsIngest.gapMs)]
        #expect(GpsIngest.continuousRuns(path).count == 1)
        #expect(GpsIngest.materialGapSummary(path) == nil)
    }

    @Test func materialGapSummaryCountsDurationsAndLastRecovery() throws {
        let path = [
            pt(0, 0), pt(10, 2_000),
            pt(100, 122_000), pt(110, 124_000),
            pt(200, 184_000), pt(210, 186_000),
        ]
        let summary = try #require(GpsIngest.materialGapSummary(path))
        #expect(GpsIngest.continuousRuns(path).count == 3)
        #expect(summary.gapCount == 2)
        #expect(summary.totalMissingSeconds == 180)
        #expect(summary.longestMissingSeconds == 120)
        let recoveryTimeMatches = summary.lastRecoveryAt == Date(timeIntervalSince1970: 184)
        #expect(recoveryTimeMatches, "The material-gap recovery time is incorrect")
        let explanationMatches =
            summary.explanation == "GPS paused 2 times for 3m total (longest 2m). No straight-line distance was counted."
        #expect(explanationMatches, "The material-gap explanation is incorrect")
    }

    @Test func malformedMissingAndBackwardTimestampsDoNotInventGaps() {
        let path: [GpsPoint] = [
            pt(0, 100_000),
            [33.3, -112.0],
            pt(10, 500_000),
            pt(20, 400_000),
            [33.3, -112.0, .infinity],
            pt(30, 900_000),
        ]
        #expect(GpsIngest.materialGapSummary(path) == nil)
        #expect(GpsIngest.continuousRuns(path).count == 1)
    }

    @Test func oversizedFiniteTimestampDifferencesDoNotInventGaps() {
        let oversizedElapsed = [pt(0, 0), pt(10, 1e308)]
        let subtractionOverflow = [
            pt(0, -Double.greatestFiniteMagnitude),
            pt(10, Double.greatestFiniteMagnitude),
        ]
        let malformedIntervalsWereIgnored =
            GpsIngest.materialGapSummary(oversizedElapsed) == nil
            && GpsIngest.continuousRuns(oversizedElapsed).count == 1
            && GpsIngest.materialGapSummary(subtractionOverflow) == nil
            && GpsIngest.continuousRuns(subtractionOverflow).count == 1
        #expect(
            malformedIntervalsWereIgnored,
            "Oversized finite timestamp intervals must be omitted"
        )
    }

    @Test func materialGapSummaryOmitsAccumulatorOverflow() {
        let singleGapSeconds = 6e18
        let singleGapMs = singleGapSeconds * 1000
        let path = [pt(0, 0), pt(10, singleGapMs), pt(20, singleGapMs * 2)]
        let candidate = GpsIngest.materialGapSummary(path)
        let summaryExists = candidate != nil
        #expect(summaryExists, "A representable material gap must produce a summary")
        guard let summary = candidate else { return }

        let onlyRepresentableGapWasSummarized =
            summary.gapCount == 1
            && summary.totalMissingSeconds == singleGapSeconds
            && summary.longestMissingSeconds == singleGapSeconds
            && summary.lastRecoveryAt == Date(timeIntervalSince1970: singleGapSeconds)
        #expect(
            onlyRepresentableGapWasSummarized,
            "A gap that overflows the accumulated duration must be omitted"
        )
        let representableExplanationWasFormatted =
            summary.explanation.hasSuffix("No straight-line distance was counted.")
        #expect(
            representableExplanationWasFormatted,
            "A representable large duration must format without trapping"
        )
    }

    @Test func malformedGapSummaryFormattingFailsClosed() {
        let malformed = GpsIngest.MaterialGapSummary(
            gapCount: 1,
            totalMissingSeconds: Double.greatestFiniteMagnitude,
            longestMissingSeconds: Double.greatestFiniteMagnitude,
            lastRecoveryAt: Date(timeIntervalSince1970: 0)
        )
        let usedSafeFallback =
            malformed.explanation == "GPS paused. No straight-line distance was counted."
        #expect(usedSafeFallback, "Malformed gap duration formatting must fail closed")
    }

    @Test func activeStatusDistinguishesWaitingPausedRecoveredAndGoodAtBoundaries() {
        let now = Date(timeIntervalSince1970: 200)
        let continuous = [pt(0, 190_000), pt(10, 192_000)]
        #expect(GpsIngest.activeStatus(path: [], lastFixAt: nil, now: now) == .waiting)
        #expect(GpsIngest.activeStatus(
            path: continuous,
            lastFixAt: Date(timeIntervalSince1970: 100),
            now: now
        ) == .paused)

        let recovered = [pt(0, 50_000), pt(100, 190_000), pt(110, 192_000)]
        #expect(GpsIngest.activeStatus(
            path: recovered,
            lastFixAt: Date(timeIntervalSince1970: 205),
            now: Date(timeIntervalSince1970: 205)
        ) == .recovered)
        #expect(GpsIngest.activeStatus(
            path: recovered,
            lastFixAt: Date(timeIntervalSince1970: 205.001),
            now: Date(timeIntervalSince1970: 205.001)
        ) == .good)
        #expect(GpsIngest.activeStatus(
            path: continuous,
            lastFixAt: Date(timeIntervalSince1970: 200),
            now: Date(timeIntervalSince1970: 200 + GpsIngest.staleFixSeconds)
        ) == .good)
        #expect(GpsIngest.activeStatus(
            path: continuous,
            lastFixAt: Date(timeIntervalSince1970: 200),
            now: Date(timeIntervalSince1970: 200.001 + GpsIngest.staleFixSeconds)
        ) == .paused)
        #expect(GpsIngest.activeStatus(
            path: continuous,
            lastFixAt: Date(timeIntervalSince1970: 200),
            now: now
        ) == .good)
    }

    // MARK: - GPS pause fixes (stale status, heartbeat, segmentation)

    /// Replays `decide` over polls at `times` (seconds) with position
    /// `northAt(t)`; returns the folded path. Polls in `skip` are zero-fix
    /// (the recorder's freshness gate never calls `decide` for them).
    private func replay(times: [Double], northAt: (Double) -> Double,
                        skip: (Double) -> Bool = { _ in false }) -> [GpsPoint] {
        var path: [GpsPoint] = []
        for t in times where !skip(t) {
            let p = pt(northAt(t), t * 1000)
            let d = GpsIngest.decide(prev: path.last, lat: p[0], lon: p[1],
                                     tsMs: p[2], priorCount: path.count)
            if d.keep { path.append(p) }
        }
        return path
    }

    @Test func zeroFixGapFlipsToPausedAfterThresholdAndRecoversOnNextFix() {
        let t0 = Date(timeIntervalSince1970: 1_000)
        let n = GpsIngest.staleFixSeconds
        let walking = [pt(0, 990_000), pt(10, 1_000_000)]
        #expect(GpsIngest.activeStatus(path: walking, lastFixAt: t0,
                                       now: t0.addingTimeInterval(n)) == .good)
        #expect(GpsIngest.activeStatus(path: walking, lastFixAt: t0,
                                       now: t0.addingTimeInterval(n + 0.001)) == .paused)
        // Next good fix after a 60 s hole: path has the material gap.
        let t1 = t0.addingTimeInterval(60)
        let resumed = walking + [pt(20, t1.timeIntervalSince1970 * 1000)]
        #expect(GpsIngest.activeStatus(path: resumed, lastFixAt: t1, now: t1) == .recovered)
        #expect(GpsIngest.activeStatus(
            path: resumed, lastFixAt: t1.addingTimeInterval(16),
            now: t1.addingTimeInterval(16)) == .good)
    }

    @Test func staleFixIsNotAppended() {
        let now = Date(timeIntervalSince1970: 500)
        let m = GpsIngest.maxFixAgeSeconds
        #expect(GpsIngest.isFreshFix(lastFixAt: now.addingTimeInterval(-m), now: now))
        #expect(!GpsIngest.isFreshFix(lastFixAt: now.addingTimeInterval(-m - 0.001), now: now))
        #expect(!GpsIngest.isFreshFix(lastFixAt: nil, now: now))
    }

    @Test func stationaryHoldCreatesNoPause() {
        let times = stride(from: 0.0, through: 150.0, by: 2.0).map { $0 }
        // Walk first so the jitter filter is past warm-up, then stand.
        let walk = replay(times: stride(from: 0.0, through: 20.0, by: 2.0).map { $0 },
                          northAt: { $0 * 1.4 })
        var path = walk
        let standLat = walk.last![0]
        for t in times.map({ $0 + 22 }) {
            let d = GpsIngest.decide(prev: path.last, lat: standLat, lon: -112.0,
                                     tsMs: t * 1000, priorCount: path.count)
            if d.keep { path.append([standLat, -112.0, t * 1000]) }
        }
        #expect(GpsIngest.materialGapSummary(path) == nil)
        #expect(GpsIngest.continuousRuns(path).count == 1)
        let heartbeats = path.count - walk.count
        #expect((13...16).contains(heartbeats), "heartbeats: \(heartbeats)")
    }

    /// Request asked for "stop + gap = two pauses"; per findings.md a stop is
    /// not a GPS pause, so a 150 s stop plus a 60 s zero-fix gap is ONE pause.
    @Test func stopPlusRealGapYieldsExactlyOnePause() throws {
        var path = replay(times: stride(from: 0.0, through: 60.0, by: 2.0).map { $0 },
                          northAt: { $0 * 1.4 })
        let standLat = path.last![0]
        for t in stride(from: 62.0, through: 210.0, by: 2.0) {   // 150 s stop
            let d = GpsIngest.decide(prev: path.last, lat: standLat, lon: -112.0,
                                     tsMs: t * 1000, priorCount: path.count)
            if d.keep { path.append([standLat, -112.0, t * 1000]) }
        }
        // 60 s with no fixes (no decide calls), then moving again.
        let resume = replay(times: [270, 272, 274], northAt: { 84 + ($0 - 270) * 1.4 + 20 })
        for p in resume {
            let d = GpsIngest.decide(prev: path.last, lat: p[0], lon: p[1],
                                     tsMs: p[2], priorCount: path.count)
            if d.keep { path.append(p) }
        }
        let summary = try #require(GpsIngest.materialGapSummary(path))
        #expect(summary.gapCount == 1)
        #expect(abs(summary.longestMissingSeconds - 60) <= 2)
        #expect(summary.totalMissingSeconds == summary.longestMissingSeconds)
    }

    @Test func twoRealGapsYieldTwoPausesAndTrueLongest() throws {
        let path = [pt(0, 0), pt(3, 2_000), pt(6, 4_000),
                    pt(100, 64_000), pt(103, 66_000),          // 60 s gap
                    pt(300, 216_000), pt(303, 218_000)]        // 150 s gap
        let s = try #require(GpsIngest.materialGapSummary(path))
        #expect(s.gapCount == 2)
        #expect(s.longestMissingSeconds == 150)
        #expect(s.totalMissingSeconds == 210)
    }

    @Test func jitteryCadenceCreatesNoSpuriousPauses() {
        var rng = SystemRandomNumberGeneratorSeeded(seed: 42)
        var t = 0.0
        var path: [GpsPoint] = []
        var north = 0.0
        for i in 0..<400 {
            t += Double(1 + Int(rng.next() % 6))          // 1...6 s
            let standing = (i / 50) % 2 == 1
            if !standing { north += 1.4 * 3 }
            let p = pt(north, t * 1000)
            let d = GpsIngest.decide(prev: path.last, lat: p[0], lon: p[1],
                                     tsMs: p[2], priorCount: path.count)
            if d.keep { path.append(p) }
        }
        #expect(GpsIngest.materialGapSummary(path) == nil)
        #expect(GpsIngest.continuousRuns(path).count == 1)
    }

    /// Regression for the 17 fake pauses: a stationary fix every ~20.2 s.
    @Test func repeatedTwentySecondStationaryFixesCreateNoGaps() {
        var path = replay(times: stride(from: 0.0, through: 30.0, by: 2.0).map { $0 },
                          northAt: { $0 * 1.4 })
        let standLat = path.last![0]
        var t = 32.0
        while t < 400 {
            let d = GpsIngest.decide(prev: path.last, lat: standLat, lon: -112.0,
                                     tsMs: t * 1000, priorCount: path.count)
            if d.keep { path.append([standLat, -112.0, t * 1000]) }
            t += 2
        }
        #expect(GpsIngest.materialGapSummary(path) == nil)
    }

    @Test func gapResumeAtSameSpotStillBreaksRun() {
        let d = GpsIngest.decide(prev: pt(0, 0), lat: pt(1, 60_000)[0], lon: -112.0,
                                 tsMs: 60_000, priorCount: 10)
        #expect(d.keep && d.startsNewRun && d.addMeters == 0)
    }

    #if DEBUG
    @Test func ordinaryDebugLaunchDoesNotOverrideRecordingStatusClock() {
        let startedAt = Date(timeIntervalSince1970: 100)
        let active = ActiveRecording(
            areaId: "gps-status-test-area",
            mode: .trail,
            trailId: "gps-status-test-trail",
            startedAt: startedAt,
            path: [pt(0, 100_000), pt(10, 102_000)],
            distanceMi: 0.01,
            priorCompleteTrailIds: []
        )
        #expect(UITestSupport.recordingStatusNow(for: active) == nil)
    }
    #endif

    @Test func oneGapExplanationUsesSingularCopy() throws {
        let summary = try #require(GpsIngest.materialGapSummary([pt(0, 0), pt(10, 21_000)]))
        let explanationMatches =
            summary.explanation == "GPS paused for 21s. No straight-line distance was counted."
        #expect(explanationMatches, "The singular material-gap explanation is incorrect")
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

/// Tiny deterministic generator for the jitter test (no sleeps, no flakiness).
private struct SystemRandomNumberGeneratorSeeded {
    var state: UInt64
    init(seed: UInt64) { state = seed &+ 0x9E3779B97F4A7C15 }
    mutating func next() -> UInt64 {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        return state >> 33
    }
}
