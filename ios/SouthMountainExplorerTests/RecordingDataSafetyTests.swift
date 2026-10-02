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
        private(set) var acquireCount = 0
        private(set) var releaseCount = 0
        private(set) var ownsRecordingLocation = false

        func acquireRecordingLocation() {
            guard !ownsRecordingLocation else { return }
            ownsRecordingLocation = true
            acquireCount += 1
        }

        func releaseRecordingLocation() {
            guard ownsRecordingLocation else { return }
            ownsRecordingLocation = false
            releaseCount += 1
        }
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
        let activeRecordingWasPreserved = service.activeRecording == active
        #expect(activeRecordingWasPreserved, "Starting a Walk changed the active recording")
        let recovered = try #require(defaults.data(forKey: StorageKeys.activeRecording))
        let decodedCheckpoint = try JSONDecoder().decode(ActiveRecording.self, from: recovered)
        let checkpointWasPreserved = decodedCheckpoint == active
        #expect(checkpointWasPreserved, "Starting a Walk changed the persisted checkpoint")
    }

    @Test func newRecordingAcquiresOnceAndDiscardReleasesOnce() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let location = FakeLocationController()
        let service = RecordingService(
            historyStore: RecordingHistoryStore(
                fileURL: directory.appendingPathComponent("hike-history.json")
            ),
            userDefaults: defaults,
            locationService: location
        )

        #expect(service.startRecording(
            areaId: "recording-safety-test-area",
            mode: .trail,
            trailId: "safe-trail"
        ) == .started)
        #expect(location.acquireCount == 1)
        #expect(location.ownsRecordingLocation)

        service.discardRecording()
        service.discardRecording()
        #expect(location.releaseCount == 1)
        #expect(!location.ownsRecordingLocation)
        let checkpointWasCleared = defaults.data(forKey: StorageKeys.activeRecording) == nil
        #expect(checkpointWasCleared, "Discard left an active checkpoint")
    }

    @Test func localControlVisibilityDoesNotMutateActiveRecording() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let active = makeActive(recordingId: "visibility-safety-id")
        let location = FakeLocationController()
        let service = RecordingService(
            historyStore: RecordingHistoryStore(
                fileURL: directory.appendingPathComponent("hike-history.json")
            ),
            userDefaults: defaults,
            locationService: location,
            initialActiveRecording: active
        )
        let visibility = RecordingControlVisibility()
        let token = RecordingControlVisibility.Token()

        visibility.acquire(token)
        visibility.release(token)

        let activeRecordingWasPreserved = service.activeRecording == active
        #expect(activeRecordingWasPreserved, "Control visibility changed the active recording")
        #expect(location.ownsRecordingLocation)
        #expect(location.releaseCount == 0)
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
                Issue.record("expected a corrupt-history error")
                return
            }
        }
        do {
            try store.prepend(makeSaved())
            Issue.record("corrupt history must reject prepend")
        } catch is RecordingHistoryStoreError { }
        let corruptBytesWerePreserved = try Data(contentsOf: url) == corrupt
        #expect(corruptBytesWerePreserved, "Corrupt history bytes were changed")

        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: FakeLocationController()
        )
        let loadedHistory = await service.loadHistory()
        #expect(loadedHistory.isEmpty)
        let historyFailureIsVisible = service.historyErrorMessage != nil
        #expect(historyFailureIsVisible, "Stats must receive an error instead of an empty-history state")
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

        let loadedHistory = try store.load()
        let historyOrderMatches = loadedHistory == [newer, older]
        #expect(historyOrderMatches, "Atomic history save produced an unexpected order")
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

        let reconciledSave = try store.prepend(saved)
        let reconciledSaveMatches = reconciledSave == saved
        #expect(reconciledSaveMatches, "Post-commit reconciliation returned an unexpected recording")
        let loadedHistory = try store.load()
        let persistedHistoryMatches = loadedHistory == [saved]
        #expect(persistedHistoryMatches, "Post-commit reconciliation persisted unexpected history")
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
        let trailIntentWasPreserved = service.activeRecording?.trailId == active.trailId
        #expect(trailIntentWasPreserved, "Retarget changed trail intent during atomic append")
        let recordingIdentityWasPreserved = service.activeRecording?.recordingId == active.recordingId
        #expect(recordingIdentityWasPreserved, "Retarget changed recording identity during atomic append")

        writer.release()
        _ = try await stopTask.value
        let activeRecordingWasCleared = service.activeRecording == nil
        #expect(activeRecordingWasCleared, "Verified save left an active recording")
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

        let activeRecordingWasPreserved = service.activeRecording == active
        #expect(activeRecordingWasPreserved, "Save failure changed the active recording")
        let checkpointWasPreserved = defaults.data(forKey: StorageKeys.activeRecording) != nil
        #expect(checkpointWasPreserved, "Save failure removed the active checkpoint")
        let historyFileIsAbsent = !FileManager.default.fileExists(atPath: url.path)
        #expect(historyFileIsAbsent, "Save failure created an unverified history file")
        #expect(location.releaseCount == 0, "save failure must retain recording ownership")
        #expect(location.acquireCount == 1, "save failure must restart polling without reacquiring")
        #expect(location.ownsRecordingLocation)
        let saveFailureIsVisible = service.errorMessage != nil
        #expect(saveFailureIsVisible, "Save failure did not expose an error state")

        writer.allowWrites()
        let retryResult = try await service.stopRecording(trails: [])
        let finished = try #require(retryResult)

        let startTimeWasPreserved = finished.startedAt == active.startedAt
        #expect(startTimeWasPreserved, "Save retry changed the recording start time")
        let activeRecordingWasCleared = service.activeRecording == nil
        #expect(activeRecordingWasCleared, "Successful retry left an active recording")
        let checkpointWasCleared = defaults.data(forKey: StorageKeys.activeRecording) == nil
        #expect(checkpointWasCleared, "Successful retry left an active checkpoint")
        #expect(location.releaseCount == 1)
        #expect(!location.ownsRecordingLocation)
        let history = try store.load()
        #expect(history.count == 1)
        let savedIdentityMatches = history[0].id == active.recordingId
        #expect(savedIdentityMatches, "Save retry persisted an unexpected recording identity")
    }

    @Test func unreadableCheckpointIsPreservedVisibleAndRetryable() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let unreadable = Data("preserve these recovery bytes".utf8)
        defaults.set(unreadable, forKey: StorageKeys.activeRecording)
        let location = FakeLocationController()
        let service = RecordingService(
            historyStore: RecordingHistoryStore(
                fileURL: directory.appendingPathComponent("hike-history.json")
            ),
            userDefaults: defaults,
            locationService: location,
            restoreStoredState: true
        )

        let noActiveRecordingWasDecoded = service.activeRecording == nil
        #expect(noActiveRecordingWasDecoded, "Unreadable checkpoint produced an active recording")
        #expect(service.recoveryIssue == .unreadableCheckpoint)
        let unreadableBytesWerePreserved =
            defaults.data(forKey: StorageKeys.activeRecording) == unreadable
        #expect(unreadableBytesWerePreserved, "Unreadable checkpoint bytes were changed")
        #expect(service.startRecording(areaId: "new-area", mode: .roam) == .recoveryRequired)
        let refusedStartPreservedBytes =
            defaults.data(forKey: StorageKeys.activeRecording) == unreadable
        #expect(refusedStartPreservedBytes, "Refused start changed unreadable checkpoint bytes")

        let repaired = makeActive(recordingId: "repaired-checkpoint")
        defaults.set(try JSONEncoder().encode(repaired), forKey: StorageKeys.activeRecording)
        service.retryRecovery()

        let repairedRecordingWasRestored = service.activeRecording == repaired
        #expect(repairedRecordingWasRestored, "Recovery retry restored an unexpected recording")
        #expect(service.recoveryIssue == nil)
        #expect(location.acquireCount == 1)
        #expect(location.ownsRecordingLocation)
    }

    @Test func historyReadFailurePreservesBothCopiesAndRecoveryRetry() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let historyURL = directory.appendingPathComponent("hike-history.json")
        let corruptHistory = Data("preserve corrupt history".utf8)
        try corruptHistory.write(to: historyURL)
        let active = makeActive(recordingId: "history-read-checkpoint")
        let activeBytes = try JSONEncoder().encode(active)
        defaults.set(activeBytes, forKey: StorageKeys.activeRecording)
        let location = FakeLocationController()
        let service = RecordingService(
            historyStore: RecordingHistoryStore(fileURL: historyURL),
            userDefaults: defaults,
            locationService: location,
            restoreStoredState: true
        )

        let activeRecordingWasRestored = service.activeRecording == active
        #expect(activeRecordingWasRestored, "History failure changed the active recording")
        #expect(service.recoveryIssue == .historyUnavailable)
        let activeBytesWerePreserved =
            defaults.data(forKey: StorageKeys.activeRecording) == activeBytes
        #expect(activeBytesWerePreserved, "History failure changed the active checkpoint bytes")
        let corruptHistoryWasPreserved = try Data(contentsOf: historyURL) == corruptHistory
        #expect(corruptHistoryWasPreserved, "History failure changed the corrupt history bytes")
        #expect(location.acquireCount == 1)

        try FileManager.default.removeItem(at: historyURL)
        service.retryRecovery()

        let retryPreservedActiveRecording = service.activeRecording == active
        #expect(retryPreservedActiveRecording, "Recovery retry changed the active recording")
        #expect(service.recoveryIssue == nil)
        let retryPreservedActiveBytes =
            defaults.data(forKey: StorageKeys.activeRecording) == activeBytes
        #expect(retryPreservedActiveBytes, "Recovery retry changed the active checkpoint bytes")
        #expect(location.acquireCount == 1, "retry must not duplicate recording ownership")
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

        let activeRecordingWasCleared = service.activeRecording == nil
        #expect(activeRecordingWasCleared, "Exact saved-checkpoint recovery left an active recording")
        let checkpointWasCleared = defaults.data(forKey: StorageKeys.activeRecording) == nil
        #expect(checkpointWasCleared, "Exact saved-checkpoint recovery left checkpoint bytes")
        #expect(service.recoveryIssue == nil)
        #expect(location.acquireCount == 0)
        let loadedHistory = try store.load()
        let historyWasNotDuplicated = loadedHistory == [saved]
        #expect(historyWasNotDuplicated, "Exact saved-checkpoint recovery duplicated history")
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

        let activeRecordingWasPreserved = service.activeRecording == active
        #expect(activeRecordingWasPreserved, "Identifier conflict changed the active recording")
        let checkpointWasPreserved = defaults.data(forKey: StorageKeys.activeRecording) != nil
        #expect(checkpointWasPreserved, "Identifier conflict removed the active checkpoint")
        #expect(service.recoveryIssue == .identifierConflict)
        let genericErrorStayedClear = service.errorMessage == nil
        #expect(genericErrorStayedClear, "Identifier conflict populated the generic error channel")
        #expect(location.acquireCount == 1, "a conflict preserves and resumes the active recording")
        #expect(location.ownsRecordingLocation)

        service.retryRecovery()
        #expect(service.recoveryIssue == .identifierConflict)
        let retryPreservedActiveRecording = service.activeRecording == active
        #expect(retryPreservedActiveRecording, "Conflict retry changed the active recording")
        let historyAfterRetry = try store.load()
        let retryPreservedHistory = historyAfterRetry == [conflicting]
        #expect(retryPreservedHistory, "Conflict retry changed persisted history")
        #expect(location.acquireCount == 1, "recovery retry must not duplicate ownership")

        do {
            _ = try await service.stopRecording(trails: [])
            Issue.record("a different payload with the same stable ID must fail closed")
        } catch let error as RecordingHistoryStoreError {
            guard case .identifierConflict("conflicting-id") = error else {
                Issue.record("expected an identifier-conflict error")
                return
            }
        }

        let activePathWasPreserved = service.activeRecording?.path == active.path
        #expect(activePathWasPreserved, "Failed conflicting stop changed the active path")
        let failedStopPreservedCheckpoint = defaults.data(forKey: StorageKeys.activeRecording) != nil
        #expect(failedStopPreservedCheckpoint, "Failed conflicting stop removed the checkpoint")
        let historyAfterFailedStop = try store.load()
        let failedStopPreservedHistory = historyAfterFailedStop == [conflicting]
        #expect(failedStopPreservedHistory, "Failed conflicting stop changed persisted history")
        #expect(location.acquireCount == 1, "failed stop must restart polling without duplicating ownership")
        #expect(location.releaseCount == 0)
        #expect(location.ownsRecordingLocation)
    }

    @Test func restoredWalkCannotSaveFromPartialPersistedScope() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let active = ActiveRecording(
            areaId: "recording-safety-test-area",
            mode: .walk,
            trailId: nil,
            startedAt: Date().addingTimeInterval(-120),
            path: [[33.3, -112.0, Date().timeIntervalSince1970 * 1000]],
            distanceMi: 1.25,
            priorCompleteTrailIds: [],
            nearbyAreaIds: ["recording-safety-test-area", "missing-area"],
            priorCompleteByArea: [:],
            recordingId: "partial-restored-walk"
        )
        let location = FakeLocationController()
        let store = RecordingHistoryStore(
            fileURL: directory.appendingPathComponent("hike-history.json")
        )
        let service = RecordingService(
            historyStore: store,
            userDefaults: defaults,
            locationService: location,
            initialActiveRecording: active
        )
        let loadedTrail = Trail(
            id: "safe-trail",
            name: "Safe Trail",
            distanceMi: 1,
            difficulty: .easy,
            segments: [[[33.3, -112.0], [33.31, -112.0]]]
        )

        do {
            _ = try await service.stopWalk(
                trailsByArea: [active.areaId: [loadedTrail]]
            )
            Issue.record("a partial persisted Walk scope must not save")
        } catch let error as RecordingOperationError {
            let expectedError = error == .missingWalkAreaData
            #expect(expectedError, "Partial Walk save returned an unexpected error")
        }

        let activeRecordingWasPreserved = service.activeRecording == active
        #expect(activeRecordingWasPreserved, "Partial Walk save changed the active recording")
        let checkpointWasPreserved = defaults.data(forKey: StorageKeys.activeRecording) != nil
        #expect(checkpointWasPreserved, "Partial Walk save removed the active checkpoint")
        #expect(try store.load().isEmpty)
        #expect(location.ownsRecordingLocation)
        #expect(location.releaseCount == 0)
        #expect(!service.isStopping)
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
        let activeRecordingWasCleared = service.activeRecording == nil
        #expect(activeRecordingWasCleared, "Successful Walk save left an active recording")
        let checkpointWasCleared = defaults.data(forKey: StorageKeys.activeRecording) == nil
        #expect(checkpointWasCleared, "Successful Walk save left an active checkpoint")
        let history = try store.load()
        #expect(history.count == 1)
        let savedIdentityMatches = history[0].id == "stable-walk-id"
        #expect(savedIdentityMatches, "Walk save persisted an unexpected recording identity")
        #expect(history[0].mode == .walk)
    }
}
