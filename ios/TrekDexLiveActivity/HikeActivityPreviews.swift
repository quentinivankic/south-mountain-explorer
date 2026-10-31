import SwiftUI
import WidgetKit

#if DEBUG
private let previewAttributes = HikeActivityAttributes(
    recordingId: "preview", startedAt: Date().addingTimeInterval(-4320), isWalk: false)

private func previewState(_ status: HikeState.Status) -> HikeState {
    HikeState(status: status, title: "Hidden Valley Trail", subtitle: "South Mountain",
              distanceText: "1.20 mi", trailLengthText: "2.40 mi", progress: 0.5,
              estimatedTrailEndAt: status.isDegraded ? nil : Date().addingTimeInterval(1500),
              estimatedBackAt: status.isDegraded ? nil : Date().addingTimeInterval(2400),
              endedAt: status == .finished ? Date() : nil)
}

#Preview("Lock Screen", as: .content, using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
    previewState(.gpsPaused)
    previewState(.gpsRecovered)
    previewState(.finished)
}

#Preview("Island expanded", as: .dynamicIsland(.expanded), using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
    previewState(.gpsPaused)
}

#Preview("Island compact", as: .dynamicIsland(.compact), using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
    previewState(.gpsPaused)
}

#Preview("Island minimal", as: .dynamicIsland(.minimal), using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
}
#endif
