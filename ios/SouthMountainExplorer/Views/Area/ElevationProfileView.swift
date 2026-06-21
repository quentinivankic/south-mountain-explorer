import SwiftUI
import Charts

/// Line chart of altitude vs cumulative distance, plus min/max
/// annotations. Embedded in `HikeDetailView`'s elevation section.
/// Heights and chart styling chosen to harmonize with the existing
/// `statsCard` and trail-list sections nearby. Both axes honor the
/// units toggle in Settings: imperial renders mi / ft, metric km / m.
struct ElevationProfileView: View {
    let stats: ElevationStats
    /// Hike's total distance in meters. Used to anchor the chart's
    /// X axis to the FULL hike length rather than the last elevation
    /// sample — without this the chart cuts off short when trailing
    /// GPS points lacked altitude (common with low signal at the end
    /// of a hike), so the visual line stops well before the right
    /// edge despite the user having walked further.
    let totalDistanceMeters: Double

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    /// X-axis domain pinned to the full hike length, with a fallback
    /// to the last sample so a sample-only call (e.g. a Preview) still
    /// renders something sensible.
    private var xDomain: ClosedRange<Double> {
        let maxSample = stats.samples.last?.distanceMeters ?? 0
        let upper = max(totalDistanceMeters, maxSample, 1)
        return 0...upper
    }

    var body: some View {
        Chart(stats.samples, id: \.distanceMeters) { sample in
            // Bound the area fill explicitly between the chart's
            // lower y-tick and the sample altitude. AreaMark with
            // just `y:` defaults the floor to y=0, which is far
            // below the visible domain — the chart usually clips
            // this, but on some renders the fill leaks past the
            // bottom of the plot frame. Using `yStart` / `yEnd` =
            // the displayed domain bounds keeps the green inside.
            AreaMark(
                x: .value("Distance", sample.distanceMeters),
                yStart: .value("Floor", yAxis.domain.lowerBound),
                yEnd: .value("Altitude", sample.altitudeMeters)
            )
            .foregroundStyle(
                LinearGradient(
                    colors: [.green.opacity(0.6), .green.opacity(0.05)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            .interpolationMethod(.monotone)

            LineMark(
                x: .value("Distance", sample.distanceMeters),
                y: .value("Altitude", sample.altitudeMeters)
            )
            .foregroundStyle(.green)
            .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            .interpolationMethod(.monotone)
        }
        .chartYScale(domain: yAxis.domain)
        .chartXScale(domain: xDomain)
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { value in
                AxisGridLine().foregroundStyle(.secondary.opacity(0.2))
                AxisValueLabel {
                    if let meters = value.as(Double.self) {
                        // Two decimals for short hikes, one for longer
                        // ones — mirrors UnitFormatter.distance rules
                        // but on bare numbers so tick labels stay tight.
                        let display = units == .imperial ? meters / 1609.344 : meters / 1000
                        Text(display < 1 ? String(format: "%.2f", display) : String(format: "%.1f", display))
                            .font(.caption2)
                    }
                }
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: yAxis.ticks) { value in
                AxisGridLine().foregroundStyle(.secondary.opacity(0.2))
                AxisValueLabel {
                    if let meters = value.as(Double.self) {
                        let display = units == .imperial ? meters * 3.28084 : meters
                        // .rounded() not Int() truncation: a tick is a
                        // round display-unit value converted to meters
                        // and back, so FP drift can land it at e.g.
                        // 999.9999 — truncation would print "999".
                        Text("\(Int(display.rounded()))")
                            .font(.caption2)
                    }
                }
            }
        }
        // No baked-in height — callers size it. HikeDetailView uses
        // 160 for the full post-hike chart; the live recording strip
        // uses ~80 to fit alongside the stats row.
    }

    /// Computed Y-axis ticks + domain. Picks a "nice" step in the
    /// display unit (feet) that yields ~4 ticks across the data
    /// range, then rounds the bounds OUTWARD to the nearest step
    /// multiple. The domain is the resulting tick range — guarantees
    /// every tick label sits inside the visible plot and the line
    /// never extends past a labeled tick. Replaces `AxisMarks(.
    /// automatic)` which silently picked ticks INSIDE the data range
    /// (e.g. ticks 1377/1410/1443 ft for a 1357–1466 ft hike, so the
    /// chart line legitimately drew above the top label).
    private var yAxis: (domain: ClosedRange<Double>, ticks: [Double]) {
        // Pick "nice" tick values in the DISPLAY unit so gridlines
        // land on round numbers the user actually sees — 300 m, not
        // 305 m (which is what you get rounding feet then converting).
        // All math is done in display units, then the domain + ticks
        // are converted back to meters for the chart (which always
        // plots raw meters; the axis label closure re-applies the
        // unit). `unitPerMeter` is the display-unit-per-meter factor.
        let unitPerMeter = units == .imperial ? 3.28084 : 1.0
        let minU = stats.minAltitudeMeters * unitPerMeter
        let maxU = stats.maxAltitudeMeters * unitPerMeter
        let span = maxU - minU

        // Flat hike fallback — center ± a couple of steps so the
        // line doesn't render as a single pixel at one tick.
        if span < 1 {
            let centerU = minU
            let step = 10.0
            let ticksU = [centerU - step, centerU, centerU + step]
            let ticksM = ticksU.map { $0 / unitPerMeter }
            return (domain: ticksM.first!...ticksM.last!, ticks: ticksM)
        }

        // Candidate steps in display units. Metric and imperial use
        // the same round-number ladder — both ft and m read naturally
        // at 5 / 10 / 25 / 50 / 100 / … increments.
        let candidates: [Double] = [5, 10, 25, 50, 100, 250, 500, 1000]
        let targetTicks = 4.0
        let step = candidates.first(where: { span / $0 <= targetTicks + 1 }) ?? 1000.0
        let minTickU = (minU / step).rounded(.down) * step
        let maxTickU = (maxU / step).rounded(.up) * step
        var ticksU: [Double] = []
        var t = minTickU
        while t <= maxTickU + 0.001 {
            ticksU.append(t)
            t += step
        }
        let ticksM = ticksU.map { $0 / unitPerMeter }
        return (ticksM.first!...ticksM.last!, ticksM)
    }
}
