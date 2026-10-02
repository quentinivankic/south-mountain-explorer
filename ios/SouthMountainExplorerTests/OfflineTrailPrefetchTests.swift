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
        let firstAttemptsMatch = attempted == ["a", "b", "c"]
        #expect(firstAttemptsMatch, "The first prefetch attempts were not sequential")
        let partialSuccessesMatch = partial.succeededIDs == ["a", "c"]
        #expect(partialSuccessesMatch, "Partial prefetch reported unexpected successful identifiers")
        let partialFailuresMatch = partial.failedIDs == ["b"]
        #expect(partialFailuresMatch, "Partial prefetch reported unexpected failed identifiers")

        let current = await prefetcher.run(ids: ["a", "c"], forceRefresh: false)
        let currentIdentifiersMatch = current.alreadyCurrentIDs == ["a", "c"]
        #expect(currentIdentifiersMatch, "Prefetch reported unexpected already-current identifiers")
        let currentAttemptsWereSkipped = attempted == ["a", "b", "c"]
        #expect(currentAttemptsWereSkipped, "Already-current identifiers must not fetch")

        let failed = await prefetcher.run(ids: ["b"], forceRefresh: true)
        let forcedFailuresMatch = failed.failedIDs == ["b"]
        #expect(forcedFailuresMatch, "Forced prefetch reported unexpected failed identifiers")
        let forcedAttemptMatches = attempted.last == "b"
        #expect(forcedAttemptMatches, "Forced prefetch attempted an unexpected identifier")
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

        let retryAttemptsMatch = attempted == ["b"]
        #expect(retryAttemptsMatch, "Retry attempted identifiers outside the prior failures")
        let retrySuccessesMatch = retry.succeededIDs == ["b"]
        #expect(retrySuccessesMatch, "Retry reported unexpected successful identifiers")
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
        let partialRetryIdentifiersMatch = partial.retryIDs == ["b"]
        #expect(partialRetryIdentifiersMatch, "Partial result reported unexpected retry identifiers")
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

        let requestedOrderMatches = result.requestedIDs == ["b", "a", "c", "d"]
        #expect(requestedOrderMatches, "Requested identifiers lost stable deduplication order")
        let successfulIdentifiersMatch = result.succeededIDs == ["b", "c"]
        #expect(successfulIdentifiersMatch, "Result reported unexpected successful identifiers")
        let currentIdentifiersMatch = result.alreadyCurrentIDs == ["d"]
        #expect(currentIdentifiersMatch, "Result reported unexpected already-current identifiers")
        let failedIdentifiersMatch = result.failedIDs == ["a"]
        #expect(failedIdentifiersMatch, "Result reported unexpected failed identifiers")
        #expect(result.processedCount == result.requestedIDs.count)
    }

    @Test func missingOutcomeIsClassifiedFailedAndRetryable() {
        let result = OfflineTrailPrefetchResult(
            requestedIDs: ["a", "b"],
            succeededIDs: ["a"]
        )
        let failedIdentifiersMatch = result.failedIDs == ["b"]
        #expect(failedIdentifiersMatch, "Missing outcome produced unexpected failed identifiers")
        let retryIdentifiersMatch = result.retryIDs == ["b"]
        #expect(retryIdentifiersMatch, "Missing outcome produced unexpected retry identifiers")
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
