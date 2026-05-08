import SwiftUI

struct HistoryView: View {
    @Environment(RecordingService.self) private var recording
    @Environment(AuthService.self) private var auth
    @Environment(AreaDataService.self) private var areas

    @State private var hikes: [SavedRecording] = []
    @State private var isLoading = false

    var body: some View {
        NavigationStack {
            Group {
                if !auth.isSignedIn {
                    signInPrompt
                } else if isLoading {
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
                HikeRow(hike: hike, areaName: areaName(for: hike.areaId))
            }
            .onDelete { indexSet in
                Task { await deleteHikes(at: indexSet) }
            }
        }
        .listStyle(.insetGrouped)
    }

    private var emptyState: some View {
        ContentUnavailableView(
            "No Hikes Yet",
            systemImage: "figure.hiking",
            description: Text("Start recording a hike from any trail area and it will appear here.")
        )
    }

    private var signInPrompt: some View {
        VStack(spacing: 20) {
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 56))
                .foregroundStyle(.secondary)
            Text("Sign in to see your hike history")
                .font(.headline)
                .foregroundStyle(.secondary)
            NavigationLink(destination: AuthView()) {
                Text("Sign In")
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .padding(.horizontal, 40)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(areaName)
                    .font(.headline)
                Spacer()
                Text(dateString)
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
