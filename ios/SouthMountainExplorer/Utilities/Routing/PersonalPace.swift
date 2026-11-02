import Foundation

/// Outcome of measuring the user's own walking pace from saved hikes.
struct PersonalPaceResult: Equatable, Sendable {
    /// Speed multiplier vs the default grade-aware (Tobler) model, with the
    /// SAME meaning as `PaceModel.Result.factor` and the `paceFactor`
    /// parameter of `TrailNetworkRouting`: Tobler-expected time / observed time,
    /// applied as `seconds = tobler seconds / factor`. > 1 = faster than the
    /// default, < 1 = slower. Clamped to `PaceModel.factorRange`.
    let factor: Double
    /// Moving seconds the factor was measured over (after recency selection,
    /// before outlier trimming).
    let movingSeconds: Double
    /// Hikes that contributed at least one measurable chunk.
    let hikesUsed: Int

    var movingMinutes: Double { movingSeconds / 60 }

    /// Plain-language explanation for the planner UI.
    var summary: String {
        // Percentages are in TIME terms: slower = extra time vs default,
        // faster = time saved vs default.
        let timeRatio = 1 / factor
        let pct = Int(((timeRatio - 1) * 100).rounded())
        let lead: String
        if abs(pct) <= 2 {
            lead = "About the same as the default"
        } else if pct > 0 {
            lead = "\(pct)% slower than the default"
        } else {
            lead = "\(Int(((1 - timeRatio) * 100).rounded()))% faster than the default"
        }
        let moving = movingSeconds >= 3600
            ? String(format: "%.1f h", movingSeconds / 3600)
            : "\(Int((movingSeconds / 60).rounded())) min"
        return "\(lead), from \(hikesUsed) hike\(hikesUsed == 1 ? "" : "s") / \(moving) of moving time"
    }
}

/// Measures "my pace" from saved hikes. Pure and deterministic (the clock is
/// injected via `now`).
///
/// Method (mirrors `PaceModel.evaluate`, over whole hikes):
///  - Hikes: roam and trail recordings only. Walk-mode recordings are excluded
///    (urban, stop-and-go sidewalks; not representative of trail pace).
///  - Recency: hikes that started within the last `recencyDays` (90) days, newest
///    `maxHikes` (20) only. Each hike is weighted by 0.5^(ageDays / halfLifeDays)
///    with a 30-day half-life, so recent hikes count more.
///  - Moving pairs only: consecutive fixes 0 < dt <= 20 s apart and
///    0.3...6 m/s. Anything else (stops, gaps, GPS jumps) ends the chunk.
///  - Chunks of >= 50 m (chunks under 25 m are dropped). Grade comes from
///    recorded altitude at the chunk ends; 0 when altitude is missing (older
///    3-element points). Chunks implying |grade| > 0.5 are dropped as altitude
///    noise.
///  - Aggregate: sort chunks by their own pace ratio, drop the fastest and
///    slowest 10% (when there are >= 10 chunks), then factor =
///    sum(expected * w) / sum(observed * w), i.e. time-weighted.
///  - Result needs >= `minMovingSeconds` (30 min) over >= `minHikes` (2) hikes,
///    otherwise `nil` and the planner keeps the default model.
enum PersonalPace {
    static let recencyDays: Double = 90
    static let maxHikes = 20
    static let halfLifeDays: Double = 30
    static let minMovingSeconds: Double = 1800
    static let minHikes = 2
    static let minChunkMeters: Double = 25
    static let maxAbsChunkGrade: Double = 0.5
    static let trimFraction: Double = 0.10

    private struct Chunk {
        let expected: Double
        let observed: Double
        let weight: Double
        var ratio: Double { expected / observed }
    }

    static func estimate(from recordings: [SavedRecording], now: Date) -> PersonalPaceResult? {
        let day = 86_400.0
        let eligible = recordings
            .filter { $0.mode != .walk }
            .filter { now.timeIntervalSince($0.startedAt) <= recencyDays * day }
            // Deterministic order: newest first, id breaks ties.
            .sorted { $0.startedAt != $1.startedAt ? $0.startedAt > $1.startedAt : $0.id < $1.id }
            .prefix(maxHikes)

        var all: [Chunk] = []
        var hikes = 0
        for rec in eligible {
            let ageDays = max(0, now.timeIntervalSince(rec.startedAt)) / day
            let w = pow(0.5, ageDays / halfLifeDays)
            let found = chunks(from: rec.path, weight: w)
            if !found.isEmpty { hikes += 1; all += found }
        }
        guard hikes >= minHikes else { return nil }
        let moving = all.reduce(0) { $0 + $1.observed }
        guard moving >= minMovingSeconds else { return nil }

        let sorted = all.sorted { $0.ratio < $1.ratio }
        let drop = sorted.count >= 10 ? Int((Double(sorted.count) * trimFraction).rounded(.down)) : 0
        let kept = sorted[drop..<(sorted.count - drop)]
        let exp = kept.reduce(0) { $0 + $1.expected * $1.weight }
        let obs = kept.reduce(0) { $0 + $1.observed * $1.weight }
        guard exp > 0, obs > 0 else { return nil }
        let factor = min(PaceModel.factorRange.upperBound,
                         max(PaceModel.factorRange.lowerBound, exp / obs))
        return PersonalPaceResult(factor: factor, movingSeconds: moving, hikesUsed: hikes)
    }

    /// Moving chunks of one hike, walking forward in time.
    private static func chunks(from path: [GpsPoint], weight: Double) -> [Chunk] {
        guard path.count >= 2 else { return [] }
        var out: [Chunk] = []
        var len = 0.0, dt = 0.0
        var altStart: Double? = nil, altEnd: Double? = nil

        func flush() {
            defer { len = 0; dt = 0; altStart = nil; altEnd = nil }
            guard len >= minChunkMeters, dt > 0 else { return }
            var grade = 0.0
            if let a = altStart, let b = altEnd {
                grade = (b - a) / len
                if !grade.isFinite || abs(grade) > maxAbsChunkGrade { return }
            }
            out.append(Chunk(expected: len / PaceModel.tobler(grade: grade),
                             observed: dt, weight: weight))
        }

        for i in 1..<path.count {
            let a = path[i - 1], b = path[i]
            guard a.count >= 3, b.count >= 3 else { flush(); continue }
            let seconds = (b[2] - a[2]) / 1000
            guard seconds > 0, seconds <= GpsIngest.gapMs / 1000 else { flush(); continue }
            let d = MapMath.haversineMeters(lat1: a[0], lon1: a[1], lat2: b[0], lon2: b[1])
            let v = d / seconds
            guard v >= PaceModel.minMovingSpeed, v <= PaceModel.maxMovingSpeed else { flush(); continue }
            if len == 0 { altStart = a.altitudeMeters }
            altEnd = b.altitudeMeters
            len += d
            dt += seconds
            if len >= PaceModel.chunkMeters { flush() }
        }
        flush()
        return out
    }

    /// App adapter: loads history through the existing off-main store path
    /// (`RecordingService.loadHistory`), then measures off the main actor.
    /// `nil` means insufficient history (or unreadable history): use the default.
    @MainActor
    static func load(using recording: RecordingService, now: Date = Date()) async -> PersonalPaceResult? {
        let history = await recording.loadHistory()
        return await Task.detached(priority: .userInitiated) {
            estimate(from: history, now: now)
        }.value
    }
}
