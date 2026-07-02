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
    var raw: [(distance: Double, altitude: Double)] = []
    for i in 0..<path.count {
        let p = path[i]
        guard p.count >= 2 else { continue }
        if i > 0 {
            let prev = path[i - 1]
            if prev.count >= 2 {
                cumulativeDistance += haversineDistanceM(
                    lat1: prev[0], lon1: prev[1],
                    lat2: p[0], lon2: p[1]
                )
            }
        }
        if let altitude = p.altitudeMeters {
            raw.append((cumulativeDistance, altitude))
        }
    }
    guard !raw.isEmpty else { return nil }

    // Smooth altitudes via a centered moving average. Edges use a
    // shrinking window (so the first and last samples still appear).
    var samples: [ElevationStats.Sample] = []
    samples.reserveCapacity(raw.count)
    for i in 0..<raw.count {
        let half = smoothingWindow / 2
        let lo = max(0, i - half)
        let hi = min(raw.count - 1, i + half)
        var sum = 0.0
        for j in lo...hi { sum += raw[j].altitude }
        let avg = sum / Double(hi - lo + 1)
        samples.append(.init(distanceMeters: raw[i].distance, altitudeMeters: avg))
    }

    // Ascent / descent: sum positive / negative deltas of the
    // smoothed series. Pre-smoothing, GPS noise routinely produces
    // "1500 ft of climb" on a flat walk; the moving average gets
    // that down to single-digit feet for flat ground.
    var ascent: Double = 0
    var descent: Double = 0
    for i in 1..<samples.count {
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
