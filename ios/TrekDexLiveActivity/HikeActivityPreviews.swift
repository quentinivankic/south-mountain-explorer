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

private func previewTurnState() -> HikeState {
    var s = previewState(.recording)
    s.nextTurnArrow = .turnLeft
    s.nextTurnText = "Turn left in 300 ft"
    s.nextTurnDistanceText = "300 ft"
    s.nextTurnTrail = "Crosscut Trail"
    s.nextTurnSpoken = "turn left in 300 feet onto Crosscut Trail"
    s.estimatedTrailEndApprox = true
    return s
}

#Preview("Lock Screen", as: .content, using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
    previewState(.gpsPaused)
    previewState(.gpsRecovered)
    previewState(.finished)
    previewTurnState()
}

#Preview("Island expanded", as: .dynamicIsland(.expanded), using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
    previewState(.gpsPaused)
    previewTurnState()
}

#Preview("Island compact", as: .dynamicIsland(.compact), using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
    previewState(.gpsPaused)
    previewTurnState()
}

#Preview("Island minimal", as: .dynamicIsland(.minimal), using: previewAttributes) {
    HikeLiveActivity()
} contentStates: {
    previewState(.recording)
}
#endif
