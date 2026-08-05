import Foundation

/// Pure, unit-testable rules for turning a stream of GPS fixes into a recorded
/// path — and for splitting a recorded path back into continuous runs at the
/// gaps where recording paused.
///
/// Why this exists (the screen-lock bug): the recorder polls the latest fix
/// every ~2 s. When GPS drops during a screen lock / signal loss and later
/// resumes, the next fix can be far from the last kept point. The old rule
/// gated purely on distance — reject if `d > 200 m` — which failed two ways:
///   • moved < 200 m during the gap → the jump was accepted and the map/
///     distance joined it to the pre-gap point with a **straight line**,
///     crediting false distance the hiker never walked; and
///   • moved > 200 m → every post-gap fix was rejected forever (the last kept
///     point stays pre-gap), so the recording **stalled** and lost the rest,
///     dropping trail completion.
///
/// The fix gates on *implied speed* instead: a bad fix is a big jump in a tiny
/// time (impossible speed); a legitimate resume is a big jump over a long time
/// (ordinary speed). A large **time** gap is treated as a discontinuity that
/// adds no straight-line distance and starts a new run, and every consumer
/// that draws or measures the path breaks it at the same gaps.
enum GpsIngest {
    /// Consecutive fixes closer than this are stationary jitter and are dropped
    /// so standing still doesn't inflate distance (the prior 3 m rule).
    static let jitterMeters = 3.0
    /// A time gap larger than this between two fixes is a recording
    /// discontinuity (backgrounded / lost signal), not continuous walking —
    /// ~10 missed 2 s polls. Points across such a gap are kept but NOT joined
    /// by distance, and drawing/measuring breaks the path here.
    static let gapMs = 20_000.0
    /// Reject a fix whose implied speed from the previous kept point exceeds
    /// this: an impossible jump is a bad fix, not travel. 15 m/s ≈ 54 km/h —
    /// well above hiking/running, below GPS teleports. Only applies within a
    /// continuous stretch; a post-gap resume has a large `dt` so its implied
    /// speed is small and it is never rejected here.
    static let maxSpeedMps = 15.0
    /// Small on-chart separation inserted between runs so the elevation series
    /// after a gap starts just right of the previous run instead of colliding
    /// with it — enough to clear the de-dupe threshold, not real distance.
    static let runSeparatorMeters = 5.0

    struct Decision: Equatable {
        /// Append this fix to the path.
        let keep: Bool
        /// Meters of continuous travel this fix adds to the running distance —
        /// 0 across a gap (no straight-line credit for the teleport).
        let addMeters: Double
        /// This fix begins a new continuous run (a gap preceded it).
        let startsNewRun: Bool
    }

    /// Decide how to ingest a fix given the previous kept point.
    /// - Parameters:
    ///   - prev: last kept point `[lat, lon, tsMs, …]`, or nil for the first.
    ///   - priorCount: points already in the path — the first few bypass the
    ///     jitter filter so standing at the trailhead still records.
    static func decide(prev: GpsPoint?, lat: Double, lon: Double, tsMs: Double,
                       priorCount: Int) -> Decision {
        guard let prev, prev.count >= 3 else {
            return Decision(keep: true, addMeters: 0, startsNewRun: false)
        }
        let d = haversineDistanceM(lat1: prev[0], lon1: prev[1], lat2: lat, lon2: lon)
        let dt = max(0, (tsMs - prev[2]) / 1000.0)
        if dt > gapMs / 1000.0 {
            // Gap: resume as a new run, crediting no straight-line distance.
            return Decision(keep: true, addMeters: 0, startsNewRun: true)
        }
        // Continuous stretch: drop impossible-speed bad fixes and sub-jitter
        // movement (except while the path is still warming up).
        if dt > 0, d / dt > maxSpeedMps {
            return Decision(keep: false, addMeters: 0, startsNewRun: false)
        }
        if priorCount > 5, d < jitterMeters {
            return Decision(keep: false, addMeters: 0, startsNewRun: false)
        }
        return Decision(keep: true, addMeters: d, startsNewRun: false)
    }

    /// True when the fix at `p` begins a new run relative to `prev` — i.e. more
    /// than `gapMs` elapsed between them. Both must carry a timestamp.
    static func isGap(prev: GpsPoint, p: GpsPoint) -> Bool {
        prev.count >= 3 && p.count >= 3 && (p[2] - prev[2] > gapMs)
    }

    /// Split a recorded path into continuous runs, breaking wherever the time
    /// gap between consecutive fixes exceeds `gapMs`. Consumers draw/measure
    /// per run so nothing crosses a gap.
    static func continuousRuns(_ path: [GpsPoint]) -> [[GpsPoint]] {
        var runs: [[GpsPoint]] = []
        var cur: [GpsPoint] = []
        for p in path {
            if let last = cur.last, isGap(prev: last, p: p) {
                runs.append(cur)
                cur = []
            }
            cur.append(p)
        }
        if !cur.isEmpty { runs.append(cur) }
        return runs
    }
}
