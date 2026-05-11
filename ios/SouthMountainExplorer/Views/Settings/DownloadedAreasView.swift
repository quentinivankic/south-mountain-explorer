import SwiftUI

/// Settings → Manage Downloads. Lists every area whose trail data is
/// cached to disk so the user can free up space on a per-area basis,
/// or nuke the lot from a single button. Sourced from
/// `AreaDataService.downloadedAreas()` (disk enumeration) so it
/// reflects exactly what's actually on the device.
struct DownloadedAreasView: View {
    @State private var rows: [AreaDataService.DownloadedArea] = []
    @State private var showClearAllConfirm = false

    var body: some View {
        List {
            if rows.isEmpty {
                Section {
                    Text("No downloaded areas")
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
                        Label("Clear All Downloads", systemImage: "trash")
                    }
                } header: {
                    Text("Summary")
                }

                Section("Downloaded Areas") {
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
        .navigationTitle("Manage Downloads")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { rows = AreaDataService.shared.downloadedAreas() }
        .confirmationDialog(
            "Clear all downloaded areas?",
            isPresented: $showClearAllConfirm,
            titleVisibility: .visible
        ) {
            Button("Clear All", role: .destructive) {
                AreaDataService.shared.clearAreaCache()
                rows = []
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Removes \(rows.count) downloaded area\(rows.count == 1 ? "" : "s") from this device (~\(totalSizeFormatted)). Each one will re-download the next time you open it.")
        }
    }

    private var totalSizeFormatted: String {
        sizeFormatted(rows.reduce(0) { $0 + $1.sizeBytes })
    }

    private func sizeFormatted(_ bytes: Int) -> String {
        Int64(bytes).formatted(.byteCount(style: .file))
    }
}
