import SwiftUI

/// Top-of-screen pill that appears any time RecordingService has an
/// active recording. Lives at the ContentView level via .safeAreaInset
/// so it's visible across every tab and doesn't collide with per-screen
/// chrome. Shows live distance/elapsed and exposes a Stop button so the
/// user doesn't have to navigate back to the area to end a hike.
struct ActiveRecordingBanner: View {
    let areaName: String
    let distanceMi: Double
    let startedAt: Date
    let onStop: () -> Void

    @State private var elapsed: TimeInterval = 0
    @State private var timer: Timer? = nil

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "record.circle.fill")
                .foregroundStyle(.red)
                .font(.title3)
                .symbolEffect(.pulse)

            VStack(alignment: .leading, spacing: 1) {
                Text(areaName)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                Text("\(String(format: "%.2f", distanceMi)) mi · \(formattedElapsed)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 8)

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
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            elapsed = Date().timeIntervalSince(startedAt)
        }
    }
}
