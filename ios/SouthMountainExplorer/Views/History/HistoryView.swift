import SwiftUI

struct HistoryView: View {
    @Environment(RecordingService.self) private var recording
    @Environment(AreaDataService.self) private var areas

    @State private var hikes: [SavedRecording] = []
    @State private var isLoading = false

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("Loading hikes...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if hikes.isEmpty {
                    emptyState
                } else {
                    hikeList
                }
            }
            .navigationTitle("History")
            .task { await loadHikes() }
            .refreshable { await loadHikes() }
        }
    }

    private var hikeList: some View {
        List {
            ForEach(hikes) { hike in
                NavigationLink {
                    HikeDetailView(hike: hike, areaName: areaName(for: hike.areaId))
                } label: {
                    HikeRow(
                        hike: hike,
                        areaName: areaName(for: hike.areaId),
                        trailName: trailName(for: hike)
                    )
                }
            }
            .onDelete { indexSet in
                Task { await deleteHikes(at: indexSet) }
            }
        }
        .listStyle(.insetGrouped)
    }

    /// Trail-mode hikes get the trail's name as a subtitle on the row so
    /// History reads "Hiked National Trail" instead of just "South Mountain
    /// Park". Best-effort lookup via the cached area; falls back to nil
    /// when the area isn't in cache yet.
    private func trailName(for hike: SavedRecording) -> String? {
        guard let trailId = hike.trailId,
              let area = areas.cachedArea(id: hike.areaId)
        else { return nil }
        return area.trails.first { $0.id == trailId }?.name
    }

    private var emptyState: some View {
        ContentUnavailableView(
            "No Hikes Yet",
            systemImage: "figure.hiking",
            description: Text("Start recording a hike from any trail area and it will appear here.")
        )
    }

    private func areaName(for areaId: String) -> String {
        areas.summaries.first { $0.id == areaId }?.name ?? areaId
    }

    private func loadHikes() async {
        isLoading = true
        hikes = await recording.loadHistory()
        isLoading = false
    }

    private func deleteHikes(at indexSet: IndexSet) async {
        for i in indexSet {
            await recording.deleteRecording(id: hikes[i].id)
        }
        hikes.remove(atOffsets: indexSet)
    }
}

struct HikeRow: View {
    let hike: SavedRecording
    let areaName: String
    let trailName: String?

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
                Label(String(format: "%.2f mi", hike.distanceMi), systemImage: "figure.walk")
                Label(durationString, systemImage: "clock")
                if !hike.completedTrailIds.isEmpty {
                    Label("\(hike.completedTrailIds.count) trails", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                }
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}
