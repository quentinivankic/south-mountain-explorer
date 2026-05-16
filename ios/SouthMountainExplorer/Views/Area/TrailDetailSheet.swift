import SwiftUI

/// Half-sheet that appears when the user taps a trail in the
/// list. Shows the trail's stats + coverage and exposes the two
/// primary trail actions: record it, export a GPX of the
/// official polyline. Lives at `.medium` detent so the map
/// underneath stays visible — the trail highlight engaged by the
/// same tap is the whole point of the layout.
struct TrailDetailSheet: View {
    let trail: Trail
    let areaId: String
    let areaName: String
    /// Forwarded from TrailListView → AreaView's recording-start
    /// pipeline. Sheet dismisses itself before invoking so the
    /// existing recording-start flow (permission alerts, conflict
    /// dialogs, etc.) has a clean view hierarchy to land in.
    var onRecordTrail: ((Trail) -> Void)? = nil

    @Environment(\.dismiss) private var dismiss
    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var gpxShareURL: IdentifiedURL? = nil

    private var coverageFraction: Double {
        coverage.coverage(for: areaId)[trail.id] ?? 0
    }

    private var isComplete: Bool {
        progress.isComplete(areaId: areaId, trailId: trail.id)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    statRow

                    if !isComplete && coverageFraction > 0.01 {
                        coverageBar
                    } else if isComplete {
                        completedBanner
                    }

                    actionButtons
                }
                .padding(20)
            }
            .navigationTitle(trail.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .sheet(item: $gpxShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
    }

    private var statRow: some View {
        HStack(spacing: 0) {
            stat(value: UnitFormatter.distanceValue(miles: trail.distanceMi, units: units),
                 unit: UnitFormatter.distanceSuffix(units: units),
                 label: "Length")
            Divider().frame(height: 36)
            stat(value: difficultyLabel, unit: "", label: "Difficulty")
            Divider().frame(height: 36)
            stat(value: routeLabel, unit: "", label: "Route")
        }
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity)
        .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func stat(value: String, unit: String, label: String) -> some View {
        VStack(spacing: 4) {
            HStack(alignment: .lastTextBaseline, spacing: 2) {
                Text(value)
                    .font(.title3.bold().monospacedDigit())
                if !unit.isEmpty {
                    Text(unit)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private var coverageBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Progress")
                    .font(.subheadline.weight(.medium))
                Spacer()
                Text("\(Int((coverageFraction * 100).rounded()))%")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            ProgressView(value: coverageFraction)
                .tint(.cyan)
        }
        .padding(14)
        .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var completedBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.seal.fill")
                .foregroundStyle(.green)
                .font(.title3)
            Text("You've completed this trail")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(.green)
            Spacer()
        }
        .padding(14)
        .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var actionButtons: some View {
        VStack(spacing: 10) {
            // "Record This Trail" is gated on no active recording,
            // matching the prior context-menu behavior. Mid-recording
            // the bottom retarget / suggestion banners are the right
            // affordance instead.
            if recording.activeRecording == nil, let onRecordTrail {
                Button {
                    dismiss()
                    onRecordTrail(trail)
                } label: {
                    Label("Record This Trail", systemImage: "record.circle")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }

            Button {
                exportGpx()
            } label: {
                Label("Export as GPX", systemImage: "square.and.arrow.up")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
        }
    }

    private func exportGpx() {
        do {
            let url = try GpxExport.temporaryFile(trail: trail, areaName: areaName)
            gpxShareURL = IdentifiedURL(url: url)
        } catch {
            // Silent — share sheet won't appear; user can retry.
        }
    }

    private var difficultyLabel: String {
        switch trail.difficulty {
        case .easy: return "Easy"
        case .moderate: return "Moderate"
        case .hard: return "Hard"
        }
    }

    private var routeLabel: String {
        switch trail.routeType {
        case .loop: return "Loop"
        case .linear: return "Linear"
        }
    }
}
