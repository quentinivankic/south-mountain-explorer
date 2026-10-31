import SwiftUI
import WidgetKit

typealias HikeState = HikeActivityAttributes.ContentState

extension HikeState.Status {
    var tint: Color {
        switch self {
        case .recording, .finished: return .green
        case .waitingForGPS, .gpsPaused: return .orange
        case .gpsRecovered: return .blue
        }
    }
}

/// System-driven elapsed timer: ticks with no activity updates and freezes
/// at `endedAt` once the hike is saved.
struct HikeTimerText: View {
    let startedAt: Date
    let state: HikeState

    var body: some View {
        Text(timerInterval: startedAt...Date.distantFuture,
             pauseTime: state.endedAt,
             countsDown: false)
            .monospacedDigit()
    }
}

struct HikeEstimatesRow: View {
    let state: HikeState

    var body: some View {
        if state.estimatedTrailEndAt != nil || state.estimatedBackAt != nil {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { items }
                VStack(alignment: .leading, spacing: 2) { items }
            }
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var items: some View {
        if let end = state.estimatedTrailEndAt {
            Text("Trail end ~\(end.formatted(date: .omitted, time: .shortened))")
        }
        if let back = state.estimatedBackAt {
            Text("Back by ~\(back.formatted(date: .omitted, time: .shortened))")
        }
    }
}

struct HikeProgressRow: View {
    let state: HikeState

    var body: some View {
        if let progress = state.progress, let length = state.trailLengthText {
            VStack(alignment: .leading, spacing: 4) {
                ProgressView(value: progress)
                    .tint(state.status.tint)
                Text("\(state.distanceText) of \(length)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct HikeLockScreenView: View {
    let attributes: HikeActivityAttributes
    let state: HikeState
    let isStale: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "figure.hiking")
                    .foregroundStyle(state.status.tint)
                VStack(alignment: .leading, spacing: 0) {
                    Text(state.title).font(.headline).lineLimit(1)
                    if let subtitle = state.subtitle {
                        Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 8)
                if let pill = state.status.pillText {
                    Text(pill)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(state.status.tint.opacity(0.25), in: Capsule())
                        .foregroundStyle(state.status.tint)
                } else {
                    Text("Recording").font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack(alignment: .firstTextBaseline) {
                HikeTimerText(startedAt: attributes.startedAt, state: state)
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    .minimumScaleFactor(0.7)
                Spacer(minLength: 8)
                Text(state.distanceText).font(.title3.weight(.medium))
            }
            if state.status != .finished {
                HikeProgressRow(state: state)
                if isStale {
                    Text("Updating paused. Open TrekDex to refresh.")
                        .font(.footnote).foregroundStyle(.secondary)
                } else {
                    HikeEstimatesRow(state: state)
                }
            }
        }
        .padding()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(HikeActivityAccessibility.label(for: state))
        .accessibilityValue(Text(timerInterval: attributes.startedAt...Date.distantFuture,
                                 pauseTime: state.endedAt, countsDown: false))
    }
}
