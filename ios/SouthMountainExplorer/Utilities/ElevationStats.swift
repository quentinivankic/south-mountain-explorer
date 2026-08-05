import Foundation

/// Summary stats for a hike's elevation track. All fields are in
/// meters; converted to feet at display time by `UnitFormatter`.
/// `samples` is the smoothed (cumulativeDistanceMeters, altitudeMeters)
/// pairs suitable for plotting an elevation profile chart.
///
/// `nil` instead of an empty struct when no point in the path has
/// altitude data — distinguishes "we have no data" from "we have
/// data and it's flat at sea level."
struct ElevationStats: Equatable {
    let totalAscentMeters: Double
    let totalDescentMeters: Double
    let maxAltitudeMeters: Double
    let minAltitudeMeters: Double
    let samples: [Sample]

    struct Sample: Equatable {
        let distanceMeters: Double
        let altitudeMeters: Double
        /// Which continuous run this sample belongs to. Samples in different
        /// runs are plotted as separate line series so the profile doesn't
        /// draw a slope across a recording gap (screen locked / lost signal).
        let run: Int
    }
}

/// Window size for the moving-average smoothing pass. iPhone GPS
/// altitude is jittery (±5-10m between adjacent samples even when
/// stationary). Five-sample smoothing collapses most of that into
/// the trend without losing real climb detail at typical hiking pace
/// (1 sample / 2 s × 5 = 10 s windows ≈ ~12 m of horizontal travel).
private let smoothingWindow = 5

/// Compute elevation stats for a GPS path. Returns nil when no point
/// in the path carries altitude data (e.g., a pre-feature hike or
/// every fix had bad vertical accuracy).
func elevationStats(path: [GpsPoint]) -> ElevationStats? {
    // Pull the (cumulativeDistance, smoothedAltitude) sequence in one
    // pass. Distance accumulates across all points regardless of
    // altitude availability so the x-axis matches the actual hike;
    // altitude is only contributed by points that have it, but we
    // ignore the smoothing details here — see below.

    // First: collect raw altitude samples paired with cumulative
    // distance. Points without altitude are skipped — they leave a
    // gap in the elevation series. For a typical hike with mostly-
    // valid altitudes this is essentially every point.
    var cumulativeDistance: Double = 0
    var runIdx = 0
    var raw: [(distance: Double, altitude: Double, run: Int)] = []
    for i in 0..<path.count {
        let p = path[i]
        guard p.count >= 2 else { continue }
        if i > 0 {
            let prev = path[i - 1]
            if prev.count >= 2 {
                if GpsIngest.isGap(prev: prev, p: p) {
                    // Recording gap (screen locked / lost signal): start a new
                    // run and nudge x by a small separator instead of crediting
                    // the straight-line jump — that jump isn't distance the
                    // hiker walked and would inflate the axis and draw a false
                    // slope. The chart breaks the line between runs.
                    runIdx += 1
                    cumulativeDistance += GpsIngest.runSeparatorMeters
                } else {
                    cumulativeDistance += haversineDistanceM(
                        lat1: prev[0], lon1: prev[1],
                        lat2: p[0], lon2: p[1]
                    )
                }
            }
        }
        if let altitude = p.altitudeMeters {
            // Only keep samples that actually advance along the hike, so
            // standing at the trailhead (several fixes at one spot) doesn't
            // stack equal-x samples. De-dupe WITHIN a run only — the first
            // sample of a new run must always survive.
            if let last = raw.last, last.run == runIdx,
               cumulativeDistance - last.distance < 0.5 {
                continue
            }
            raw.append((cumulativeDistance, altitude, runIdx))
        }
    }
    guard !raw.isEmpty else { return nil }

    // Smooth altitudes via a centered moving average, clamped to the current
    // run so the average never blends across a gap. Edges use a shrinking
    // window (so the first and last samples of each run still appear).
    var samples: [ElevationStats.Sample] = []
    samples.reserveCapacity(raw.count)
    for i in 0..<raw.count {
        let half = smoothingWindow / 2
        var lo = max(0, i - half)
        var hi = min(raw.count - 1, i + half)
        while lo < i && raw[lo].run != raw[i].run { lo += 1 }
        while hi > i && raw[hi].run != raw[i].run { hi -= 1 }
        var sum = 0.0
        for j in lo...hi { sum += raw[j].altitude }
        let avg = sum / Double(hi - lo + 1)
        samples.append(.init(distanceMeters: raw[i].distance, altitudeMeters: avg, run: raw[i].run))
    }

    // Ascent / descent: sum positive / negative deltas of the smoothed
    // series, but never count the altitude change ACROSS a gap — that
    // happened while not recording and isn't climb the hiker did on trail.
    var ascent: Double = 0
    var descent: Double = 0
    for i in 1..<samples.count where samples[i].run == samples[i - 1].run {
        let d = samples[i].altitudeMeters - samples[i - 1].altitudeMeters
        if d > 0 { ascent += d } else { descent += -d }
    }

    let altitudes = samples.map(\.altitudeMeters)
    return ElevationStats(
        totalAscentMeters: ascent,
        totalDescentMeters: descent,
        maxAltitudeMeters: altitudes.max() ?? 0,
        minAltitudeMeters: altitudes.min() ?? 0,
        samples: samples
    )
}

/// Safe `(domain, ticks)` for the elevation chart's Y axis. Guards the
/// empty / non-finite case so `ElevationProfileView` can't crash
/// force-unwrapping `ticks.first!` / `.last!` on degenerate altitude
/// data (e.g. a hike whose GPS reported NaN altitudes — the tick loop
/// would then produce an empty array). Falls back to a tiny valid
/// domain so the chart renders flat instead of trapping.
func elevationAxisDomain(ticks: [Double],
                         fallbackBase: Double) -> (domain: ClosedRange<Double>, ticks: [Double]) {
    guard let lo = ticks.first, let hi = ticks.last,
          lo.isFinite, hi.isFinite, lo <= hi else {
        let base = fallbackBase.isFinite ? fallbackBase : 0
        return (base...(base + 1), [])
    }
    return (lo...hi, ticks)
}
