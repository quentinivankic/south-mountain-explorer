import Foundation

/// Sliding-window concurrency: at most `limit` `work` calls in flight.
/// `onResult` is called serially, never concurrently. Checks cancellation and
/// `shouldContinue` before launching each item; in-flight items are drained.
enum BoundedConcurrentRunner {
    static func run<T: Sendable>(
        _ items: [String],
        limit: Int,
        shouldContinue: () -> Bool = { true },
        work: @escaping @Sendable (String) async -> T,
        onResult: (String, T) async -> Void
    ) async {
        let width = max(limit, 1)
        await withTaskGroup(of: (String, T).self) { group in
            var next = 0
            func launchNext() -> Bool {
                guard next < items.count, !Task.isCancelled, shouldContinue() else { return false }
                let id = items[next]
                next += 1
                group.addTask { (id, await work(id)) }
                return true
            }
            for _ in 0..<min(width, items.count) {
                if !launchNext() { break }
            }
            while let (id, result) = await group.next() {
                await onResult(id, result)
                _ = launchNext()
            }
        }
    }
}
