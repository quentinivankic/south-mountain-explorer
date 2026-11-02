import Foundation
import os

/// Cooperative cancel flag for a planning run ("Plan a Hike").
///
/// `Task.detached` does not inherit the parent's cancellation, so the
/// controller creates one token per run, flips it from a cancellation
/// handler, and binds it with `PlanCancellation.$current`. The planner loops
/// poll `PlanCancellation.isCancelled` and stop early. A cancelled run returns
/// a throwaway result that the caller must ignore.
final class PlanCancellation: Sendable {
    @TaskLocal static var current: PlanCancellation?

    private let flag = OSAllocatedUnfairLock(initialState: false)

    func cancel() { flag.withLock { $0 = true } }
    var isSet: Bool { flag.withLock { $0 } }

    /// true when the run bound to the current task has been cancelled.
    static var isCancelled: Bool { current?.isSet ?? false }

    /// Runs `work` on a detached task whose planner loops stop when the CALLING
    /// task is cancelled. Returns `work`'s result, or nil if cancelled.
    static func runDetached<T: Sendable>(
        priority: TaskPriority = .userInitiated, _ work: @escaping @Sendable () -> T
    ) async -> T? {
        let token = PlanCancellation()
        let value = await withTaskCancellationHandler {
            await Task.detached(priority: priority) {
                PlanCancellation.$current.withValue(token) { work() }
            }.value
        } onCancel: {
            token.cancel()
        }
        return token.isSet || Task.isCancelled ? nil : value
    }
}
