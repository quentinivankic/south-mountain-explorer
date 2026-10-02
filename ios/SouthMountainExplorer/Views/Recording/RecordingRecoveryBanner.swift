import SwiftUI

/// Persistent root notice for launch-time recording reconciliation failures.
/// The issue carries fixed, non-sensitive copy; retry never deletes either
/// recovery copy unless history verifies an exact already-saved checkpoint.
struct RecordingRecoveryBanner: View {
    let issue: RecordingRecoveryIssue
    let onRetry: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.arrow.triangle.2.circlepath")
                .foregroundStyle(.orange)
                .font(.title3)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(issue.title)
                    .font(.subheadline.weight(.semibold))
                Text(issue.message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            Button("Retry", action: onRetry)
                .buttonStyle(.bordered)
                .accessibilityHint("Checks the preserved recording and history again")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.regularMaterial)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("recording-recovery-banner")
    }
}
