import Foundation

struct LiveActivityRecordingIdentity: Equatable, Sendable {
    var id: String
    var startedAt: Date

    /// Stable id for legacy recordings that were persisted without one.
    static func fallbackId(startedAt: Date) -> String {
        "legacy-\(Int(startedAt.timeIntervalSince1970))"
    }
}

struct LiveActivityExisting: Equatable, Sendable {
    var id: String
    var recordingId: String
    var isEnded: Bool
}

/// Pure start / adopt / update / end decisions.
enum LiveActivityLifecycle {
    static let finishedDismissAfter: TimeInterval = 8

    struct Input: Equatable, Sendable {
        var recording: LiveActivityRecordingIdentity?
        /// `isStopping` was observed true while this recording was tracked.
        var sawStopping: Bool
        var activitiesEnabled: Bool
        var existing: [LiveActivityExisting]
        /// Recording id the coordinator was tracking before it vanished.
        var previouslyTrackedRecordingId: String?
        /// Activity this process created or adopted.
        var currentActivityId: String?
        var isRecoveryBlocked: Bool
        var userDismissedRecordingId: String?
    }

    enum Action: Equatable, Sendable {
        case start
        case adopt(activityId: String)
        case update(activityId: String)
        case endFinished(activityId: String, dismissAfter: TimeInterval)
        case endImmediately(activityId: String)
    }

    static func decide(_ input: Input) -> [Action] {
        let live = input.existing.filter { !$0.isEnded }

        guard let recording = input.recording else {
            var actions: [Action] = []
            var finishedUsed = false
            for activity in live {
                let isTracked = activity.recordingId == input.previouslyTrackedRecordingId
                if input.sawStopping, isTracked, !finishedUsed {
                    finishedUsed = true
                    actions.append(.endFinished(activityId: activity.id, dismissAfter: finishedDismissAfter))
                } else {
                    actions.append(.endImmediately(activityId: activity.id))
                }
            }
            return actions
        }

        if !input.activitiesEnabled {
            return live.map { .endImmediately(activityId: $0.id) }
        }

        var actions: [Action] = []
        let matching = live.filter { $0.recordingId == recording.id }
        for other in live where other.recordingId != recording.id {
            actions.append(.endImmediately(activityId: other.id))
        }

        if let keep = matching.first(where: { $0.id == input.currentActivityId }) ?? matching.first {
            for extra in matching where extra.id != keep.id {
                actions.append(.endImmediately(activityId: extra.id))
            }
            if !input.isRecoveryBlocked {
                actions.append(keep.id == input.currentActivityId
                               ? .update(activityId: keep.id)
                               : .adopt(activityId: keep.id))
            }
            return actions
        }

        if !input.isRecoveryBlocked, input.userDismissedRecordingId != recording.id {
            actions.append(.start)
        }
        return actions
    }
}
