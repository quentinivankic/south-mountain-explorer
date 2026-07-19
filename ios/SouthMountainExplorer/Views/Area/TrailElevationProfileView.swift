import SwiftUI
import Charts

/// Elevation profile for a trail you're CONSIDERING (or standing on), drawn
/// from the baked `Trail.profileFt` series. The sibling of
/// `ElevationProfileView`, which charts a hike you already recorded.
///
/// The two are deliberately separate rather than one generalised view:
/// `ElevationProfileView` plots irregular GPS samples in metres against real
/// cumulative distance, while this plots an evenly-spaced series in feet
/// against trail length, and adds the "you are here" marker. Merging them
/// would mean a view with two unit systems and two x-axis meanings.
///
/// **Direction comes from the user, not the data.** `profileFt` follows
/// arbitrary OSM way order, so the caller passes `startIsNearer` — is the
/// stored start the trail end closer to them — and the series is drawn from
/// that end. It orients with or without a `position`, so a browsed profile
/// still opens at the end you'd set off from; `position` only adds the marker.
/// See CLAUDE.md, "Trail elevation profiles — the direction problem".
struct TrailElevationProfileView: View {
    /// Elevations in feet, evenly spaced by distance (`Trail.profileFt`).
    let profileFt: [Int]
    /// Trail length, used for the x-axis so ticks read in real distance.
    let totalDistanceMi: Double
    /// Where the hiker is along the STORED series, 0…1. nil = not on trail.
    var position: Double? = nil
    /// Is the stored START the trail end nearer the user? Latched by the caller
    /// at open — see `TrailRow.profileStartIsNearer` for why it must not change
    /// while the chart is up.
    var startIsNearer: Bool = true

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    /// Series + marker oriented into the hiker's frame of reference. Both flip
    /// together, so the elevation under the marker never changes on a re-draw.
    private var view: (samples: [Int], fraction: Double?) {
        // The series is oriented whether or not we have a position: direction
        // comes from which trail END is nearer, so a browsed profile still
        // opens at the end you'd set off from.
        let samples = startIsNearer ? profileFt : profileFt.reversed()
        guard let position else { return (samples, nil) }
        let o = TrailProfile.oriented(profileFt, fraction: position,
                                      startIsNearer: startIsNearer)
        return (o.samples, o.fraction)
    }

    /// Distance in metres at a sample index. The series is evenly spaced, so
    /// index maps linearly onto trail length — no stored distance array.
    private func distanceMeters(at index: Int, count: Int) -> Double {
        guard count > 1 else { return 0 }
        return totalMeters * Double(index) / Double(count - 1)
    }

    private var totalMeters: Double { max(totalDistanceMi * 1609.344, 1) }

    var body: some View {
        let samples = view.samples
        Chart {
            // Positional identity for the same reason ElevationProfileView
            // uses it: repeated elevation values must not collide into one
            // mark and draw stray segments across the profile.
            ForEach(Array(samples.enumerated()), id: \.offset) { item in
                let x = distanceMeters(at: item.offset, count: samples.count)
                let y = Double(item.element)

                AreaMark(
                    x: .value("Distance", x),
                    yStart: .value("Floor", yAxis.domain.lowerBound),
                    yEnd: .value("Elevation", y)
                )
                .foregroundStyle(
                    LinearGradient(colors: [.green.opacity(0.6), .green.opacity(0.05)],
                                   startPoint: .top, endPoint: .bottom)
                )
                .interpolationMethod(.monotone)

                LineMark(
                    x: .value("Distance", x),
                    y: .value("Elevation", y)
                )
                .foregroundStyle(.green)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.monotone)
            }

            // "You are here". Drawn last so it sits above the fill.
            if let f = view.fraction,
               let elevation = TrailProfile.elevationFt(samples, at: f) {
                let x = totalMeters * f
                RuleMark(x: .value("You", x))
                    .foregroundStyle(.orange)
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                PointMark(
                    x: .value("You", x),
                    y: .value("Elevation", elevation)
                )
                .foregroundStyle(.orange)
                .symbolSize(90)
            }
        }
        .chartYScale(domain: yAxis.domain)
        .chartXScale(domain: 0...totalMeters)
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { value in
                AxisGridLine().foregroundStyle(.secondary.opacity(0.2))
                AxisValueLabel {
                    if let meters = value.as(Double.self) {
                        let display = units == .imperial ? meters / 1609.344 : meters / 1000
                        Text(display < 1 ? String(format: "%.2f", display)
                                         : String(format: "%.1f", display))
                            .font(.caption2)
                    }
                }
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: yAxis.ticks) { value in
                AxisGridLine().foregroundStyle(.secondary.opacity(0.2))
                AxisValueLabel {
                    if let ft = value.as(Double.self) {
                        // The series is in FEET; metric converts at display.
                        let display = units == .imperial ? ft : ft / 3.28084
                        Text("\(Int(display.rounded()))")
                            .font(.caption2)
                    }
                }
            }
        }
        // No baked-in height — callers size it, matching ElevationProfileView.
    }

    /// Nice ticks in FEET (the series' native unit), rounded outward so the
    /// line never draws above the top label. Same approach as
    /// `ElevationProfileView.yAxis`, but the source data is already feet, so
    /// there's no metres round-trip.
    private var yAxis: (domain: ClosedRange<Double>, ticks: [Double]) {
        let lo = Double(profileFt.min() ?? 0)
        let hi = Double(profileFt.max() ?? 0)
        let span = hi - lo

        // Flat trail: centre it so the line isn't a single pixel on one tick.
        if span < 1 {
            let step = 10.0
            return elevationAxisDomain(ticks: [lo - step, lo, lo + step],
                                       fallbackBase: lo)
        }
        let candidates: [Double] = [5, 10, 25, 50, 100, 250, 500, 1000]
        let step = candidates.first(where: { span / $0 <= 5.0 }) ?? 1000.0
        let minTick = (lo / step).rounded(.down) * step
        let maxTick = (hi / step).rounded(.up) * step
        var ticks: [Double] = []
        var t = minTick
        while t <= maxTick + 0.001 {
            ticks.append(t)
            t += step
        }
        return elevationAxisDomain(ticks: ticks, fallbackBase: lo)
    }
}
