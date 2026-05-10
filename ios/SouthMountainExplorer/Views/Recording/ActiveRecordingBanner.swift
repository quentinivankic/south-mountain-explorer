import SwiftUI

/// Top-of-screen pill that appears any time RecordingService has an
/// active recording. Lives at the ContentView level via .safeAreaInset
/// so it's visible across every tab and doesn't collide with per-screen
/// chrome. Shows live distance/elapsed and exposes a Stop button so the
/// user doesn't have to navigate back to the area to end a hike.
struct ActiveRecordingBanner: View {
    let areaName: String
    /// Set when the active recording is in `.trail` mode — promoted to
    /// the banner's primary label so the user sees "West Alta" instead
    /// of just "South Mountain Park". Mirrors HikeRow's title treatment.
    let trailName: String?
    let distanceMi: Double
    let startedAt: Date
    let onTap: () -> Void
    let onStop: () -> Void

    @State private var elapsed: TimeInterval = 0
    @State private var timer: Timer? = nil

    var body: some View {
        HStack(spacing: 12) {
            // Info-region is its own button so a tap anywhere on the
            // record dot / name / stats jumps to the recording area.
            // Stop stays a separate hit target on the right.
            Button(action: onTap) {
                HStack(spacing: 12) {
                    Image(systemName: "record.circle.fill")
                        .foregroundStyle(.red)
                        .font(.title3)
                        .symbolEffect(.pulse)

                    VStack(alignment: .leading, spacing: 1) {
                        // Trail name wins the primary slot when in
                        // .trail mode; area name slips to a caption.
                        Text(trailName ?? areaName)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.primary)
                            .lineLimit(1)
                        Text(subtitleLine)
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 8)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Button(action: onStop) {
                Text("Stop")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 7)
                    .background(.red, in: Capsule())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.regularMaterial)
        .onAppear { startTimer() }
        .onDisappear { timer?.invalidate() }
    }

    /// "South Mountain Park · 1.23 mi · 14:32" when in trail mode,
    /// "1.23 mi · 14:32" when in roam mode (area is already the title).
    private var subtitleLine: String {
        let stats = "\(String(format: "%.2f", distanceMi)) mi · \(formattedElapsed)"
        if trailName != nil {
            return "\(areaName) · \(stats)"
        }
        return stats
    }

    private var formattedElapsed: String {
        let total = Int(elapsed)
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%02d:%02d", m, s)
    }

    private func startTimer() {
        elapsed = Date().timeIntervalSince(startedAt)
        // The Timer fire closure is @Sendable / nonisolated; hop back to the
        // main actor before touching @State to keep Swift 6 strict
        // concurrency happy.
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            Task { @MainActor in
                elapsed = Date().timeIntervalSince(startedAt)
            }
        }
    }
}
