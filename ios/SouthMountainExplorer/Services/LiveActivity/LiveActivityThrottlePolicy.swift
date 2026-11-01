import Foundation

/// Pure update-throttling policy. A GPS fix (about every 2 s) never triggers
/// an update by itself; see `Config` for the cadence.
enum LiveActivityThrottlePolicy {
    typealias ContentState = HikeActivityAttributes.ContentState

    struct Config: Equatable, Sendable {
        var minSpacing: TimeInterval = 5
        var distanceMeters: Double = 50
        var distanceSpacing: TimeInterval = 15
        var heartbeat: TimeInterval = 60
        var estimateShiftSeconds: TimeInterval = 2 * 60
    }

    struct SentRecord: Equatable, Sendable {
        var state: ContentState
        var sentAt: Date
        var distanceMetersAtSend: Double
    }

    enum Reason: Equatable, Sendable {
        case initial, finished, stateChange, distance, estimateShift, heartbeat, cueChange, cueDistance
    }

    enum Decision: Equatable, Sendable {
        case skip
        case send(Reason)
    }

    static func decide(lastSent: SentRecord?,
                       candidate: ContentState,
                       candidateDistanceMeters: Double,
                       now: Date,
                       config: Config = Config()) -> Decision {
        guard let last = lastSent else { return .send(.initial) }
        if candidate.status == .finished { return .send(.finished) }

        let elapsed = now.timeIntervalSince(last.sentAt)
        if elapsed < config.minSpacing { return .skip }

        let old = last.state
        if candidate.status != old.status
            || candidate.title != old.title
            || candidate.subtitle != old.subtitle
            || (candidate.estimatedTrailEndAt == nil) != (old.estimatedTrailEndAt == nil)
            || (candidate.estimatedBackAt == nil) != (old.estimatedBackAt == nil) {
            return .send(.stateChange)
        }

        // Next-turn row is event-driven: a new cue (arrow, target trail,
        // appearing or clearing) sends promptly; a coarse distance step
        // ("300 ft" -> "200 ft") is rate-limited like distance updates.
        if candidate.nextTurnArrow != old.nextTurnArrow
            || candidate.nextTurnTrail != old.nextTurnTrail
            || (candidate.nextTurnText == nil) != (old.nextTurnText == nil)
            || (candidate.nextTurnDistanceText == "Now") != (old.nextTurnDistanceText == "Now") {
            return .send(.cueChange)
        }

        if elapsed >= config.distanceSpacing {
            if candidate.nextTurnText != old.nextTurnText {
                return .send(.cueDistance)
            }
            if candidateDistanceMeters - last.distanceMetersAtSend >= config.distanceMeters {
                return .send(.distance)
            }
            if shifted(candidate.estimatedTrailEndAt, old.estimatedTrailEndAt, config.estimateShiftSeconds)
                || shifted(candidate.estimatedBackAt, old.estimatedBackAt, config.estimateShiftSeconds) {
                return .send(.estimateShift)
            }
        }

        if elapsed >= config.heartbeat { return .send(.heartbeat) }
        return .skip
    }

    private static func shifted(_ a: Date?, _ b: Date?, _ threshold: TimeInterval) -> Bool {
        guard let a, let b else { return false }
        return abs(a.timeIntervalSince(b)) >= threshold
    }
}
