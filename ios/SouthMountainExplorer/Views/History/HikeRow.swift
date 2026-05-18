import SwiftUI

struct HikeRow: View {
    let hike: SavedRecording
    let areaName: String
    let trailName: String?

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    private var dateString: String {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f.string(from: hike.startedAt)
    }

    private var durationString: String {
        let h = hike.durationSeconds / 3600
        let m = (hike.durationSeconds % 3600) / 60
        if h > 0 { return "\(h)h \(m)m" }
        return "\(m)m"
    }

    private var titleText: String {
        if let trailName { return trailName }
        return areaName
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(titleText)
                    .font(.headline)
                Spacer()
                Text(dateString)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // When the title is the trail name, surface the area below
            // so the user has the geographic context.
            if trailName != nil {
                Text(areaName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 20) {
                Label(UnitFormatter.distance(miles: hike.distanceMi, units: units), systemImage: "figure.walk")
                Label(durationString, systemImage: "clock")
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)

            // Completion summary: separate "newly completed" from
            // "previously completed" (re-walked), so a second hike of the
            // same trail doesn't read as 0 trails done.
            if !hike.completedTrailIds.isEmpty || !hike.revisitedTrailIds.isEmpty {
                HStack(spacing: 14) {
                    if !hike.completedTrailIds.isEmpty {
                        Label("\(hike.completedTrailIds.count) newly completed",
                              systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                    }
                    if !hike.revisitedTrailIds.isEmpty {
                        Label("\(hike.revisitedTrailIds.count) revisited",
                              systemImage: "arrow.clockwise.circle.fill")
                            .foregroundStyle(.cyan)
                    }
                }
                .font(.caption)
            }
        }
        .padding(.vertical, 4)
    }
}
