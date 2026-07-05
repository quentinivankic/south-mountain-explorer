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

    /// Walk-aware completion counts: a walk's per-area credits live in
    /// the multi-area dicts (its flat arrays only mirror the primary
    /// area), so sum across areas; regular hikes read the flat arrays.
    private var completedCount: Int {
        if let m = hike.multiAreaCompletions {
            return m.values.map(\.count).reduce(0, +)
        }
        return hike.completedTrailIds.count
    }

    private var revisitedCount: Int {
        if let r = hike.multiAreaRevisited {
            return r.values.map(\.count).reduce(0, +)
        }
        return hike.revisitedTrailIds.count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                if hike.isWalk {
                    Text("Walk")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.accentColor, in: Capsule())
                }
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
            if completedCount > 0 || revisitedCount > 0 {
                HStack(spacing: 14) {
                    if completedCount > 0 {
                        Label("\(completedCount) newly completed",
                              systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                    }
                    if revisitedCount > 0 {
                        Label("\(revisitedCount) revisited",
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
