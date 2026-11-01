import SwiftUI

enum StateDownloadFormat {
    static func bytes(_ value: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
    }

    static func date(_ value: Date) -> String {
        value.formatted(date: .abbreviated, time: .omitted)
    }

    static func statusText(_ status: StateRowStatus) -> String {
        switch status {
        case .notDownloaded:
            return "Not downloaded"
        case .downloading(let verified, let total):
            return "Downloading \(verified) of \(total)"
        case .queued:
            return "Queued"
        case .paused(let verified, let total):
            return "Paused, \(verified) of \(total)"
        case .waitingForNetwork(let verified, let total):
            return "Waiting for connection, \(verified) of \(total)"
        case .interrupted(let verified, let total):
            return "Interrupted, \(verified) of \(total). Resume"
        case .incomplete(let onDevice, let total):
            return "Incomplete: \(onDevice) of \(total) on device. Resume"
        case .complete(let verified, let total, let completedAt):
            let when = completedAt.map { ", \(date($0))" } ?? ""
            return "Verified: \(verified) of \(total) areas\(when)"
        }
    }
}

/// Settings > Offline Trails > Download by State or Country.
struct StateDownloadsView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var manager = StateDownloadManager.shared
    @State private var network = NetworkService.shared
    @State private var service = AreaDataService.shared
    @AppStorage(StorageKeys.offlineStatesAllowCellular) private var allowCellular = false
    @State private var sheet: StartRequest?
    @State private var currentKey: String?

    var body: some View {
        List {
            Section {
                Toggle("Allow cellular data", isOn: $allowCellular)
                    .accessibilityHint("Off by default. When off, downloads wait for Wi-Fi.")
            } footer: {
                Text("Saves trail geometry only; Apple base-map tiles are not included. Downloads use Wi-Fi unless you allow cellular data.")
            }

            if manager.isRunning { runningSection }

            if let notice = manager.notice {
                Section {
                    Text(notice)
                        .foregroundStyle(.orange)
                        .accessibilityLabel("Notice: \(notice)")
                }
            }

            if !manager.usScopes.isEmpty {
                Section("United States") {
                    allUSRow
                }
            }

            if let currentKey, let scope = manager.scope(currentKey) {
                Section("Near You") { row(scope) }
            }

            if !manager.usScopes.isEmpty {
                Section("US States") {
                    ForEach(manager.usScopes.filter { $0.id != currentKey }) { row($0) }
                }
            }

            if !manager.countryScopes.isEmpty {
                Section("Countries") {
                    ForEach(manager.countryScopes) { row($0) }
                }
            }

            if manager.scopes.isEmpty {
                Section {
                    Text("The area catalog hasn't loaded yet. Connect once and come back.")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Download by State")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            manager.reload()
            currentKey = manager.currentStateKey()
        }
        .onChange(of: service.summaries.count) { _, _ in manager.reload() }
        .onChange(of: network.isConnected) { _, _ in manager.resumeIfStalled() }
        .onChange(of: network.isExpensive) { _, _ in manager.resumeIfStalled() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { manager.sceneDidEnterBackground() }
            if phase == .active { manager.resumeIfStalled() }
        }
        .onChange(of: manager.lastAnnouncement) { _, text in
            if let text { AccessibilityNotification.Announcement(text).post() }
        }
        .sheet(item: $sheet) { request in
            StateStartSheet(request: request) { consent in
                manager.start(
                    keys: request.keys,
                    forceUpdate: request.mode == .update,
                    onlyFailed: request.mode == .retry,
                    consent: consent
                )
                sheet = nil
            }
        }
    }

    private var runningSection: some View {
        Section("In Progress") {
            if let key = manager.currentKey, let p = manager.progress[key] {
                VStack(alignment: .leading, spacing: 6) {
                    Text(key).font(.headline)
                    ProgressView(value: Double(p.verified), total: Double(max(p.total, 1)))
                        .accessibilityLabel("\(key) download")
                        .accessibilityValue(Self.progressValue(p))
                    Text("\(p.verified) of \(p.total) areas, \(StateDownloadFormat.bytes(p.bytes))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
            }
            Button(role: .destructive) {
                manager.cancel()
            } label: {
                Label("Cancel Download", systemImage: "xmark.circle")
            }
        }
    }

    static func progressValue(_ p: StateProgress) -> String {
        let percent = p.total > 0 ? p.verified * 100 / p.total : 0
        return "\(p.verified) of \(p.total) areas, \(percent) percent"
    }

    private var allUSRow: some View {
        let keys = manager.usKeys()
        let preflight = manager.preflight(keys: keys)
        return Button {
            sheet = StartRequest(keys: keys, title: "United States", mode: .download)
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text("Download all US").font(.headline)
                Text("\(preflight.areaCount) areas, about \(StateDownloadFormat.bytes(preflight.estimate.wireBytes)) download, \(StateDownloadFormat.bytes(preflight.estimate.onDeviceBytes)) on device")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .disabled(manager.isRunning || preflight.pendingCount == 0)
        .accessibilityLabel("Download all of the United States")
        .accessibilityValue(preflight.pendingCount == 0
            ? "Already on this device"
            : "\(preflight.pendingCount) areas, about \(StateDownloadFormat.bytes(preflight.estimate.wireBytes))")
    }

    private func row(_ scope: StateScope) -> some View {
        NavigationLink {
            StateDownloadDetailView(stateKey: scope.id) { request in sheet = request }
        } label: {
            StateDownloadRow(scope: scope)
        }
    }

    struct StartRequest: Identifiable {
        enum Mode { case download, update, retry }
        let id = UUID()
        let keys: [String]
        let title: String
        let mode: Mode
    }
}

struct StateDownloadRow: View {
    let scope: StateScope
    @State private var manager = StateDownloadManager.shared

    var body: some View {
        let estimate = manager.fullEstimate(scope)
        let status = manager.status(for: scope.id)
        let failed = manager.failedCount(scope.id)
        VStack(alignment: .leading, spacing: 4) {
            Text(scope.displayName).font(.headline)
            Text("\(scope.areaIDs.count) areas, about \(StateDownloadFormat.bytes(estimate.wireBytes)) download, \(StateDownloadFormat.bytes(estimate.onDeviceBytes)) on device\(manager.isRoughEstimate(scope) ? " (rough estimate)" : "")")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(StateDownloadFormat.statusText(status))
                .font(.caption)
                .foregroundStyle(statusColor(status))
            if failed > 0 {
                Text("\(failed) area\(failed == 1 ? "" : "s") failed")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func statusColor(_ status: StateRowStatus) -> Color {
        if case .complete = status { return .green }
        return .secondary
    }
}

/// Size, network and storage check shown before any job starts.
struct StateStartSheet: View {
    let request: StateDownloadsView.StartRequest
    let onStart: (_ consent: Bool) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var manager = StateDownloadManager.shared
    @State private var network = NetworkService.shared
    @State private var consent = false

    var body: some View {
        let isUpdate = request.mode == .update
        let preflight = manager.preflight(keys: request.keys, consent: consent)
        NavigationStack {
            List {
                Section {
                    if isUpdate {
                        Text("Checks every area for changes and downloads only the ones that changed.")
                    } else {
                        Text("\(preflight.pendingCount) of \(preflight.areaCount) areas to download")
                        Text("About \(StateDownloadFormat.bytes(preflight.estimate.wireBytes)) download, \(StateDownloadFormat.bytes(preflight.estimate.onDeviceBytes)) on device")
                    }
                }
                Section("Connection") { networkRows(preflight) }
                if !isUpdate { Section("Storage") { storageRow(preflight) } }
            }
            .navigationTitle(request.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(buttonTitle) { onStart(consent) }
                        .disabled(!canStart(preflight, isUpdate: isUpdate))
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private var buttonTitle: String {
        switch request.mode {
        case .download: return "Start"
        case .update: return "Update"
        case .retry: return "Retry"
        }
    }

    private func canStart(_ preflight: StatePreflight, isUpdate: Bool) -> Bool {
        let networkOK = preflight.network == .proceed
        if request.mode == .download { return preflight.canStart && networkOK }
        return networkOK
    }

    @ViewBuilder
    private func networkRows(_ preflight: StatePreflight) -> some View {
        switch preflight.network {
        case .proceed:
            Text(network.isExpensive ? "Using cellular data, as you allowed." : "On Wi-Fi.")
        case .needsCellularConsent:
            Text("You're on cellular data. This download is about \(StateDownloadFormat.bytes(preflight.estimate.wireBytes)).")
            Toggle("Use cellular data for this download", isOn: $consent)
        case .blocked(.cellularNotAllowed):
            Text("Cellular data is off for downloads. Connect to Wi-Fi, or turn on Allow cellular data in the list.")
                .foregroundStyle(.orange)
        case .blocked(.offline):
            Text("You're offline. Connect to download.")
                .foregroundStyle(.orange)
        }
    }

    @ViewBuilder
    private func storageRow(_ preflight: StatePreflight) -> some View {
        let free = preflight.freeBytes.map { StateDownloadFormat.bytes(Int($0)) } ?? "unknown"
        switch preflight.storage {
        case .ok:
            Text("Needs about \(StateDownloadFormat.bytes(preflight.estimate.onDeviceBytes)); \(free) free.")
        case .lowSpaceWarning(let after):
            Text("Low space: about \(StateDownloadFormat.bytes(Int(max(after, 0)))) would be left. You can still continue.")
                .foregroundStyle(.orange)
        case .insufficient(let needed):
            Text("Not enough free space. About \(StateDownloadFormat.bytes(Int(needed))) is needed; \(free) free.")
                .foregroundStyle(.red)
        }
    }
}
