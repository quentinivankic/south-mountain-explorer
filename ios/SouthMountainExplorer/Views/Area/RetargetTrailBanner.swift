import SwiftUI

/// Slim banner that appears above the recording panel when the
/// user has tapped a trail different from the one their active
/// recording is targeted at. Offers a one-tap "switch active to
/// this trail" action, plus a dismiss button for when they tapped
/// out of curiosity and don't actually want to retarget.
///
/// Visual: rounded glass-effect pill matching the recording
/// panel's chrome, two columns wide ("Switch active to…" label
/// + Switch button) with a small × to the right.
struct RetargetTrailBanner: View {
    /// Trail the user has currently selected — the retarget
    /// target. Always non-nil when this view is rendered (AreaView
    /// only mounts the banner when there's a selected trail to
    /// switch TO).
    let selectedTrail: Trail
    /// Called when the user taps "Switch" — caller updates
    /// `RecordingService.retargetTrail` and clears the selection
    /// so the banner unmounts.
    let onSwitch: () -> Void
    /// Called when the user taps × — caller clears the selection
    /// to unmount the banner without retargeting.
    let onDismiss: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Switch active trail to")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(selectedTrail.name)
                    .font(.subheadline.weight(.semibold))
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
}
