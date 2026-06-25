import ActivityKit
import WidgetKit
import SwiftUI

/// Lock-screen Live Activity + Dynamic Island layouts for an active
/// recording. The app updates the `ContentState` once per second
/// during a hike; this view re-renders from that state.
///
/// Three Dynamic Island contexts:
/// - `compact`: leading + trailing slots either side of the cutout.
///   Hiking icon + distance.
/// - `expanded`: full pill, four regions. Hiking icon, name, stats.
/// - `minimal`: single-icon slot when another activity steals the
///   foreground. We just render the hiking icon.
struct RecordingLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: RecordingActivityAttributes.self) { context in
            LockScreenView(context: context)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Image(systemName: "figure.hiking")
                        .foregroundStyle(.red)
                        .font(.title2)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(formattedElapsed(context.state.elapsedSeconds))
                        .font(.title3.monospacedDigit())
                }
                DynamicIslandExpandedRegion(.center) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(context.attributes.name)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                        Text(formattedDistance(context.state.distanceMeters))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                DynamicIslandExpandedRegion(.bottom) {
                    if let turn = context.state.nextTurnMeters {
                        Text("→ \(formattedShort(meters: turn)) to next turn")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } compactLeading: {
                Image(systemName: "figure.hiking").foregroundStyle(.red)
            } compactTrailing: {
                Text(formattedDistance(context.state.distanceMeters))
                    .font(.caption.monospacedDigit())
            } minimal: {
                Image(systemName: "figure.hiking").foregroundStyle(.red)
            }
        }
    }
}

private struct LockScreenView: View {
    let context: ActivityViewContext<RecordingActivityAttributes>

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "figure.hiking")
                .font(.title)
                .foregroundStyle(.red)
            VStack(alignment: .leading, spacing: 2) {
                Text(context.attributes.name)
                    .font(.headline)
                    .lineLimit(1)
                if let subtitle = context.attributes.subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                HStack(spacing: 12) {
                    Label(formattedDistance(context.state.distanceMeters),
                          systemImage: "figure.walk")
                    Label(formattedElapsed(context.state.elapsedSeconds),
                          systemImage: "clock")
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
                if let turn = context.state.nextTurnMeters {
                    Text("→ \(formattedShort(meters: turn)) to next turn")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

private func formattedDistance(_ meters: Double) -> String {
    let miles = meters / 1609.344
    if miles < 10 { return String(format: "%.2f mi", miles) }
    return String(format: "%.1f mi", miles)
}

private func formattedElapsed(_ seconds: Int) -> String {
    let h = seconds / 3600
    let m = (seconds % 3600) / 60
    let s = seconds % 60
    if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
    return String(format: "%02d:%02d", m, s)
}

private func formattedShort(meters: Double) -> String {
    let ft = Int((meters * 3.28084).rounded())
    return "\(ft) ft"
}
