import SwiftUI

// SPIKE (debug-only): see UsgsTopoMath.swift for removal notes.
struct UsgsTopoSettingsView: View {
    @StateObject private var dl = UsgsTopoDownloader()
    @AppStorage(UsgsTopoKeys.mode) private var modeRaw = UsgsTopoMode.off.rawValue
    @AppStorage(UsgsTopoKeys.replaceApple) private var replaceApple = true

    var body: some View {
        Form {
            Section("Mode") {
                Picker("Topo base map", selection: $modeRaw) {
                    ForEach(UsgsTopoMode.allCases) { Text($0.title).tag($0.rawValue) }
                }
                Toggle("Replace Apple map content", isOn: $replaceApple)
            }
            Section("South Mountain (z8-z16)") {
                if dl.running {
                    ProgressView(value: Double(dl.done), total: Double(max(dl.total, 1)))
                    Text("\(dl.done)/\(dl.total) tiles, \(dl.failed) failed")
                    Button("Cancel", action: dl.cancel)
                } else {
                    Button("Download South Mountain topo", action: dl.download)
                }
                Text("On disk: \(dl.statsCount) tiles, \(dl.statsBytes) bytes (\(ByteCountFormatter.string(fromByteCount: dl.statsBytes, countStyle: .file)))")
                    .accessibilityIdentifier("usgsTopoStats")
                if !dl.lastMessage.isEmpty { Text(dl.lastMessage).font(.caption) }
                Button("Remove topo tiles", role: .destructive, action: dl.remove)
                    .disabled(dl.running)
            }
        }
        .navigationTitle("USGS Topo (test)")
    }
}
