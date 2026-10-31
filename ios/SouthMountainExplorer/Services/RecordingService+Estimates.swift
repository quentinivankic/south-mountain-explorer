import Foundation

/// Mutable holder for the estimator, owned by `RecordingService`.
struct HikeEstimateState {
    var estimator = HikeEstimator()
    var geometry: TrailGeometry? = nil
    var trailKey: String? = nil
    /// `startedAt` of the recording the estimator was built for.
    var recordingKey: Date? = nil
    var estimates: HikeEstimates? = nil
    var lastRefreshAt: Date? = nil
}

extension RecordingService {
    /// Minimum seconds between recomputes.
    static let estimateThrottleSeconds: TimeInterval = 3

    /// Latest Remaining/Back estimates for the active recording, nil when there
    /// is none (or nothing computed yet). Works with the recording panel closed.
    var estimates: HikeEstimates? {
        activeRecording == nil ? nil : estimateState.estimates
    }

    /// Point the estimator at the selected trail (nil for roam). Resets progress.
    /// Call on panel appear and whenever the recording's trail changes.
    func setEstimateTrail(_ trail: Trail?) {
        estimateState.geometry = trail.flatMap { TrailGeometry(trail: $0) }
        estimateState.trailKey = trail?.id
        estimateState.estimator = HikeEstimator()
        estimateState.estimates = nil
        estimateState.lastRefreshAt = nil
    }

    /// Recompute (throttled) from the recording path. `lastFixAt` is the time of
    /// the last fresh GPS fix; pass nil when unknown.
    func refreshEstimates(now: Date = Date(), lastFixAt: Date?) {
        guard let rec = activeRecording else { return }
        if estimateState.recordingKey != rec.startedAt {
            let geometry = estimateState.geometry
            let key = estimateState.trailKey
            estimateState = HikeEstimateState()
            estimateState.geometry = geometry
            estimateState.trailKey = key
            estimateState.recordingKey = rec.startedAt
        }
        if let last = estimateState.lastRefreshAt,
           now.timeIntervalSince(last) >= 0, now.timeIntervalSince(last) < Self.estimateThrottleSeconds {
            return
        }
        estimateState.lastRefreshAt = now
        estimateState.estimates = estimateState.estimator.update(
            path: rec.path, geometry: estimateState.geometry, now: now, lastFixAt: lastFixAt)
    }
}
