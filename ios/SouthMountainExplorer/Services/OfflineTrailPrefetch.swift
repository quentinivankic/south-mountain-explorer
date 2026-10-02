import Foundation

struct OfflineTrailPrefetchProgress: Equatable, Sendable {
    let processedCount: Int
    let totalCount: Int
    let succeededCount: Int
    let failedCount: Int
    let alreadyCurrentCount: Int

    init(
        processedCount: Int,
        totalCount: Int,
        succeededCount: Int = 0,
        failedCount: Int = 0,
        alreadyCurrentCount: Int = 0
    ) {
        self.processedCount = processedCount
        self.totalCount = totalCount
        self.succeededCount = succeededCount
        self.failedCount = failedCount
        self.alreadyCurrentCount = alreadyCurrentCount
    }
}

struct OfflineTrailPrefetchResult: Equatable, Sendable {
    let requestedIDs: [String]
    let succeededIDs: [String]
    let failedIDs: [String]
    let alreadyCurrentIDs: [String]

    init(
        requestedIDs: [String],
        succeededIDs: [String] = [],
        failedIDs: [String] = [],
        alreadyCurrentIDs: [String] = []
    ) {
        let requested = Self.stableUnique(requestedIDs)
        let requestedSet = Set(requested)
        let succeededSet = Set(succeededIDs).intersection(requestedSet)
        let alreadySet = Set(alreadyCurrentIDs)
            .intersection(requestedSet)
            .subtracting(succeededSet)
        let explicitFailedSet = Set(failedIDs)
            .intersection(requestedSet)
            .subtracting(succeededSet)
            .subtracting(alreadySet)
        let classified = succeededSet.union(alreadySet).union(explicitFailedSet)
        let failedSet = explicitFailedSet.union(requestedSet.subtracting(classified))

        self.requestedIDs = requested
        self.succeededIDs = requested.filter(succeededSet.contains)
        self.alreadyCurrentIDs = requested.filter(alreadySet.contains)
        self.failedIDs = requested.filter(failedSet.contains)
    }

    var processedCount: Int {
        succeededIDs.count + failedIDs.count + alreadyCurrentIDs.count
    }

    var progress: OfflineTrailPrefetchProgress {
        OfflineTrailPrefetchProgress(
            processedCount: processedCount,
            totalCount: requestedIDs.count,
            succeededCount: succeededIDs.count,
            failedCount: failedIDs.count,
            alreadyCurrentCount: alreadyCurrentIDs.count
        )
    }

    /// Empty target sets are successful no-ops. Every non-empty request must
    /// have a verified durable success/current receipt and no failures.
    var isCompleteDurableAvailability: Bool {
        failedIDs.isEmpty && processedCount == requestedIDs.count
    }

    var retryIDs: [String] { failedIDs }

    var isPartial: Bool {
        !failedIDs.isEmpty && succeededIDs.count + alreadyCurrentIDs.count > 0
    }

    static func stableUnique(_ ids: [String]) -> [String] {
        var seen = Set<String>()
        return ids.filter { seen.insert($0).inserted }
    }
}

@MainActor
struct OfflineTrailPrefetcher {
    let isDurablyAvailable: (String) -> Bool
    let fetchAndVerify: (String) async -> Bool

    func run(
        ids: [String],
        forceRefresh: Bool,
        progress: ((OfflineTrailPrefetchProgress) async -> Void)? = nil
    ) async -> OfflineTrailPrefetchResult {
        let requested = OfflineTrailPrefetchResult.stableUnique(ids)
        var succeeded: [String] = []
        var failed: [String] = []
        var alreadyCurrent: [String] = []

        await progress?(OfflineTrailPrefetchProgress(
            processedCount: 0,
            totalCount: requested.count
        ))
        for id in requested {
            if !forceRefresh, isDurablyAvailable(id) {
                alreadyCurrent.append(id)
            } else if await fetchAndVerify(id), isDurablyAvailable(id) {
                succeeded.append(id)
            } else {
                failed.append(id)
            }

            await progress?(OfflineTrailPrefetchProgress(
                processedCount: succeeded.count + failed.count + alreadyCurrent.count,
                totalCount: requested.count,
                succeededCount: succeeded.count,
                failedCount: failed.count,
                alreadyCurrentCount: alreadyCurrent.count
            ))
            await Task.yield()
        }

        return OfflineTrailPrefetchResult(
            requestedIDs: requested,
            succeededIDs: succeeded,
            failedIDs: failed,
            alreadyCurrentIDs: alreadyCurrent
        )
    }
}

enum OfflineTrailPrefetchSkipReason: String, Equatable, Sendable {
    case noLocation
    case networkUnavailable
    case expensiveNetwork
    case movementCooldown
}

enum NearbyOfflineTrailPrefetchResult: Equatable, Sendable {
    case completed(OfflineTrailPrefetchResult)
    case skipped(OfflineTrailPrefetchSkipReason)

    var prefetchResult: OfflineTrailPrefetchResult? {
        guard case .completed(let result) = self else { return nil }
        return result
    }

    var shouldAdvanceCooldown: Bool {
        guard case .completed(let result) = self else { return false }
        return result.isCompleteDurableAvailability
    }

    static func networkSkip(isOnUnmeteredNetwork: Bool, isExpensive: Bool, force: Bool) -> OfflineTrailPrefetchSkipReason? {
        guard !force else { return nil }
        if isExpensive { return .expensiveNetwork }
        if !isOnUnmeteredNetwork { return .networkUnavailable }
        return nil
    }
}
