import Foundation
import Testing
@testable import SouthMountainExplorer

/// Thread-safe fake backend for engine tests.
final class FakeAreaBackend: @unchecked Sendable {
    private let lock = NSLock()
    private var onDisk: Set<String>
    private var calls: [String: Int] = [:]
    private var inFlight = 0
    private(set) var peak = 0

    init(onDisk: Set<String> = []) { self.onDisk = onDisk }

    func has(_ id: String) -> Bool { lock.withLock { onDisk.contains(id) } }
    func put(_ id: String) { lock.withLock { _ = onDisk.insert(id) } }
    func callCount(_ id: String) -> Int { lock.withLock { calls[id, default: 0] } }
    var totalCalls: Int { lock.withLock { calls.values.reduce(0, +) } }
    var fetchedIDs: Set<String> { lock.withLock { Set(calls.keys) } }

    func enter(_ id: String) {
        lock.withLock {
            calls[id, default: 0] += 1
            inFlight += 1
            peak = max(peak, inFlight)
        }
    }

    func leave() { lock.withLock { inFlight -= 1 } }

    func engine(
        limit: Int = 4,
        delayMillis: Int = 0,
        breaker: Int = 8,
        behavior: @escaping @Sendable (String, Int) -> AreaOutcome = { _, _ in .stored(bytes: 100, etag: "\"e\"") }
    ) -> StateDownloadEngine {
        StateDownloadEngine(
            limit: limit,
            breakerThreshold: breaker,
            retryDelay: .milliseconds(0),
            isAvailable: { [self] in has($0) },
            fetch: { [self] id, _ in
                enter(id)
                defer { leave() }
                if delayMillis > 0 { try? await Task.sleep(for: .milliseconds(delayMillis)) }
                if Task.isCancelled { return .cancelled }
                let attempt = callCount(id)
                let outcome = behavior(id, attempt)
                if case .stored = outcome { put(id) }
                return outcome
            }
        )
    }
}

private func ids(_ n: Int) -> [String] { (0..<n).map { "area-\($0)" } }

struct BoundedConcurrentRunnerTests {
    @Test func peakInFlightNeverExceedsLimitAndEveryItemRunsOnce() async {
        for limit in [1, 4, 6] {
            let backend = FakeAreaBackend()
            let items = ids(40)
            var seen: [String] = []
            await BoundedConcurrentRunner.run(
                items, limit: limit,
                work: { id -> Int in
                    backend.enter(id)
                    try? await Task.sleep(for: .milliseconds(5))
                    backend.leave()
                    return 1
                },
                onResult: { id, _ in seen.append(id) }
            )
            #expect(backend.peak <= limit)
            if limit == 1 { #expect(backend.peak == 1) }
            #expect(Set(seen) == Set(items))
            #expect(seen.count == items.count)
            #expect(items.allSatisfy { backend.callCount($0) == 1 })
        }
    }

    @Test func emptyInputReturnsImmediately() async {
        var count = 0
        await BoundedConcurrentRunner.run([], limit: 4, work: { _ -> Int in 1 }, onResult: { _, _ in count += 1 })
        #expect(count == 0)
    }
}

struct StateDownloadEngineTests {
    @Test func resumeSkipsAlreadyVerifiedAreasAndFetchesOnlyTheRemainder() async {
        let all = ids(20)
        let backend = FakeAreaBackend(onDisk: Set(all.prefix(12)))
        let engine = backend.engine()
        let result = await engine.run(ids: all, forceUpdate: false)
        #expect(result.alreadyOnDevice.count == 12)
        #expect(result.stored.count == 8)
        #expect(backend.fetchedIDs == Set(all.suffix(8)))
        #expect(result.isComplete)
        #expect(result.verifiedCount == 20)

        // A second run after completion fetches nothing.
        let calls = backend.totalCalls
        let again = await engine.run(ids: all, forceUpdate: false)
        #expect(again.alreadyOnDevice.count == 20)
        #expect(backend.totalCalls == calls)
    }

    @Test func partialFirstRunThenResumeFetchesOnlyWhatIsMissing() async {
        let all = ids(10)
        let backend = FakeAreaBackend()
        // First run: the last 4 fail permanently.
        let first = await backend.engine(behavior: { id, _ in
            all.suffix(4).contains(id) ? .failed(.http(404)) : .stored(bytes: 1, etag: nil)
        }).run(ids: all, forceUpdate: false)
        #expect(first.stored.count == 6)
        #expect(first.failed.count == 4)
        let before = backend.totalCalls
        let second = await backend.engine().run(ids: all, forceUpdate: false)
        #expect(second.alreadyOnDevice.count == 6)
        #expect(second.stored.count == 4)
        #expect(backend.totalCalls - before == 4)
    }

    @Test func cancelStopsLaunchingAndNeverCountsCancelledAsFailureOrSuccess() async {
        let all = ids(60)
        let backend = FakeAreaBackend()
        let engine = backend.engine(limit: 4, delayMillis: 30)
        let task = Task { await engine.run(ids: all, forceUpdate: false) }
        try? await Task.sleep(for: .milliseconds(100))
        task.cancel()
        let result = await task.value
        #expect(result.stopReason == .cancelled)
        #expect(result.failed.isEmpty)
        #expect(!result.isComplete)
        #expect(result.stored.count < all.count)
        #expect(result.stored.count + result.pending.count == all.count)
        // Everything stored really is on "disk"; nothing pending is.
        #expect(result.stored.keys.allSatisfy { backend.has($0) })
        #expect(result.pending.allSatisfy { !backend.has($0) })
        // Resume finishes only the pending ids.
        let resumed = await backend.engine().run(ids: all, forceUpdate: false)
        #expect(resumed.isComplete)
        #expect(Set(resumed.stored.keys) == Set(result.pending))
    }

    @Test func failureAccountingNeverReportsCompleteAndRetryRunsOnlyTheFailed() async {
        let all = ids(50)
        let bad = Set(all.prefix(12))
        let backend = FakeAreaBackend()
        let result = await backend.engine(behavior: { id, _ in
            bad.contains(id) ? .failed(.http(404)) : .stored(bytes: 10, etag: nil)
        }).run(ids: all, forceUpdate: false)
        #expect(result.failed.count == 12)
        #expect(Set(result.failed.keys) == bad)
        #expect(result.stored.count + result.alreadyOnDevice.count + result.failed.count + result.pending.count == 50)
        #expect(!result.isComplete)

        let before = backend.totalCalls
        let retry = await backend.engine().run(ids: Array(result.failed.keys), forceUpdate: false)
        #expect(retry.isComplete)
        #expect(retry.stored.count == 12)
        #expect(backend.totalCalls - before == 12)
    }

    @Test func transientFailureIsRetriedOnceAndPermanentIsNot() async {
        let backend = FakeAreaBackend()
        let result = await backend.engine(behavior: { id, attempt in
            switch id {
            case "area-0": return attempt == 1 ? .failed(.timeout) : .stored(bytes: 1, etag: nil)
            case "area-1": return .failed(.http(404))
            default: return .failed(.http(503))
            }
        }).run(ids: ["area-0", "area-1", "area-2"], forceUpdate: false)
        #expect(backend.callCount("area-0") == 2)
        #expect(result.stored.keys.contains("area-0"))
        #expect(backend.callCount("area-1") == 1)
        #expect(result.failed["area-1"] == .http(404))
        #expect(backend.callCount("area-2") == 2)
        #expect(result.failed["area-2"] == .http(503))
    }

    @Test func breakerTripsAfterConsecutiveTransientFailuresWithoutRecordingFailures() async {
        let all = ids(30)
        let backend = FakeAreaBackend()
        let result = await backend.engine(limit: 1, breaker: 8, behavior: { _, _ in .failed(.network) })
            .run(ids: all, forceUpdate: false)
        #expect(result.stopReason == .waitingForNetwork)
        #expect(result.failed.isEmpty)
        #expect(result.pending.count == 30)
        #expect(!result.isComplete)
        // 8 areas x (attempt + one retry); the rest were never launched.
        #expect(backend.totalCalls == 16)
    }

    @Test func successResetsTheBreakerStreak() async {
        let all = ids(20)
        let backend = FakeAreaBackend()
        let result = await backend.engine(limit: 1, breaker: 4, behavior: { id, _ in
            // Every 3rd area succeeds, so the streak never reaches 4.
            (Int(id.dropFirst(5)) ?? 0) % 3 == 0 ? .stored(bytes: 1, etag: nil) : .failed(.network)
        }).run(ids: all, forceUpdate: false)
        #expect(result.stopReason == .finished)
        #expect(result.failed.count == 13)
        #expect(result.stored.count == 7)
    }

    @Test func policyBlockedStopsTheRunAndKeepsIDsPending() async {
        let all = ids(10)
        let backend = FakeAreaBackend()
        let result = await backend.engine(limit: 1, behavior: { _, _ in .policyBlocked })
            .run(ids: all, forceUpdate: false)
        #expect(result.stopReason == .waitingForNetwork)
        #expect(result.failed.isEmpty)
        #expect(result.pending.count == 10)
        #expect(backend.totalCalls == 1)
    }

    @Test func forceUpdateFetchesEverythingAndPassesETags() async {
        let all = ids(5)
        let backend = FakeAreaBackend(onDisk: Set(all))
        let seen = TagRecorder()
        let engine = StateDownloadEngine(
            retryDelay: .milliseconds(0),
            isAvailable: { backend.has($0) },
            fetch: { id, etag in
                seen.record(id, etag)
                return etag == nil ? .stored(bytes: 1, etag: "\"n\"") : .unchanged
            }
        )
        let result = await engine.run(ids: all, forceUpdate: true, etags: ["area-0": "\"x\"", "area-1": "\"y\""])
        #expect(seen.count == 5)
        #expect(seen.tag("area-0") == "\"x\"")
        #expect(result.unchanged.count == 2)
        #expect(result.stored.count == 3)
        #expect(result.alreadyOnDevice.isEmpty)
    }
}

final class TagRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var tags: [String: String?] = [:]
    func record(_ id: String, _ tag: String?) { lock.withLock { tags[id] = .some(tag) } }
    var count: Int { lock.withLock { tags.count } }
    func tag(_ id: String) -> String? { lock.withLock { tags[id] ?? nil } }
}
