import SwiftUI

/// Build-12 trail-suggestion banner. Appears above the recording
/// panel when `TrailSuggestionEngine.candidates` returns a nearby
/// incomplete trail the user could easily knock out. Same glass
/// chrome + button affordances as `RetargetTrailBanner` so the
/// two banners feel like one consistent surface — only the copy
/// differs.
///
/// Switch action calls `RecordingService.retargetTrail` (which the
/// build-12 PR loosens to handle roam → trail conversion), so a
/// roam-mode recording becomes a trail-mode recording with a one-
/// tap "okay yeah I'll finish that trail."
struct SuggestionBanner: View {
    let suggestion: TrailSuggestion
    /// Caller invokes the retarget + clears any UI state that
    /// would race with the banner unmounting on next body eval.
    let onSwitch: () -> Void
    /// Caller adds the trail id to a per-session "dismissed" set
    /// so the same suggestion doesn't re-mount immediately as the
    /// user keeps walking past it.
    let onDismiss: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Add \(suggestion.trail.name)")
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                Text(detailLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            Button {
                onSwitch()
            } label: {
                Text("Switch")
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(.tint, in: Capsule())
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
            Button {
                onDismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.secondary)
                    .padding(8)
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Dismiss")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .compatibleGlass(in: .rect(cornerRadius: 20))
        .padding(.horizontal, 16)
    }

    /// Concatenated detail line: "0.2 mi detour, ~6 min" — drops
    /// the time portion when the engine couldn't compute pace
    /// (shouldn't happen since pace is a hard gate in the engine,
    /// but defend against future engine relaxations).
    private var detailLine: String {
        let detour = suggestion.detourMilesLabel
        let time = TrailETA.formatLabel(suggestion.extraSeconds)
        return "\(detour) detour, ~\(time)"
    }
}
