import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `RecordingService.paceMetersPerSec` — the live-pace math.
/// The regression these guard against: path timestamps are epoch
/// MILLISECONDS, and the function once read them as seconds, so the
/// 60 s window was really 60 ms and pace/ETA silently returned nil for
/// every real hike.
struct RecordingPaceTests {

    /// Synthetic straight-line walk. `stepMeters` between adjacent
    /// samples, `stepMs` apart in time (timestamps in ms, matching
    /// `appendPoint`).
    private func walk(points: Int, stepMeters: Double, stepMs: Double,
                      baseMs: Double = 1_700_000_000_000) -> [GpsPoint] {
        let startLat = 33.3, lon = -112.0
        let degPerMeter = 1.0 / 111_000.0   // ~m per degree latitude
        return (0..<points).map { i in
            [startLat + Double(i) * stepMeters * degPerMeter,
             lon,
             baseMs + Double(i) * stepMs]
        }
    }

    /// A steady ~1.4 m/s walk (2.8 m every 2 s) fills pace — and returns
    /// a sane value, not nil. This is the core regression: with the old
    /// ms-as-seconds bug this returned nil.
    @Test func steadyWalkYieldsSanePace() throws {
        let path = walk(points: 40, stepMeters: 2.8, stepMs: 2000)
        let pace = try #require(RecordingService.paceMetersPerSec(path: path, windowSeconds: 60))
        #expect(abs(pace - 1.4) < 0.15)
    }

    /// Standing still (no movement) → nil (below the 0.3 m/s floor).
    @Test func stationaryReturnsNil() {
        let path = walk(points: 40, stepMeters: 0, stepMs: 2000)
        #expect(RecordingService.paceMetersPerSec(path: path, windowSeconds: 60) == nil)
    }

    /// Fewer than 5 samples → nil (too little to trust).
    @Test func tooFewSamplesReturnsNil() {
        let path = walk(points: 4, stepMeters: 2.8, stepMs: 2000)
        #expect(RecordingService.paceMetersPerSec(path: path, windowSeconds: 60) == nil)
    }

    /// A window spanning under 30 s → nil (not enough signal yet). Five
    /// samples 2 s apart span only 8 s.
    @Test func shortSpanReturnsNil() {
        let path = walk(points: 5, stepMeters: 2.8, stepMs: 2000)
        #expect(RecordingService.paceMetersPerSec(path: path, windowSeconds: 60) == nil)
    }

    /// The window really is in seconds: a long walk sampled over minutes
    /// still reports the recent pace, not an average diluted by old
    /// samples. Walk slows to a crawl in the last 60 s → pace reflects
    /// the slow tail, and stays a plausible walking value.
    @Test func windowTracksRecentTail() throws {
        // 60 fast samples (2.8 m / 2 s ≈ 1.4 m/s) then 40 slow ones
        // (0.8 m / 2 s ≈ 0.4 m/s). The 60 s window covers ~30 samples,
        // all from the slow tail.
        var path = walk(points: 60, stepMeters: 2.8, stepMs: 2000)
        let lastFast = path.last!
        let slowStartLat = lastFast[0]
        let slowBaseMs = lastFast[2]
        let degPerMeter = 1.0 / 111_000.0
        for i in 1...40 {
            path.append([slowStartLat + Double(i) * 0.8 * degPerMeter,
                         -112.0,
                         slowBaseMs + Double(i) * 2000])
        }
        let pace = try #require(RecordingService.paceMetersPerSec(path: path, windowSeconds: 60))
        #expect(pace < 0.6)   // reflects the slow tail, not the fast start
    }
}
