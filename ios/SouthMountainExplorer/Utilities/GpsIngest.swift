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

    struct MaterialGapSummary: Equatable {
        let gapCount: Int
        let totalMissingSeconds: TimeInterval
        let longestMissingSeconds: TimeInterval
        let lastRecoveryAt: Date

        var explanation: String {
            if gapCount == 1 {
                return "GPS paused for \(GpsIngest.durationLabel(totalMissingSeconds)). "
                    + "No straight-line distance was counted."
            }
            return "GPS paused \(gapCount) times for \(GpsIngest.durationLabel(totalMissingSeconds)) total "
                + "(longest \(GpsIngest.durationLabel(longestMissingSeconds))). "
                + "No straight-line distance was counted."
        }
    }

    enum ActiveStatus: Equatable {
        case waiting
        case paused
        case recovered
        case good
    }

    static let staleFixSeconds: TimeInterval = 45
    static let recoveredDisplaySeconds: TimeInterval = 15

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
        guard prev.count >= 3, p.count >= 3,
              prev[2].isFinite, p[2].isFinite else { return false }
        return p[2] > prev[2] && p[2] - prev[2] > gapMs
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

    /// Summarize only trustworthy, forward-moving timestamp gaps. The source
    /// path is never changed, and malformed legacy points cannot create a
    /// warning.
    static func materialGapSummary(_ path: [GpsPoint]) -> MaterialGapSummary? {
        guard path.count >= 2 else { return nil }
        var gapCount = 0
        var totalMissingSeconds: TimeInterval = 0
        var longestMissingSeconds: TimeInterval = 0
        var lastRecoveryAt: Date?

        for index in 1..<path.count {
            let previous = path[index - 1]
            let current = path[index]
            guard isValidTimestampedPoint(previous), isValidTimestampedPoint(current) else {
                continue
            }
            let elapsedMs = current[2] - previous[2]
            guard elapsedMs > gapMs else { continue }
            let elapsedSeconds = elapsedMs / 1000
            gapCount += 1
            totalMissingSeconds += elapsedSeconds
            longestMissingSeconds = max(longestMissingSeconds, elapsedSeconds)
            lastRecoveryAt = Date(timeIntervalSince1970: current[2] / 1000)
        }

        guard let lastRecoveryAt else { return nil }
        return MaterialGapSummary(
            gapCount: gapCount,
            totalMissingSeconds: totalMissingSeconds,
            longestMissingSeconds: longestMissingSeconds,
            lastRecoveryAt: lastRecoveryAt
        )
    }

    /// Quiet active-recording state. Current signal freshness comes from the
    /// location observer so standing still does not look like signal loss when
    /// jitter-filtered fixes are intentionally absent from the stored path.
    /// Recovery is derived from the path's existing timestamps.
    static func activeStatus(
        path: [GpsPoint],
        lastFixAt: Date?,
        now: Date = Date()
    ) -> ActiveStatus {
        let recordedFixAt = path.last(where: isValidTimestampedPoint)
            .map { Date(timeIntervalSince1970: $0[2] / 1000) }
        guard let freshestFixAt = lastFixAt ?? recordedFixAt else { return .waiting }
        if now.timeIntervalSince(freshestFixAt) > staleFixSeconds {
            return .paused
        }
        let validPointCount = path.lazy.filter(isValidTimestampedPoint).prefix(2).count
        guard validPointCount >= 2 else { return .waiting }
        if let recoveredAt = materialGapSummary(path)?.lastRecoveryAt {
            let age = now.timeIntervalSince(recoveredAt)
            if age >= 0, age <= recoveredDisplaySeconds {
                return .recovered
            }
        }
        return .good
    }

    private static func isValidTimestampedPoint(_ point: GpsPoint) -> Bool {
        point.count >= 3 && point[0].isFinite && point[1].isFinite && point[2].isFinite
    }

    private static func durationLabel(_ seconds: TimeInterval) -> String {
        let rounded = max(0, Int(seconds.rounded()))
        let hours = rounded / 3600
        let minutes = (rounded % 3600) / 60
        let remainingSeconds = rounded % 60
        if hours > 0 {
            return minutes > 0 ? "\(hours)h \(minutes)m" : "\(hours)h"
        }
        if minutes > 0 {
            return remainingSeconds > 0 ? "\(minutes)m \(remainingSeconds)s" : "\(minutes)m"
        }
        return "\(remainingSeconds)s"
    }
}
