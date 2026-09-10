import CoreLocation
import Foundation
import Testing
@testable import SouthMountainExplorer

@Suite(.serialized)
@MainActor
struct RecordingDataSafetyTests {
    private enum ForcedWriteFailure: Error {
        case failed
    }

    @MainActor
    private final class FakeLocationController: RecordingLocationControlling {
        var liveLocation: CLLocationCoordinate2D? = nil
        var liveAltitude: Double? = nil
        private(set) var startCount = 0
        private(set) var stopCount = 0

        func startBackgroundTracking() { startCount += 1 }
        func stopBackgroundTracking() { stopCount += 1 }
    }

    private final class ToggleWriter: @unchecked Sendable {
        private let lock = NSLock()
        private var shouldFail = true

        func allowWrites() {
            lock.lock()
            shouldFail = false
            lock.unlock()
        }

        func write(_ data: Data, to url: URL) throws {
            lock.lock()
            let fail = shouldFail
            lock.unlock()
            if fail { throw ForcedWriteFailure.failed }
            try data.write(to: url, options: .atomic)
        }
    }

    private final class BlockingWriter: @unchecked Sendable {
        private let condition = NSCondition()
        private var started = false
        private var released = false

        var hasStarted: Bool {
            condition.lock()
            defer { condition.unlock() }
            return started
        }

        func release() {
            condition.lock()
            released = true
            condition.broadcast()
            condition.unlock()
        }

        func write(_ data: Data, to url: URL) throws {
            condition.lock()
            started = true
            condition.broadcast()
            while !released { condition.wait() }
            condition.unlock()
            try data.write(to: url, options: .atomic)
        }
    }

    private func makeDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("recording-data-safety-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func makeDefaults() throws -> (UserDefaults, String) {
        let name = "RecordingDataSafetyTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        defaults.removePersistentDomain(forName: name)
        return (defaults, name)
    }

    private func makeActive(
        mode: RecordingMode = .trail,
        startedAt: Date = Date().addingTimeInterval(-120),
        recordingId: String = UUID().uuidString
    ) -> ActiveRecording {
        ActiveRecording(
            areaId: "recording-safety-test-area",
            mode: mode,
            trailId: mode == .trail ? "safe-trail" : nil,
            startedAt: startedAt,
            path: [[33.3, -112.0, startedAt.timeIntervalSince1970 * 1000]],
            distanceMi: 1.25,
            priorCompleteTrailIds: [],
            nearbyAreaIds: mode == .walk ? ["recording-safety-test-area"] : nil,
            priorCompleteByArea: mode == .walk ? ["recording-safety-test-area": []] : nil,
            recordingId: recordingId
        )
    }

    private func makeSaved(id: String = "saved-1", startedAt: TimeInterval = 100) -> SavedRecording {
        SavedRecording(
            id: id,
            areaId: "area",
            startedAt: Date(timeIntervalSince1970: startedAt),
            endedAt: Date(timeIntervalSince1970: startedAt + 60),
            distanceMi: 1.5,
            durationSeconds: 60,
            completedTrailIds: ["trail"],
            path: [[33.3, -112.0, startedAt * 1000]],
            trailId: "trail",
            revisitedTrailIds: [],
            mode: .trail
        )
    }

    private func makeSaved(matching active: ActiveRecording) -> SavedRecording {
        SavedRecording(
            id: active.recordingId!,
            areaId: active.areaId,
            startedAt: active.startedAt,
            endedAt: active.startedAt.addingTimeInterval(120),
            distanceMi: (active.distanceMi * 100).rounded() / 100,
            durationSeconds: 120,
            completedTrailIds: [],
            path: active.path,
            trailId: active.trailId,
            revisitedTrailIds: [],
            multiAreaCompletions: active.mode == .walk ? [active.areaId: []] : nil,
            multiAreaRevisited: active.mode == .walk ? [:] : nil,
            mode: active.mode
        )
    }

    @Test func startingWalkRefusesActiveHikeAndPreservesEveryFix() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let active = makeActive()
        let service = RecordingService(
            historyStore: RecordingHistoryStore(
                fileURL: directory.appendingPathComponent("hike-history.json")
            ),
            userDefaults: defaults,
            locationService: FakeLocationController(),
            initialActiveRecording: active
        )

        let result = service.startWalk(
            primaryAreaId: "other-area",
            nearbyAreaIds: ["other-area"]
        )

        #expect(result == .alreadyActive)
        #expect(service.activeRecording == active)
        let recovered = try #require(defaults.data(forKey: StorageKeys.activeRecording))
        #expect(try JSONDecoder().decode(ActiveRecording.self, from: recovered) == active)
    }

    @Test func missingHistoryIsEmptyButCorruptHistoryIsDistinctAndProtected() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let url = directory.appendingPathComponent("hike-history.json")
        let store = RecordingHistoryStore(fileURL: url)

        #expect(try store.load().isEmpty)

        let corrupt = Data("not valid history json".utf8)
        try corrupt.write(to: url)
        do {
            _ = try store.load()
            Issue.record("corrupt history must not be interpreted as empty")
        } catch let error as RecordingHistoryStoreError {
            guard case .corrupt = error else {
                Issue.record("expected a corrupt-history error, got \(error)")
                return
            }
        }
        #expect(throws: RecordingHistoryStoreError.self) {
            try store.prepend(makeSaved())
        }
        #expect(try Data(contentsOf: url) == corrupt)

        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: FakeLocationController()
        )
        let loadedHistory = await service.loadHistory()
        #expect(loadedHistory.isEmpty)
        #expect(service.historyErrorMessage != nil, "Stats must receive an error instead of an empty-history state")
    }

    @Test func successfulAtomicSaveRoundTripsSortsAndIsIdempotent() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = RecordingHistoryStore(
            fileURL: directory.appendingPathComponent("hike-history.json")
        )
        let older = makeSaved(id: "older", startedAt: 100)
        let newer = makeSaved(id: "newer", startedAt: 200)

        try store.prepend(older)
        try store.prepend(newer)
        try store.prepend(newer)

        #expect(try store.load() == [newer, older])
    }

    @Test func postCommitWriterErrorReconcilesInsideHistoryStore() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = RecordingHistoryStore(
            fileURL: directory.appendingPathComponent("hike-history.json"),
            atomicWriter: { data, url in
                try data.write(to: url, options: .atomic)
                throw ForcedWriteFailure.failed
            }
        )
        let saved = makeSaved()

        #expect(try store.prepend(saved) == saved)
        #expect(try store.load() == [saved])
    }

    @Test func retargetCannotMutateCheckpointDuringAtomicAppend() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let writer = BlockingWriter()
        defer { writer.release() }
        let store = RecordingHistoryStore(
            fileURL: directory.appendingPathComponent("hike-history.json"),
            atomicWriter: { data, url in try writer.write(data, to: url) }
        )
        let active = makeActive(recordingId: "blocked-append-id")
        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: FakeLocationController(),
            initialActiveRecording: active
        )

        let stopTask = Task { @MainActor in
            try await service.stopRecording(trails: [])
        }
        for _ in 0..<200 where !writer.hasStarted {
            try await Task.sleep(for: .milliseconds(5))
        }
        guard writer.hasStarted else {
            writer.release()
            _ = try? await stopTask.value
            Issue.record("atomic writer never reached its blocking point")
            return
        }

        #expect(!service.retargetTrail("different-trail"))
        #expect(service.activeRecording?.trailId == active.trailId)
        #expect(service.activeRecording?.recordingId == active.recordingId)

        writer.release()
        _ = try await stopTask.value
        #expect(service.activeRecording == nil)
        #expect(try store.load().count == 1)
    }

    @Test func writeFailurePreservesActiveStateResumesObservationAndOrdinaryStopRetries() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let url = directory.appendingPathComponent("hike-history.json")
        let writer = ToggleWriter()
        let store = RecordingHistoryStore(
            fileURL: url,
            atomicWriter: { data, url in try writer.write(data, to: url) }
        )
        let location = FakeLocationController()
        let active = makeActive()
        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: location,
            initialActiveRecording: active
        )

        do {
            _ = try await service.stopRecording(trails: [])
            Issue.record("the forced write failure must reach the caller")
        } catch is RecordingHistoryStoreError { }

        #expect(service.activeRecording == active)
        #expect(defaults.data(forKey: StorageKeys.activeRecording) != nil)
        #expect(!FileManager.default.fileExists(atPath: url.path))
        #expect(location.stopCount == 1)
        #expect(location.startCount == 1, "every save failure must resume location observation")
        #expect(service.errorMessage != nil)

        writer.allowWrites()
        let retryResult = try await service.stopRecording(trails: [])
        let finished = try #require(retryResult)

        #expect(finished.startedAt == active.startedAt)
        #expect(service.activeRecording == nil)
        #expect(defaults.data(forKey: StorageKeys.activeRecording) == nil)
        let history = try store.load()
        #expect(history.count == 1)
        #expect(history[0].id == active.recordingId)
    }

    @Test func launchClearsExactAlreadySavedCheckpointWithoutDuplicatingHistory() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = RecordingHistoryStore(
            fileURL: directory.appendingPathComponent("hike-history.json")
        )
        let active = makeActive(mode: .walk, recordingId: "stable-crash-id")
        let saved = makeSaved(matching: active)
        try store.prepend(saved)
        defaults.set(try JSONEncoder().encode(active), forKey: StorageKeys.activeRecording)
        let location = FakeLocationController()

        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: location,
            restoreStoredState: true
        )

        #expect(service.activeRecording == nil)
        #expect(defaults.data(forKey: StorageKeys.activeRecording) == nil)
        #expect(location.startCount == 0)
        #expect(try store.load() == [saved])
    }

    @Test func identifierConflictPreservesBothHistoryAndActiveCheckpoint() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = RecordingHistoryStore(
            fileURL: directory.appendingPathComponent("hike-history.json")
        )
        let active = makeActive(recordingId: "conflicting-id")
        let conflicting = makeSaved(id: "conflicting-id", startedAt: 100)
        try store.prepend(conflicting)
        defaults.set(try JSONEncoder().encode(active), forKey: StorageKeys.activeRecording)
        let location = FakeLocationController()
        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: location,
            restoreStoredState: true
        )

        #expect(service.activeRecording == active)
        #expect(defaults.data(forKey: StorageKeys.activeRecording) != nil)
        #expect(service.errorMessage != nil)
        #expect(location.startCount == 1, "a conflict preserves and resumes the active recording")

        do {
            _ = try await service.stopRecording(trails: [])
            Issue.record("a different payload with the same stable ID must fail closed")
        } catch let error as RecordingHistoryStoreError {
            guard case .identifierConflict("conflicting-id") = error else {
                Issue.record("expected identifier conflict, got \(error)")
                return
            }
        }

        #expect(service.activeRecording?.path == active.path)
        #expect(defaults.data(forKey: StorageKeys.activeRecording) != nil)
        #expect(try store.load() == [conflicting])
        #expect(location.startCount == 2, "failed stop must resume observation again")
    }

    @Test func successfulWalkSaveUsesStableIdAndClearsRecoveryAfterSavePath() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = RecordingHistoryStore(
            fileURL: directory.appendingPathComponent("hike-history.json")
        )
        let active = makeActive(mode: .walk, recordingId: "stable-walk-id")
        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: FakeLocationController(),
            initialActiveRecording: active
        )

        let requiredTrail = Trail(
            id: "safe-trail",
            name: "Safe Trail",
            distanceMi: 1,
            difficulty: .easy,
            segments: [[[33.3, -112.0], [33.31, -112.0]]]
        )
        let walkResult = try await service.stopWalk(
            trailsByArea: [active.areaId: [requiredTrail]]
        )
        let finished = try #require(walkResult)

        #expect(finished.mode == .walk)
        #expect(service.activeRecording == nil)
        #expect(defaults.data(forKey: StorageKeys.activeRecording) == nil)
        let history = try store.load()
        #expect(history.count == 1)
        #expect(history[0].id == "stable-walk-id")
        #expect(history[0].mode == .walk)
    }
}
