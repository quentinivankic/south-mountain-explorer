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
    let isSaving: Bool
    let showsStopControl: Bool
    let onTap: () -> Void
    let onStop: () -> Void

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var elapsed: TimeInterval = 0
    @State private var timer: Timer? = nil

    var body: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 10) {
                    accessibilityOpenButton
                    if showsStopControl {
                        stopButton
                            .frame(maxWidth: .infinity)
                    }
                }
            } else {
                HStack(spacing: 12) {
                    compactOpenButton
                    if showsStopControl {
                        stopButton
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.regularMaterial)
        .onAppear { startTimer() }
        .onDisappear { timer?.invalidate() }
    }

    private var compactOpenButton: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                recordingIcon
                VStack(alignment: .leading, spacing: 1) {
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
        .frame(maxWidth: .infinity)
        .recordingBannerAccessibility(
            title: trailName ?? areaName,
            subtitle: subtitleLine
        )
    }

    private var accessibilityOpenButton: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    recordingIcon
                    Text(trailName ?? areaName)
                        .font(.headline)
                        .foregroundStyle(.primary)
                        .multilineTextAlignment(.leading)
                }
                Text(subtitleLine)
                    .font(.body.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.leading)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .recordingBannerAccessibility(
            title: trailName ?? areaName,
            subtitle: subtitleLine
        )
    }

    private var recordingIcon: some View {
        Image(systemName: "record.circle.fill")
            .foregroundStyle(.red)
            .font(.title3)
            .symbolEffect(.pulse)
    }

    private var stopButton: some View {
        Button(action: onStop) {
            HStack(spacing: 6) {
                if isSaving {
                    ProgressView()
                        .controlSize(.small)
                        .tint(.white)
                }
                Text(isSaving ? "Saving…" : "Stop")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
            }
            .frame(maxWidth: dynamicTypeSize.isAccessibilitySize ? .infinity : nil)
            .padding(.horizontal, 14)
            .padding(.vertical, dynamicTypeSize.isAccessibilitySize ? 9 : 7)
            .background(isSaving ? Color.secondary : Color.red, in: Capsule())
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
        .accessibilityIdentifier("active-recording-stop-button")
        .accessibilityLabel(isSaving ? "Saving recording" : "Stop recording")
        .accessibilityHint("Stops and saves the active recording")
    }

    /// "South Mountain Park · 1.23 mi · 14:32" when in trail mode,
    /// "1.23 mi · 14:32" when in roam mode (area is already the title).
    private var subtitleLine: String {
        let stats = "\(UnitFormatter.distance(miles: distanceMi, units: units)) · \(formattedElapsed)"
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

private extension View {
    func recordingBannerAccessibility(title: String, subtitle: String) -> some View {
        accessibilityIdentifier("active-recording-banner")
            .accessibilityLabel("Open active recording")
            .accessibilityValue("\(title), \(subtitle)")
            .accessibilityHint("Returns to the map and recording controls")
    }
}
