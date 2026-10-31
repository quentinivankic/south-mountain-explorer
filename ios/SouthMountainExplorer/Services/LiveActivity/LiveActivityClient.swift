import ActivityKit
import Foundation

/// Seam over ActivityKit so the coordinator can be tested with a fake.
@MainActor
protocol LiveActivityClient: AnyObject {
    var areActivitiesEnabled: Bool { get }
    func currentActivities() -> [LiveActivityExisting]
    /// Returns the new activity id. Throws on ActivityKit errors.
    func request(attributes: HikeActivityAttributes,
                 state: HikeActivityAttributes.ContentState,
                 staleDate: Date) throws -> String
    func update(id: String, state: HikeActivityAttributes.ContentState, staleDate: Date?) async
    /// `dismissAfter == nil` removes the activity immediately.
    func end(id: String, finalState: HikeActivityAttributes.ContentState?, dismissAfter: TimeInterval?) async
    func enablementUpdates() -> AsyncStream<Bool>
}

@MainActor
final class ActivityKitClient: LiveActivityClient {
    var areActivitiesEnabled: Bool { ActivityAuthorizationInfo().areActivitiesEnabled }

    func currentActivities() -> [LiveActivityExisting] {
        Activity<HikeActivityAttributes>.activities.map {
            LiveActivityExisting(
                id: $0.id,
                recordingId: $0.attributes.recordingId,
                isEnded: $0.activityState == .ended || $0.activityState == .dismissed
            )
        }
    }

    func request(attributes: HikeActivityAttributes,
                 state: HikeActivityAttributes.ContentState,
                 staleDate: Date) throws -> String {
        let content = ActivityContent(state: state, staleDate: staleDate)
        return try Activity.request(attributes: attributes, content: content, pushType: nil).id
    }

    func update(id: String, state: HikeActivityAttributes.ContentState, staleDate: Date?) async {
        await Self.performUpdate(id: id, content: ActivityContent(state: state, staleDate: staleDate))
    }

    func end(id: String, finalState: HikeActivityAttributes.ContentState?, dismissAfter: TimeInterval?) async {
        let content = finalState.map { ActivityContent(state: $0, staleDate: nil) }
        let policy: ActivityUIDismissalPolicy = dismissAfter.map { .after(Date().addingTimeInterval($0)) } ?? .immediate
        await Self.performEnd(id: id, content: content, policy: policy)
    }

    // `Activity` is not Sendable, so the lookup and the async call both stay
    // inside these nonisolated helpers instead of crossing actors.
    private nonisolated static func performUpdate(id: String, content: ActivityContent<HikeActivityAttributes.ContentState>) async {
        guard let activity = Activity<HikeActivityAttributes>.activities.first(where: { $0.id == id }) else { return }
        await activity.update(content)
    }

    private nonisolated static func performEnd(id: String, content: ActivityContent<HikeActivityAttributes.ContentState>?,
                                               policy: ActivityUIDismissalPolicy) async {
        guard let activity = Activity<HikeActivityAttributes>.activities.first(where: { $0.id == id }) else { return }
        await activity.end(content, dismissalPolicy: policy)
    }

    func enablementUpdates() -> AsyncStream<Bool> {
        AsyncStream { continuation in
            let task = Task {
                for await enabled in ActivityAuthorizationInfo().activityEnablementUpdates {
                    continuation.yield(enabled)
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
