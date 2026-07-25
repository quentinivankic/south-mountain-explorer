import SwiftUI

/// Half-sheet that appears when the user taps a trail in the
/// list. Shows progress + personal hike history + the two primary
/// trail actions: record it, export a GPX of the official
/// polyline. Lives at `.height(320)` detent so the map underneath
/// stays visible — the trail highlight engaged by the same tap
/// is the whole point of the layout.
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
    /// Hike history filtered to those that touched this trail.
    /// Loaded asynchronously by `.task(id: trail.id)` — empty
    /// until the load lands. Re-filters when the sheet re-targets
    /// a different trail without dismissing.
    @State private var trailHikes: [SavedRecording] = []

    /// Fraction of the trail's nodes covered *since the last
    /// completion*. For never-completed trails this equals lifetime
    /// coverage; after a completion event it resets to 0 and grows
    /// from there. Drives the "X% remaining" copy below.
    private var coverageFraction: Double {
        coverage.coverageSinceCompletion(for: areaId)[trail.id] ?? 0
    }

    private var remainingFraction: Double {
        max(0, min(1, 1 - coverageFraction))
    }

    private var isComplete: Bool {
        progress.isComplete(trail, areaId: areaId)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(trail.name)
                .font(.title3.bold())
                .padding(.top, 16)

            // Two states only now — the third (never-walked +
            // never-completed) used to render nothing, leaving a
            // confusing void. We render the bar at 100% remaining
            // for fresh trails so the user sees the natural "all
            // ahead of you" state. Completed-and-not-yet-rewalked
            // wins over the bar.
            if isComplete && coverageFraction < 0.01 {
                Label("Completed", systemImage: "checkmark.seal.fill")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.green)
            } else {
                HStack {
                    Text("\(Int((remainingFraction * 100).rounded()))% remaining")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                    Spacer()
                }
                ProgressView(value: remainingFraction)
                    .tint(.cyan)
            }

            hikeHistoryLine

            actionButtons
                .padding(.top, 4)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .presentationDetents([.height(320)])
        .presentationDragIndicator(.visible)
        .task(id: trail.id) {
            await loadHikeHistory()
        }
        .sheet(item: $gpxShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
    }

    /// One-line summary of how often + how recently the user
    /// has walked this trail. Renders nothing when the load
    /// hasn't returned yet or when there are zero matching
    /// hikes — keeps the sheet quiet for fresh trails.
    @ViewBuilder
    private var hikeHistoryLine: some View {
        if !trailHikes.isEmpty, let mostRecent = trailHikes.first {
            HStack(spacing: 4) {
                Image(systemName: "figure.hiking")
                    .foregroundStyle(.secondary)
                    .font(.caption)
                Text(walkCountLabel(trailHikes.count))
                Text("·")
                    .foregroundStyle(.tertiary)
                Text("Last \(relativeDateString(mostRecent.startedAt))")
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
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
                    ActivityLogService.shared.log(
                        category: "trail",
                        action: "recordTap",
                        context: ["areaId": areaId, "trailId": trail.id]
                    )
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
        ActivityLogService.shared.log(
            category: "trail",
            action: "exportGpx",
            context: ["areaId": areaId, "trailId": trail.id]
        )
        do {
            let url = try GpxExport.temporaryFile(trail: trail, areaName: areaName)
            gpxShareURL = IdentifiedURL(url: url)
        } catch {
            // Silent — share sheet won't appear; user can retry.
        }
    }

    /// Load full hike history, filter to hikes in this area that
    /// touched this specific trail, then sort newest-first.
    /// "Touched" = the recording targeted this trail, or this
    /// trail showed up in newly-completed / revisited at stop time.
    /// Cheap heuristic — no per-hike coverage recomputation.
    private func loadHikeHistory() async {
        let all = await recording.loadHistory()
        trailHikes = all
            .filter { hike in
                guard hike.touchedAreaIds.contains(areaId) else { return false }
                if hike.trailId == trail.id { return true }
                // Walk-aware accessors: walks credit trails per area.
                if hike.completedTrailIds(in: areaId).contains(trail.id) { return true }
                if hike.revisitedTrailIds(in: areaId).contains(trail.id) { return true }
                return false
            }
            .sorted { $0.startedAt > $1.startedAt }
    }

    private func walkCountLabel(_ n: Int) -> String {
        switch n {
        case 1: return "Walked once"
        case 2: return "Walked twice"
        default: return "Walked \(n) times"
        }
    }

    /// `RelativeDateTimeFormatter` isn't Sendable, so we can't park
    /// one in a `static let` under Swift 6 strict concurrency.
    /// Construct on each call — cheap at sheet-open rate.
    private func relativeDateString(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .full
        f.dateTimeStyle = .named
        return f.localizedString(for: date, relativeTo: Date())
    }
}
