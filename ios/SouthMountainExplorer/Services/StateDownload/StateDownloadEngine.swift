import Foundation

/// Why a run stopped before every id was classified.
enum EngineStopReason: Equatable, Sendable {
    case finished
    case cancelled
    /// Breaker tripped or the OS refused the network (cellular off, offline).
    case waitingForNetwork
}

/// Per-result event streamed to the caller (serial, never concurrent).
struct EngineEvent: Sendable {
    let id: String
    let outcome: AreaOutcome
}

struct EngineResult: Equatable, Sendable {
    /// Newly written and verified files.
    var stored: [String: Int] = [:]          // id -> bytes
    var etags: [String: String] = [:]
    var unchanged: [String] = []             // conditional update said 304
    var alreadyOnDevice: [String] = []       // valid before this run, skipped
    var failed: [String: FailureKind] = [:]
    /// Not attempted, cancelled, or stalled: still to do.
    var pending: [String] = []
    var stopReason: EngineStopReason = .finished

    var verifiedCount: Int { stored.count + unchanged.count + alreadyOnDevice.count }

    /// True only when everything requested is verified and nothing failed.
    var isComplete: Bool { failed.isEmpty && pending.isEmpty && stopReason == .finished }
}

/// Runs the per-area fetch over a list of ids: skip-if-valid, bounded
/// concurrency, one retry for transient errors, and a stall breaker.
struct StateDownloadEngine: Sendable {
    static let defaultLimit = 4
    static let breakerThreshold = 8

    var limit: Int = StateDownloadEngine.defaultLimit
    var breakerThreshold: Int = StateDownloadEngine.breakerThreshold
    var retryDelay: Duration = .milliseconds(500)
    let isAvailable: @Sendable (String) -> Bool
    /// `etag` is non-nil only for conditional updates.
    let fetch: @Sendable (_ id: String, _ etag: String?) async -> AreaOutcome

    func run(
        ids: [String],
        forceUpdate: Bool,
        etags: [String: String] = [:],
        onEvent: @Sendable (EngineEvent) async -> Void = { _ in }
    ) async -> EngineResult {
        let requested = OfflineTrailPrefetchResult.stableUnique(ids)
        var result = EngineResult()
        var toFetch: [String] = []
        for id in requested {
            if !forceUpdate, isAvailable(id) {
                result.alreadyOnDevice.append(id)
            } else {
                toFetch.append(id)
            }
        }

        var handled = Set<String>()
        var streak: [(String, FailureKind)] = []
        var consecutiveTransient = 0
        var stalled = false

        let fetch = self.fetch
        let delay = retryDelay
        let work: @Sendable (String) async -> AreaOutcome = { id in
            let tag = forceUpdate ? etags[id] : nil
            var outcome = await fetch(id, tag)
            if case .failed(let kind) = outcome, kind.isTransient, !Task.isCancelled {
                try? await Task.sleep(for: delay)
                if Task.isCancelled { return .cancelled }
                outcome = await fetch(id, tag)
            }
            return outcome
        }

        await BoundedConcurrentRunner.run(
            toFetch,
            limit: limit,
            shouldContinue: { !stalled },
            work: work,
            onResult: { id, outcome in
                await onEvent(EngineEvent(id: id, outcome: outcome))
                switch outcome {
                case .stored(let bytes, let etag):
                    handled.insert(id)
                    result.stored[id] = bytes
                    if let etag { result.etags[id] = etag }
                    Self.flush(&streak, into: &result, handled: &handled)
                    consecutiveTransient = 0
                case .unchanged:
                    handled.insert(id)
                    result.unchanged.append(id)
                    Self.flush(&streak, into: &result, handled: &handled)
                    consecutiveTransient = 0
                case .failed(let kind):
                    if kind.isTransient {
                        streak.append((id, kind))
                        consecutiveTransient += 1
                        if consecutiveTransient >= breakerThreshold { stalled = true }
                    } else {
                        handled.insert(id)
                        result.failed[id] = kind
                    }
                case .cancelled:
                    break
                case .policyBlocked:
                    stalled = true
                }
            }
        )

        if stalled {
            // The streak that tripped the breaker is not counted as failures:
            // those ids stay pending so a resume tries them again.
            result.stopReason = .waitingForNetwork
        } else {
            Self.flush(&streak, into: &result, handled: &handled)
            result.stopReason = Task.isCancelled ? .cancelled : .finished
        }
        result.pending = toFetch.filter { !handled.contains($0) }
        if result.stopReason == .finished, !result.pending.isEmpty {
            // Defensive: nothing should be pending after a clean finish.
            result.stopReason = .cancelled
        }
        return result
    }

    private static func flush(
        _ streak: inout [(String, FailureKind)],
        into result: inout EngineResult,
        handled: inout Set<String>
    ) {
        for (id, kind) in streak {
            result.failed[id] = kind
            handled.insert(id)
        }
        streak.removeAll()
    }
}
