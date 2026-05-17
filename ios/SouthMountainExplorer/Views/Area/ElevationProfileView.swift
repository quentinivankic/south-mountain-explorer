import SwiftUI
import Charts

/// Line chart of altitude vs cumulative distance, plus min/max
/// annotations. Embedded in `HikeDetailView`'s elevation section.
/// Heights and chart styling chosen to harmonize with the existing
/// `statsCard` and trail-list sections nearby.
///
/// Display unit is meters for both axes — the parent caller is
/// expected to format axis labels through `UnitFormatter` once the
/// imperial / metric toggle (PR C) lands. For now we render raw
/// numbers; the visual shape is what matters at this stage.
struct ElevationProfileView: View {
    let stats: ElevationStats

    var body: some View {
        Chart(stats.samples, id: \.distanceMeters) { sample in
            AreaMark(
                x: .value("Distance", sample.distanceMeters),
                y: .value("Altitude", sample.altitudeMeters)
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
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { value in
                AxisGridLine().foregroundStyle(.secondary.opacity(0.2))
                AxisValueLabel {
                    if let meters = value.as(Double.self) {
                        // Distance labels in miles (display unit will
                        // become user-toggleable in PR C). Two decimals
                        // for short hikes, zero for longer ones.
                        let miles = meters / 1609.344
                        Text(miles < 1 ? String(format: "%.2f", miles) : String(format: "%.1f", miles))
                            .font(.caption2)
                    }
                }
            }
        }
        .chartYAxis {
            AxisMarks(values: yAxis.ticks) { value in
                AxisGridLine().foregroundStyle(.secondary.opacity(0.2))
                AxisValueLabel {
                    if let meters = value.as(Double.self) {
                        // Altitude labels in feet. PR C swaps for the
                        // unit toggle.
                        let feet = meters * 3.28084
                        Text("\(Int(feet))")
                            .font(.caption2)
                    }
                }
            }
        }
        .frame(height: 160)
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
        let minFt = stats.minAltitudeMeters * 3.28084
        let maxFt = stats.maxAltitudeMeters * 3.28084
        let span = maxFt - minFt

        // Flat hike fallback — center ± a couple of steps so the
        // line doesn't render as a single pixel at one tick.
        if span < 1 {
            let centerFt = minFt
            let step = 10.0
            let ticksFt = [centerFt - step, centerFt, centerFt + step]
            let ticksM = ticksFt.map { $0 / 3.28084 }
            return (domain: ticksM.first!...ticksM.last!, ticks: ticksM)
        }

        let candidates: [Double] = [5, 10, 25, 50, 100, 250, 500, 1000]
        let targetTicks = 4.0
        let step = candidates.first(where: { span / $0 <= targetTicks + 1 }) ?? 1000.0
        let minTickFt = (minFt / step).rounded(.down) * step
        let maxTickFt = (maxFt / step).rounded(.up) * step
        var ticksFt: [Double] = []
        var t = minTickFt
        while t <= maxTickFt + 0.001 {
            ticksFt.append(t)
            t += step
        }
        let ticksM = ticksFt.map { $0 / 3.28084 }
        return (ticksM.first!...ticksM.last!, ticksM)
    }
}
