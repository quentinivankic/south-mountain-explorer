import Foundation
import Testing
@testable import SouthMountainExplorer

struct AreaCacheStoreTests {
    private func makeDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("AreaCacheStoreTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func area(id: String, name: String = "Area") -> Area {
        Area(
            id: id,
            name: name,
            subtitle: "AZ",
            centerLat: 33.3,
            centerLon: -112,
            zoom: 13,
            bbox: nil,
            trails: [Trail(
                id: "trail",
                name: "Trail",
                distanceMi: 1,
                difficulty: .easy,
                segments: [[[33.3, -112], [33.31, -112.01]]]
            )],
            trailCount: 1,
            totalMi: 1,
            cachedAt: nil
        )
    }

    private func encoded(_ area: Area) throws -> Data {
        try JSONEncoder().encode(area)
    }

    @Test func verifiedPromotionCreatesIdentityMatchingDurableFile() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = AreaCacheStore(cacheDirectory: directory)

        let receipt = store.store(area(id: "a"))

        #expect(receipt.succeeded)
        #expect(store.validArea(id: "a")?.id == "a")
        #expect(store.validBytes(id: "a") != nil)
    }

    @Test func malformedEmptyGeometryAndWrongIdentityAreRejected() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = AreaCacheStore(cacheDirectory: directory)
        let empty = Area(
            id: "a", name: "Empty", subtitle: "AZ",
            centerLat: 0, centerLon: 0, zoom: 13, bbox: nil,
            trails: [], trailCount: 0, totalMi: 0, cachedAt: nil
        )

        #expect(store.store(Data("{".utf8), expectedID: "a").failure == .invalidPayload)
        #expect(store.store(try encoded(empty), expectedID: "a").failure == .invalidPayload)
        #expect(store.store(try encoded(area(id: "other")), expectedID: "a").failure == .invalidPayload)
        #expect(store.validArea(id: "a") == nil)
    }

    @Test func stagingAndPromotionFailuresPreservePriorBytesExactly() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let live = AreaCacheStore(cacheDirectory: directory)
        #expect(live.store(area(id: "a", name: "Old")).succeeded)
        let oldBytes = try #require(live.validBytes(id: "a"))

        let stagingFailure = AreaCacheStore(
            cacheDirectory: directory,
            io: .init(
                read: { try Data(contentsOf: $0) },
                writeStaged: { _, _ in throw CocoaError(.fileWriteUnknown) },
                promote: AreaCacheStore.IO.live().promote,
                remove: { try FileManager.default.removeItem(at: $0) }
            )
        )
        #expect(stagingFailure.store(area(id: "a", name: "New")).failure == .stagingWriteFailed)
        #expect(try Data(contentsOf: live.fileURL(for: "a")) == oldBytes)

        let liveIO = AreaCacheStore.IO.live()
        let promotionFailure = AreaCacheStore(
            cacheDirectory: directory,
            io: .init(
                read: liveIO.read,
                writeStaged: liveIO.writeStaged,
                promote: { _, _ in throw CocoaError(.fileWriteUnknown) },
                remove: liveIO.remove
            )
        )
        #expect(promotionFailure.store(area(id: "a", name: "New")).failure == .promotionFailed)
        #expect(try Data(contentsOf: live.fileURL(for: "a")) == oldBytes)
    }

    @Test func promotionThatMutatesBeforeThrowingRestoresPriorBytesExactly() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let live = AreaCacheStore(cacheDirectory: directory)
        #expect(live.store(area(id: "a", name: "Old")).succeeded)
        let oldBytes = try #require(live.validBytes(id: "a"))
        let liveIO = AreaCacheStore.IO.live()
        final class PromotionState { var callCount = 0 }
        let state = PromotionState()
        let store = AreaCacheStore(
            cacheDirectory: directory,
            io: .init(
                read: liveIO.read,
                writeStaged: liveIO.writeStaged,
                promote: { stagedURL, destinationURL in
                    state.callCount += 1
                    try liveIO.promote(stagedURL, destinationURL)
                    if state.callCount == 1 {
                        throw CocoaError(.fileWriteUnknown)
                    }
                },
                remove: liveIO.remove
            )
        )

        #expect(store.store(area(id: "a", name: "New")).failure == .promotionFailed)
        #expect(state.callCount == 2, "the second promotion restores the verified backup")
        #expect(try Data(contentsOf: live.fileURL(for: "a")) == oldBytes)
        #expect(live.validArea(id: "a")?.name == "Old")
    }

    @Test func rollbackThrowingBeforeMutationRetriesAndRestoresPriorBytesExactly() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let live = AreaCacheStore(cacheDirectory: directory)
        #expect(live.store(area(id: "a", name: "Old")).succeeded)
        let oldBytes = try #require(live.validBytes(id: "a"))
        let liveIO = AreaCacheStore.IO.live()
        final class PromotionState { var callCount = 0 }
        let state = PromotionState()
        let store = AreaCacheStore(
            cacheDirectory: directory,
            io: .init(
                read: liveIO.read,
                writeStaged: liveIO.writeStaged,
                promote: { stagedURL, destinationURL in
                    state.callCount += 1
                    switch state.callCount {
                    case 1:
                        try liveIO.promote(stagedURL, destinationURL)
                        throw CocoaError(.fileWriteUnknown)
                    case 2:
                        throw CocoaError(.fileWriteUnknown)
                    default:
                        try liveIO.promote(stagedURL, destinationURL)
                    }
                },
                remove: liveIO.remove
            )
        )

        #expect(store.store(area(id: "a", name: "New")).failure == .promotionFailed)
        #expect(state.callCount == 3, "rollback should retry after a pre-mutation error")
        #expect(try Data(contentsOf: live.fileURL(for: "a")) == oldBytes)
        #expect(live.validArea(id: "a")?.name == "Old")
    }

    @Test func rollbackMutatingBeforeThrowIsVerifiedAsSuccessful() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let live = AreaCacheStore(cacheDirectory: directory)
        #expect(live.store(area(id: "a", name: "Old")).succeeded)
        let oldBytes = try #require(live.validBytes(id: "a"))
        let liveIO = AreaCacheStore.IO.live()
        final class PromotionState { var callCount = 0 }
        let state = PromotionState()
        let store = AreaCacheStore(
            cacheDirectory: directory,
            io: .init(
                read: liveIO.read,
                writeStaged: liveIO.writeStaged,
                promote: { stagedURL, destinationURL in
                    state.callCount += 1
                    try liveIO.promote(stagedURL, destinationURL)
                    throw CocoaError(.fileWriteUnknown)
                },
                remove: liveIO.remove
            )
        )

        #expect(store.store(area(id: "a", name: "New")).failure == .promotionFailed)
        #expect(state.callCount == 2, "the mutated rollback destination should be verified directly")
        #expect(try Data(contentsOf: live.fileURL(for: "a")) == oldBytes)
        #expect(live.validArea(id: "a")?.name == "Old")
    }

    @Test func finalReadbackFailureRollsBackPriorBytesExactly() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let live = AreaCacheStore(cacheDirectory: directory)
        #expect(live.store(area(id: "a", name: "Old")).succeeded)
        let oldBytes = try #require(live.validBytes(id: "a"))
        let liveIO = AreaCacheStore.IO.live()
        final class ReadState { var destinationReads = 0 }
        let state = ReadState()
        let destination = live.fileURL(for: "a")
        let store = AreaCacheStore(
            cacheDirectory: directory,
            io: .init(
                read: { url in
                    if url == destination {
                        state.destinationReads += 1
                        if state.destinationReads == 2 { return Data("corrupt".utf8) }
                    }
                    return try liveIO.read(url)
                },
                writeStaged: liveIO.writeStaged,
                promote: liveIO.promote,
                remove: liveIO.remove
            )
        )

        #expect(store.store(area(id: "a", name: "New")).failure == .finalVerificationFailed)
        #expect(try Data(contentsOf: destination) == oldBytes)
    }

    @Test func successfulRetryPromotesOnlyVerifiedBytes() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = AreaCacheStore(cacheDirectory: directory)
        #expect(store.store(area(id: "a", name: "Old")).succeeded)
        let oldBytes = try #require(store.validBytes(id: "a"))

        #expect(store.store(Data("bad".utf8), expectedID: "a").failure == .invalidPayload)
        #expect(store.validBytes(id: "a") == oldBytes)
        #expect(store.store(area(id: "a", name: "New")).succeeded)
        #expect(store.validArea(id: "a")?.name == "New")
        #expect(store.validBytes(id: "a") != oldBytes)
    }

    @Test @MainActor func failedForcedRevalidationKeepsValidStaleBytesAvailable() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = AreaCacheStore(cacheDirectory: directory)
        #expect(store.store(area(id: "a", name: "Stale")).succeeded)
        let staleBytes = try #require(store.validBytes(id: "a"))
        let prefetcher = OfflineTrailPrefetcher(
            isDurablyAvailable: { store.validArea(id: $0) != nil },
            fetchAndVerify: { _ in false }
        )

        let result = await prefetcher.run(ids: ["a"], forceRefresh: true)

        #expect(result.failedIDs == ["a"])
        #expect(store.validArea(id: "a")?.name == "Stale")
        #expect(store.validBytes(id: "a") == staleBytes)
    }

    @Test func enumerationExcludesMalformedEmptyAndWrongIdentityFiles() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = AreaCacheStore(cacheDirectory: directory)
        #expect(store.store(area(id: "valid")).succeeded)
        try Data("bad".utf8).write(to: store.fileURL(for: "malformed"))
        let empty = Area(
            id: "empty", name: "Empty", subtitle: "AZ",
            centerLat: 0, centerLon: 0, zoom: 13, bbox: nil,
            trails: [], trailCount: 0, totalMi: 0, cachedAt: nil
        )
        try encoded(empty).write(to: store.fileURL(for: "empty"))
        try encoded(area(id: "other")).write(to: store.fileURL(for: "wrong"))

        #expect(store.entries().map(\.id) == ["valid"])
        #expect(store.entries().first?.sizeBytes == store.validBytes(id: "valid")?.count)
    }
}
