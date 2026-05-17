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

    @State private var gpxShareURL: IdentifiedURL? = nil

    private var coverageFraction: Double {
        coverage.coverage(for: areaId)[trail.id] ?? 0
    }

    private var isComplete: Bool {
        progress.isComplete(areaId: areaId, trailId: trail.id)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(trail.name)
                .font(.title3.bold())
                .padding(.top, 4)

            if isComplete {
                Label("Completed", systemImage: "checkmark.seal.fill")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.green)
            } else if coverageFraction > 0.01 {
                HStack {
                    Text("\(Int((coverageFraction * 100).rounded()))% covered")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                    Spacer()
                }
                ProgressView(value: coverageFraction)
                    .tint(.cyan)
            }

            actionButtons
                .padding(.top, 4)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .presentationDetents([.height(260)])
        .presentationDragIndicator(.visible)
        .sheet(item: $gpxShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
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
}
