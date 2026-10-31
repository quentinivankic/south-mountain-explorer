import ActivityKit
import SwiftUI
import WidgetKit

struct HikeLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: HikeActivityAttributes.self) { context in
            HikeLockScreenView(attributes: context.attributes,
                               state: context.state,
                               isStale: context.isStale)
                .activityBackgroundTint(Color.black.opacity(0.85))
                .activitySystemActionForegroundColor(.white)
                .widgetURL(LiveActivityDeepLink.activeHikeURL)
        } dynamicIsland: { context in
            let state = context.state
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 4) {
                        Image(systemName: "figure.hiking").foregroundStyle(state.status.tint)
                        Text(state.status.pillText ?? "Recording")
                            .font(.caption)
                            .lineLimit(2)
                    }
                    .accessibilityElement(children: .combine)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    HikeTimerText(startedAt: context.attributes.startedAt, state: state)
                        .font(.headline)
                        .frame(maxWidth: 90, alignment: .trailing)
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(state.title).font(.headline).lineLimit(1)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(state.distanceText).font(.headline)
                        HikeProgressRow(state: state)
                        if !context.isStale { HikeEstimatesRow(state: state) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(HikeActivityAccessibility.label(for: state))
                }
            } compactLeading: {
                Image(systemName: "figure.hiking")
                    .foregroundStyle(state.status.tint)
                    .accessibilityLabel(HikeActivityAccessibility.compactLabel(for: state.status))
            } compactTrailing: {
                if state.status.isDegraded {
                    Image(systemName: "location.slash")
                        .foregroundStyle(.orange)
                        .accessibilityLabel("GPS paused")
                } else {
                    HikeTimerText(startedAt: context.attributes.startedAt, state: state)
                        .font(.caption.weight(.semibold))
                        .minimumScaleFactor(0.8)
                        .frame(maxWidth: 52)
                }
            } minimal: {
                Image(systemName: "figure.hiking")
                    .foregroundStyle(state.status.tint)
                    .accessibilityLabel(HikeActivityAccessibility.compactLabel(for: state.status))
            }
            .keylineTint(state.status.tint)
            .widgetURL(LiveActivityDeepLink.activeHikeURL)
        }
    }
}
