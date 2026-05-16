import SwiftUI
import Charts

/// Line chart of altitude vs cumulative distance, plus min/max
/// annotations. Embedded in `HikeDetailView`'s elevation section.
/// Heights and chart styling chosen to harmonize with the existing
/// `statsCard` and trail-list sections nearby. Both axes honor the
/// units toggle in Settings: imperial renders mi / ft, metric km / m.
struct ElevationProfileView: View {
    let stats: ElevationStats

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

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
                        // Two decimals for short hikes, one for
                        // longer ones — matches UnitFormatter.distance
                        // rules but on bare numbers so the tick
                        // labels stay tight.
                        let display = units == .imperial ? meters / 1609.344 : meters / 1000
                        Text(display < 1 ? String(format: "%.2f", display) : String(format: "%.1f", display))
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
                        let display = units == .imperial ? meters * 3.28084 : meters
                        Text("\(Int(display))")
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
