import SwiftUI

/// Square share card rendered to an image for the system share sheet.
/// Kept text-only (no Map content) because SwiftUI's ImageRenderer can't
/// snapshot live MapKit content reliably.
struct ShareableHikeCard: View {
    let hike: SavedRecording
    let areaName: String

    var body: some View {
        ZStack(alignment: .topLeading) {
            LinearGradient(
                colors: [.cyan, .indigo],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Image(systemName: "mountain.2.fill")
                        .font(.title2)
                    Text("South Mountain Explorer")
                        .font(.headline)
                }
                .foregroundStyle(.white.opacity(0.92))

                Spacer()

                Text(areaName)
                    .font(.system(size: 56, weight: .bold))
                    .foregroundStyle(.white)
                    .lineLimit(2)

                Spacer().frame(height: 24)

                HStack(spacing: 28) {
                    stat(value: String(format: "%.2f", hike.distanceMi), unit: "mi", label: "Distance")
                    stat(value: durationValue, unit: durationUnit, label: "Duration")
                    if !hike.completedTrailIds.isEmpty {
                        stat(value: "\(hike.completedTrailIds.count)",
                             unit: hike.completedTrailIds.count == 1 ? "trail" : "trails",
                             label: "Completed")
                    }
                }

                Spacer().frame(height: 16)

                Text(dateString)
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.75))
            }
            .padding(48)
        }
        .frame(width: 1080, height: 1080)
    }

    private func stat(value: String, unit: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .lastTextBaseline, spacing: 4) {
                Text(value)
                    .font(.system(size: 40, weight: .bold).monospacedDigit())
                    .foregroundStyle(.white)
                Text(unit)
                    .font(.headline)
                    .foregroundStyle(.white.opacity(0.85))
            }
            Text(label.uppercased())
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.7))
                .tracking(1)
        }
    }

    private var durationValue: String {
        let h = hike.durationSeconds / 3600
        let m = (hike.durationSeconds % 3600) / 60
        if h > 0 { return "\(h):\(String(format: "%02d", m))" }
        return "\(m)"
    }

    private var durationUnit: String {
        hike.durationSeconds >= 3600 ? "h" : "min"
    }

    private var dateString: String {
        let f = DateFormatter()
        f.dateStyle = .long
        f.timeStyle = .none
        return f.string(from: hike.startedAt)
    }
}
