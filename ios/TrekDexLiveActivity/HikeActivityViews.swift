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
            Text(HikeActivityClock.label("Finish", end, approximate: state.estimatedTrailEndApprox))
                .lineLimit(1)
        }
        if let back = state.estimatedBackAt {
            Text(HikeActivityClock.label("Back by", back, approximate: state.estimatedBackApprox))
                .lineLimit(1)
        }
    }
}

extension HikeActivityAttributes.NextTurnArrow {
    var symbolName: String {
        switch self {
        case .keepLeft: return "arrow.up.left"
        case .keepRight: return "arrow.up.right"
        case .turnLeft, .sharpLeft: return "arrow.turn.up.left"
        case .turnRight, .sharpRight: return "arrow.turn.up.right"
        case .offTrail: return "exclamationmark.triangle.fill"
        case .backOnTrail: return "checkmark.circle.fill"
        }
    }
    var tint: Color {
        switch self {
        case .offTrail: return .orange
        case .backOnTrail: return .green
        default: return .cyan
        }
    }
}

/// Next-turn row: arrow, short text, target trail. Hidden when nil.
struct HikeNextTurnRow: View {
    let state: HikeState
    var body: some View {
        if let arrow = state.nextTurnArrow, let text = state.nextTurnText {
            HStack(spacing: 8) {
                Image(systemName: arrow.symbolName)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(arrow.tint)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 0) {
                    Text(text)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(2)
                        .minimumScaleFactor(0.8)
                    if let trail = state.nextTurnTrail {
                        Text(trail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                }
                Spacer(minLength: 0)
            }
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
                if !isStale { HikeNextTurnRow(state: state) }
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
