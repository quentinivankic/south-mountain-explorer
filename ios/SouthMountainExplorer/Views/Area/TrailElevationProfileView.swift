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
    /// Compass name for the end the chart starts from, e.g. "west end". nil on
    /// loops, whose ends coincide — the label is then omitted rather than
    /// inventing a direction.
    var startEndLabel: String? = nil
    /// `Trail.profileGaps` — `[[sampleIndex, gapMetres], ...]` where the trail
    /// does not actually join. Empty for the overwhelming majority.
    var profileGaps: [[Int]] = []
    /// Called when the user flips the direction. nil hides the control — the
    /// chart is also used where flipping has no meaning.
    var onFlip: (() -> Void)? = nil

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    /// Gap sample-indices in the CURRENTLY DRAWN orientation.
    ///
    /// The series flips with `startIsNearer`, so a gap baked at index i sits at
    /// `count - 1 - i` when reversed. Getting this wrong would put the break at
    /// the mirror image of the real discontinuity — visible only on flipped
    /// trails, which is exactly the sort of thing that hides.
    private var orientedGaps: [(index: Int, metres: Int)] {
        let n = profileFt.count
        guard n > 1 else { return [] }
        return profileGaps.compactMap { g in
            guard g.count == 2, g[0] > 0, g[0] < n else { return nil }
            return (startIsNearer ? g[0] : n - g[0], g[1])
        }
    }

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
        VStack(alignment: .leading, spacing: 2) {
            if onFlip != nil {
                // Names the left edge, which the chart alone never did — the
                // series orients by whichever trail END is nearest you, and
                // browsing from home that is near-arbitrary with nothing on
                // screen to say which way it went. The label states the
                // convention; the button lets you set it when you know better
                // ("I'm parking at THAT end").
                HStack(spacing: 6) {
                    // Names the physical end by compass direction. The earlier
                    // "nearest end" described the algorithm instead of answering
                    // the question — ambiguous about nearest to WHAT, silent on
                    // which end that is, and weakest when browsing from far
                    // away. A loop has no distinguishable ends, so it falls back
                    // to the neutral wording rather than inventing a direction.
                    Text(startEndLabel.map { "Starts: \($0)" } ?? "Start of trail")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                    Button(action: { onFlip?() }) {
                        Label("Flip", systemImage: "arrow.left.arrow.right")
                            .font(.caption2)
                            .labelStyle(.titleAndIcon)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.tint)
                    .accessibilityLabel("Flip profile direction")
                    .accessibilityHint("Draws the profile from the other end of the trail")
                }
                .padding(.bottom, 1)
            }
            chart
        }
    }

    /// Which contiguous run a sample belongs to. Charts connects marks within a
    /// series, so bumping the series at each gap is what breaks the line there.
    private func runIndex(for sampleIndex: Int) -> Int {
        orientedGaps.reduce(0) { $0 + (sampleIndex >= $1.index ? 1 : 0) }
    }

    private var chart: some View {
        let samples = view.samples
        return Chart {
            // Positional identity for the same reason ElevationProfileView
            // uses it: repeated elevation values must not collide into one
            // mark and draw stray segments across the profile.
            ForEach(Array(samples.enumerated()), id: \.offset) { item in
                let x = distanceMeters(at: item.offset, count: samples.count)
                let y = Double(item.element)

                AreaMark(
                    x: .value("Distance", x),
                    yStart: .value("Floor", yAxis.domain.lowerBound),
                    yEnd: .value("Elevation", y),
                    // `series` splits the marks into independent runs at each
                    // gap, so Charts stops connecting across a discontinuity
                    // instead of drawing a line you cannot walk.
                    series: .value("Run", runIndex(for: item.offset))
                )
                .foregroundStyle(
                    LinearGradient(colors: [.green.opacity(0.6), .green.opacity(0.05)],
                                   startPoint: .top, endPoint: .bottom)
                )
                .interpolationMethod(.monotone)

                LineMark(
                    x: .value("Distance", x),
                    y: .value("Elevation", y),
                    series: .value("Run", runIndex(for: item.offset))
                )
                .foregroundStyle(.green)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.monotone)
            }

            // Mark each discontinuity. The gap takes no x — distanceMi excludes
            // it — so it is a boundary, not a span: a muted dashed rule at the
            // seam, with a distance only when the gap is big enough to be worth
            // words. Without this the two runs sit flush and read as one trail.
            ForEach(Array(orientedGaps.enumerated()), id: \.offset) { g in
                let x = distanceMeters(at: g.element.index, count: samples.count)
                RuleMark(x: .value("Gap", x))
                    .foregroundStyle(.secondary.opacity(0.55))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [2, 3]))
                    .annotation(position: .top, alignment: .center, spacing: 0) {
                        if g.element.metres >= 1609 {
                            Text(UnitFormatter.distance(meters: Double(g.element.metres),
                                                        units: units))
                                .font(.system(size: 9))
                                .foregroundStyle(.secondary)
                        }
                    }
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
