import SwiftUI

/// One state or country: progress, receipt, failures, Update and Remove.
struct StateDownloadDetailView: View {
    let stateKey: String
    let present: (StateDownloadsView.StartRequest) -> Void
    @State private var manager = StateDownloadManager.shared
    @State private var showRemoveConfirm = false
    @State private var removalResult: String?
    @ScaledMetric private var spacing: CGFloat = 8

    var body: some View {
        let status = manager.status(for: stateKey)
        let failedCount = manager.failedCount(stateKey)
        let busy = manager.isRunning
        List {
            Section {
                VStack(alignment: .leading, spacing: spacing) {
                    Text(StateDownloadFormat.statusText(status))
                        .font(.headline)
                    if case .downloading = status, let p = manager.progress[stateKey] {
                        ProgressView(value: Double(p.verified), total: Double(max(p.total, 1)))
                            .accessibilityLabel("\(stateKey) download")
                            .accessibilityValue(StateDownloadsView.progressValue(p))
                        Text("\(StateDownloadFormat.bytes(p.bytes)) downloaded")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if let summary = manager.updateSummaries[stateKey] {
                        Text(summary).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .accessibilityElement(children: .combine)
            }

            Section {
                primaryAction(status: status, busy: busy)
                if busy, manager.jobKeys.contains(stateKey) {
                    Button(role: .destructive) { manager.cancel() } label: {
                        Label("Cancel Download", systemImage: "xmark.circle")
                    }
                }
                if failedCount > 0, !busy {
                    Button {
                        present(.init(keys: [stateKey], title: stateKey, mode: .retry))
                    } label: {
                        Label("\(failedCount) area\(failedCount == 1 ? "" : "s") failed. Retry", systemImage: "arrow.clockwise")
                    }
                }
            }

            if failedCount > 0 {
                Section {
                    DisclosureGroup("Failed areas (\(failedCount))") {
                        ForEach(Array(manager.failedAreas(stateKey).enumerated()), id: \.offset) { _, item in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.name)
                                Text(item.reason).font(.caption).foregroundStyle(.secondary)
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
            }

            if hasDownloadedAnything(status) {
                Section {
                    Button(role: .destructive) {
                        showRemoveConfirm = true
                    } label: {
                        Label("Remove \(stateKey) Downloads", systemImage: "trash")
                    }
                    .disabled(busy)
                } footer: {
                    Text("Removes only areas this download saved. Saved areas, areas you opened or downloaded another way, and your recordings stay.")
                }
            }

            if let removalResult {
                Section { Text(removalResult).accessibilityLabel(removalResult) }
            }
        }
        .navigationTitle(stateKey)
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog(
            "Remove \(stateKey) downloads?",
            isPresented: $showRemoveConfirm,
            titleVisibility: .visible
        ) {
            Button("Remove", role: .destructive) {
                if let summary = manager.remove(stateKey) {
                    removalResult = "Removed \(summary.removed) areas. Kept \(summary.kept) saved or opened areas."
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            let plan = manager.removalPreview(stateKey)
            Text("Removes \(plan.delete.count) area\(plan.delete.count == 1 ? "" : "s"). Kept: \(plan.keptProtected.count) saved or opened area\(plan.keptProtected.count == 1 ? "" : "s").")
        }
    }

    private func hasDownloadedAnything(_ status: StateRowStatus) -> Bool {
        if case .notDownloaded = status { return false }
        return !(manager.ledgers[stateKey]?.claims.isEmpty ?? true)
    }

    @ViewBuilder
    private func primaryAction(status: StateRowStatus, busy: Bool) -> some View {
        switch status {
        case .complete:
            Button {
                present(.init(keys: [stateKey], title: stateKey, mode: .update))
            } label: {
                Label("Update", systemImage: "arrow.triangle.2.circlepath")
            }
            .disabled(busy)
            .accessibilityLabel("Update \(stateKey)")
        case .downloading, .queued:
            EmptyView()
        case .notDownloaded:
            downloadButton(title: "Download", busy: busy)
        case .paused, .waitingForNetwork, .interrupted, .incomplete:
            downloadButton(title: "Resume", busy: busy)
        }
    }

    private func downloadButton(title: String, busy: Bool) -> some View {
        let scope = manager.scope(stateKey)
        let estimate = scope.map { manager.remainingEstimate($0) } ?? .zero
        return Button {
            present(.init(keys: [stateKey], title: stateKey, mode: .download))
        } label: {
            Label("\(title) \(stateKey)", systemImage: "arrow.down.circle")
        }
        .disabled(busy)
        .accessibilityLabel("\(title) \(stateKey), about \(StateDownloadFormat.bytes(estimate.wireBytes))")
    }
}
