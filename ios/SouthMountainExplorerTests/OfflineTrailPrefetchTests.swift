import Testing
@testable import SouthMountainExplorer

@MainActor
struct OfflineTrailPrefetchTests {
    @Test func executorIsSequentialAndReportsCompletePartialAndTotalFailure() async {
        var available: Set<String> = []
        var attempted: [String] = []
        let outcomes = ["a": true, "b": false, "c": true]
        let prefetcher = OfflineTrailPrefetcher(
            isDurablyAvailable: { available.contains($0) },
            fetchAndVerify: { id in
                attempted.append(id)
                if outcomes[id] == true { available.insert(id) }
                return outcomes[id] == true
            }
        )

        let partial = await prefetcher.run(ids: ["a", "b", "c"], forceRefresh: false)
        #expect(attempted == ["a", "b", "c"])
        #expect(partial.succeededIDs == ["a", "c"])
        #expect(partial.failedIDs == ["b"])

        let current = await prefetcher.run(ids: ["a", "c"], forceRefresh: false)
        #expect(current.alreadyCurrentIDs == ["a", "c"])
        #expect(attempted == ["a", "b", "c"], "already-current IDs must not fetch")

        let failed = await prefetcher.run(ids: ["b"], forceRefresh: true)
        #expect(failed.failedIDs == ["b"])
        #expect(attempted.last == "b")
    }

    @Test func retryProcessesOnlyPriorFailures() async {
        var available: Set<String> = ["a"]
        var attempted: [String] = []
        let prefetcher = OfflineTrailPrefetcher(
            isDurablyAvailable: { available.contains($0) },
            fetchAndVerify: { id in
                attempted.append(id)
                available.insert(id)
                return true
            }
        )
        let prior = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b"],
            succeededIDs: ["a"],
            failedIDs: ["b"]
        )

        let retry = await prefetcher.run(ids: prior.retryIDs, forceRefresh: true)

        #expect(attempted == ["b"])
        #expect(retry.succeededIDs == ["b"])
        #expect(retry.isCompleteDurableAvailability)
    }

    @Test func completePartialFailureAndAlreadyCurrentRemainDistinct() {
        let complete = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b"],
            succeededIDs: ["a", "b"]
        )
        let partial = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b"],
            succeededIDs: ["a"],
            failedIDs: ["b"]
        )
        let failed = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b"],
            failedIDs: ["a", "b"]
        )
        let current = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b"],
            alreadyCurrentIDs: ["a", "b"]
        )

        #expect(complete.isCompleteDurableAvailability)
        #expect(partial.isPartial)
        #expect(partial.retryIDs == ["b"])
        #expect(!failed.isPartial)
        #expect(!failed.isCompleteDurableAvailability)
        #expect(current.isCompleteDurableAvailability)
        #expect(current.succeededIDs.isEmpty)
    }

    @Test func stableDeduplicationOrderingAndDisjointOutcomesFailClosed() {
        let result = OfflineTrailPrefetchResult(
            requestedIDs: ["b", "a", "b", "c", "d"],
            succeededIDs: ["c", "b", "outside"],
            failedIDs: ["a", "b"],
            alreadyCurrentIDs: ["d", "c"]
        )

        #expect(result.requestedIDs == ["b", "a", "c", "d"])
        #expect(result.succeededIDs == ["b", "c"])
        #expect(result.alreadyCurrentIDs == ["d"])
        #expect(result.failedIDs == ["a"])
        #expect(result.processedCount == result.requestedIDs.count)
    }

    @Test func missingOutcomeIsClassifiedFailedAndRetryable() {
        let result = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b"],
            succeededIDs: ["a"]
        )
        #expect(result.failedIDs == ["b"])
        #expect(result.retryIDs == ["b"])
        #expect(!result.isCompleteDurableAvailability)
    }

    @Test func progressReportsVerifiedOutcomesNotAttempts() {
        let result = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b", "c"],
            succeededIDs: ["a"],
            failedIDs: ["b"],
            alreadyCurrentIDs: ["c"]
        )
        #expect(result.progress == OfflineTrailPrefetchProgress(
            processedCount: 3,
            totalCount: 3,
            succeededCount: 1,
            failedCount: 1,
            alreadyCurrentCount: 1
        ))
    }

    @Test func cooldownAdvancesOnlyForCompleteDurableAvailabilityOrNoTargets() {
        let complete = NearbyOfflineTrailPrefetchResult.completed(
            OfflineTrailPrefetchResult(requestedIDs: ["a"], succeededIDs: ["a"])
        )
        let current = NearbyOfflineTrailPrefetchResult.completed(
            OfflineTrailPrefetchResult(requestedIDs: ["a"], alreadyCurrentIDs: ["a"])
        )
        let empty = NearbyOfflineTrailPrefetchResult.completed(
            OfflineTrailPrefetchResult(requestedIDs: [])
        )
        let partial = NearbyOfflineTrailPrefetchResult.completed(
            OfflineTrailPrefetchResult(
                requestedIDs: ["a", "b"], succeededIDs: ["a"], failedIDs: ["b"]
            )
        )
        let failed = NearbyOfflineTrailPrefetchResult.completed(
            OfflineTrailPrefetchResult(requestedIDs: ["a"], failedIDs: ["a"])
        )

        #expect(complete.shouldAdvanceCooldown)
        #expect(current.shouldAdvanceCooldown)
        #expect(empty.shouldAdvanceCooldown)
        #expect(!partial.shouldAdvanceCooldown)
        #expect(!failed.shouldAdvanceCooldown)
        #expect(!NearbyOfflineTrailPrefetchResult.skipped(.movementCooldown).shouldAdvanceCooldown)
    }

    @Test func backgroundRequiresUnmeteredNetworkButForcedUserRunIsAllowed() {
        #expect(NearbyOfflineTrailPrefetchResult.networkSkip(
            isOnUnmeteredNetwork: false,
            isExpensive: true,
            force: false
        ) == .expensiveNetwork)
        #expect(NearbyOfflineTrailPrefetchResult.networkSkip(
            isOnUnmeteredNetwork: false,
            isExpensive: false,
            force: false
        ) == .networkUnavailable)
        #expect(NearbyOfflineTrailPrefetchResult.networkSkip(
            isOnUnmeteredNetwork: false,
            isExpensive: true,
            force: true
        ) == nil)
    }
}
