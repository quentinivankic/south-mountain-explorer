import SwiftUI

/// Settings → Manage Offline Trails. Lists every decoded, non-empty,
/// identity-matching area file whose trail geometry is durable on disk.
struct DownloadedAreasView: View {
    @State private var rows: [AreaDataService.DownloadedArea] = []
    @State private var showClearAllConfirm = false

    var body: some View {
        List {
            if rows.isEmpty {
                Section {
                    Text("No Offline Trails downloaded")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 24)
                }
            } else {
                Section {
                    HStack {
                        Text("\(rows.count) area\(rows.count == 1 ? "" : "s")")
                        Spacer()
                        Text(totalSizeFormatted)
                            .foregroundStyle(.secondary)
                    }
                    Button(role: .destructive) {
                        showClearAllConfirm = true
                    } label: {
                        Label("Clear All Offline Trails", systemImage: "trash")
                    }
                } header: {
                    Text("Summary")
                }

                Section("Offline Trail Areas") {
                    ForEach(rows) { row in
                        HStack {
                            Text(row.name)
                                .lineLimit(1)
                            Spacer()
                            Text(sizeFormatted(row.sizeBytes))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .onDelete { offsets in
                        for index in offsets {
                            AreaDataService.shared.removeDownloadedArea(id: rows[index].id)
                        }
                        rows.remove(atOffsets: offsets)
                    }
                }
            }
        }
        .navigationTitle("Manage Offline Trails")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { rows = AreaDataService.shared.downloadedAreas() }
        .confirmationDialog(
            "Clear all Offline Trails?",
            isPresented: $showClearAllConfirm,
            titleVisibility: .visible
        ) {
            Button("Clear All", role: .destructive) {
                AreaDataService.shared.clearAreaCache()
                rows = []
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Removes offline trail and catalog geometry for \(rows.count) area\(rows.count == 1 ? "" : "s") from this device (~\(totalSizeFormatted)). Apple base-map tiles are not managed here.")
        }
    }

    private var totalSizeFormatted: String {
        sizeFormatted(rows.reduce(0) { $0 + $1.sizeBytes })
    }

    private func sizeFormatted(_ bytes: Int) -> String {
        Int64(bytes).formatted(.byteCount(style: .file))
    }
}
