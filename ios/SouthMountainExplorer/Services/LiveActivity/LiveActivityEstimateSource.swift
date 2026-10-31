import Foundation
/// Read-only adapter over `RecordingService.estimates` (the hike-estimates
/// model). It never computes anything itself. Remaining = finish the selected
/// trail along the trail; Back = return to the start. Only fresh values
/// (`ok` / `approximate`) are forwarded; stale, off-trail, estimating, arrived
/// and unavailable states become nil so the Live Activity hides them rather
/// than show a wrong countdown.
@MainActor
enum LiveActivityEstimateSource {
    static func estimates(for rec: ActiveRecording,
                          recording: RecordingService,
                          now: Date = Date()) -> LiveActivityEstimates {
        guard recording.estimateState.recordingKey == rec.startedAt,
              let est = recording.estimates else { return .none }
        return LiveActivityEstimates(
            trailEndSeconds: rec.mode == .trail ? seconds(est.remaining, now: now) : nil,
            backSeconds: seconds(est.back, now: now))
    }
    static func seconds(_ e: HikeEstimate?, now: Date) -> TimeInterval? {
        guard let e, e.hasValue, e.status == .ok || e.status == .approximate,
              let eta = e.etaDate else { return nil }
        let s = eta.timeIntervalSince(now)
        return s.isFinite && s > 0 ? s : nil
    }
}
