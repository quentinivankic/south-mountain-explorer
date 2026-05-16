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
        .chartYScale(domain: yDomain)
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
            AxisMarks(values: .automatic(desiredCount: 4)) { value in
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

    /// Tight Y range plus 5% headroom on each side so the line never
    /// kisses the top or bottom of the plot area. Falls back to a
    /// fixed ±10 m window when the hike was perfectly flat (rare —
    /// even GPS noise produces a few meters of spread).
    private var yDomain: ClosedRange<Double> {
        let span = stats.maxAltitudeMeters - stats.minAltitudeMeters
        if span < 1 {
            let center = stats.minAltitudeMeters
            return (center - 10)...(center + 10)
        }
        let pad = span * 0.05
        return (stats.minAltitudeMeters - pad)...(stats.maxAltitudeMeters + pad)
    }
}
