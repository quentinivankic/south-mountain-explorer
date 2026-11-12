import CoreLocation
import Foundation
import Testing
@testable import SouthMountainExplorer

/// Release B follow-up 8: the coordinator's plan restore and clear wiring.
/// Each test runs its own coordinator on an isolated recorder (temporary
/// history file, private defaults suite, fake location), a temporary plan
/// store, a silent speaker and a fake notification center, so nothing
/// touches the host app's shared services or the audio session.
@Suite(.timeLimit(.minutes(1)))
@MainActor
struct TurnCueCoordinatorPlanTests {

    @MainActor
    private final class FakeRecordingLocation: RecordingLocationControlling {
        var liveLocation: CLLocationCoordinate2D? = nil
        var liveAltitude: Double? = nil
        func acquireRecordingLocation() {}
        func releaseRecordingLocation() {}
    }

    /// Never delivers a fix, so the guide only ticks.
    @MainActor
    private final class FakeLocationManager: LocationManagerControlling {
        var authorizationStatus: CLAuthorizationStatus = .authorizedWhenInUse
        var desiredAccuracy: CLLocationAccuracy = kCLLocationAccuracyThreeKilometers
        var allowsBackgroundLocationUpdates = false
        var pausesLocationUpdatesAutomatically = true
        var activityType: CLActivityType = .other
        var headingFilter: CLLocationDegrees = kCLHeadingFilterNone
        var headingAvailable = false
        func setDelegate(_ delegate: (any CLLocationManagerDelegate)?) {}
        func requestWhenInUseAuthorization() {}
        func requestLocation() {}
        func startUpdatingLocation() {}
        func stopUpdatingLocation() {}
        func startUpdatingHeading() {}
        func stopUpdatingHeading() {}
    }

    @MainActor
    private final class SilentSession: AudioSessionControlling {
        func activate() throws {}
        func deactivate() {}
    }

    @MainActor
    private final class SilentOutput: SpeechOutput {
        var onFinish: (@MainActor () -> Void)?
        func speak(_ text: String) {}
        func stop() { onFinish?() }
    }

    @MainActor
    private final class FakeCenter: TurnCueNotificationCenter {
        func authorizationStatus() async -> TurnCueNotificationAuthorization { .denied }
        func requestAuthorization() async -> Bool { false }
        func post(_ notification: TurnCueNotification) async {}
        func remove(identifier: String) {}
    }

    private struct Rig {
        let coordinator: TurnCueCoordinator
        let recording: RecordingService
        let store: PlanGuideStore
        let cleanup: () -> Void
    }

    private func makeRig() throws -> Rig {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("turn-cue-coordinator-plan-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let suiteName = "TurnCueCoordinatorPlanTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        let recording = RecordingService(
            historyStore: RecordingHistoryStore(fileURL: directory.appendingPathComponent("hike-history.json")),
            userDefaults: defaults,
            locationService: FakeRecordingLocation()
        )
        let store = PlanGuideStore(fileURL: directory.appendingPathComponent("plan-guide.json"))
        let coordinator = TurnCueCoordinator(
            recording: recording,
            location: LocationService(manager: FakeLocationManager(), userDefaults: defaults),
            areas: .shared,
            defaults: defaults,
            speaker: TurnCueSpeaker(session: SilentSession(), output: SilentOutput(), observeInterruptions: false),
            notificationCenter: FakeCenter(),
            planStore: store
        )
        return Rig(coordinator: coordinator, recording: recording, store: store, cleanup: {
            // Ends the guide's poll loop, then the recording.
            coordinator.endPlanGuide()
            recording.discardRecording()
            defaults.removePersistentDomain(forName: suiteName)
            try? FileManager.default.removeItem(at: directory)
        })
    }

    /// Starts a roam recording (no trail cues) and returns its id.
    private func startRecording(_ rig: Rig) throws -> String {
        #expect(rig.recording.startRecording(areaId: "wild-basin-wilderness-preserve-tx", mode: .roam) == .started)
        return try #require(rig.recording.activeRecording?.recordingId)
    }

    /// The guide starts on the coordinator's own task; wait up to 2 s.
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    // MARK: - Restore

    /// Relaunch mid-hike: the plan saved for the active recording comes back
    /// on the saved step, with its steps for the bar.
    @Test func savedPlanForTheActiveRecordingIsRestoredAtItsStep() async throws {
        let rig = try makeRig()
        defer { rig.cleanup() }
        let (_, steps) = try PlanGuideRouteTests.outAndBack()
        let id = try startRecording(rig)
        rig.store.save(PlanGuideSnapshot(recordingId: id, steps: steps, stepIndex: 2))

        rig.coordinator.start()

        #expect(await waitUntil { rig.coordinator.planGuide != nil })
        #expect(rig.coordinator.planGuide?.stepIndex == 2)
        #expect(rig.coordinator.planGuide?.stepCount == steps.count)
        #expect(rig.coordinator.planGuide?.isComplete == false)
        // Same steps in every saved field. `walkRange` is not saved, so it
        // comes back empty; the map then draws the leg from its coords.
        let restored = try #require(rig.coordinator.planGuideSteps)
        #expect(restored.map(PlanGuideSnapshot.Step.init) == steps.map(PlanGuideSnapshot.Step.init))
        #expect(restored.allSatisfy { $0.walkRange.isEmpty })
        let saved = try #require(rig.store.load())
        #expect(saved.recordingId == id)
        #expect(saved.stepIndex == 2)
    }

    /// After a restore, Next moves the guide and saves the new step.
    @Test func nextAfterARestoreSavesTheNewStep() async throws {
        let rig = try makeRig()
        defer { rig.cleanup() }
        let (_, steps) = try PlanGuideRouteTests.outAndBack()
        let id = try startRecording(rig)
        rig.store.save(PlanGuideSnapshot(recordingId: id, steps: steps, stepIndex: 1))
        rig.coordinator.start()
        #expect(await waitUntil { rig.coordinator.planGuide?.stepIndex == 1 })

        rig.coordinator.movePlanGuide(toStep: 3)

        #expect(rig.coordinator.planGuide?.stepIndex == 3)
        #expect(rig.store.load()?.stepIndex == 3)
    }

    /// A plan saved for a recording that is no longer active is dropped.
    @Test func savedPlanForAnotherRecordingIsDiscarded() async throws {
        let rig = try makeRig()
        defer { rig.cleanup() }
        let (_, steps) = try PlanGuideRouteTests.outAndBack()
        _ = try startRecording(rig)
        rig.store.save(PlanGuideSnapshot(recordingId: "an-older-recording", steps: steps, stepIndex: 2))

        rig.coordinator.start()

        #expect(rig.store.load() == nil)
        #expect(rig.coordinator.planGuide == nil)
        // Still no guide once the coordinator's task had time to run.
        try await Task.sleep(for: .milliseconds(200))
        #expect(rig.coordinator.planGuide == nil)
        #expect(rig.coordinator.planGuideSteps == nil)
    }

    /// With no active recording yet (for example a recovery is pending) the
    /// file is kept; when a different recording starts, it is dropped.
    @Test func savedPlanIsKeptWithNoRecordingThenDroppedForANewOne() async throws {
        let rig = try makeRig()
        defer { rig.cleanup() }
        let (_, steps) = try PlanGuideRouteTests.outAndBack()
        rig.store.save(PlanGuideSnapshot(recordingId: "a-recording-not-resumed-yet", steps: steps))

        rig.coordinator.start()

        #expect(rig.store.load() != nil)
        #expect(rig.coordinator.planGuide == nil)

        _ = try startRecording(rig)

        #expect(await waitUntil { rig.store.load() == nil })
        #expect(rig.coordinator.planGuide == nil)
    }

    /// Saved steps that no longer decode are cleared, not guided.
    @Test func savedPlanWithUnreadableStepsIsCleared() async throws {
        let rig = try makeRig()
        defer { rig.cleanup() }
        let (_, steps) = try PlanGuideRouteTests.outAndBack()
        let id = try startRecording(rig)
        let json = try #require(String(
            data: JSONEncoder().encode(PlanGuideSnapshot(recordingId: id, steps: steps)), encoding: .utf8))
        #expect(json.contains("\"action\":\"start\""))
        let broken = json.replacingOccurrences(of: "\"action\":\"start\"", with: "\"action\":\"hop\"")
        try FileManager.default.createDirectory(
            at: rig.store.fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(broken.utf8).write(to: rig.store.fileURL)
        #expect(rig.store.load()?.planSteps == nil)

        rig.coordinator.start()

        #expect(!FileManager.default.fileExists(atPath: rig.store.fileURL.path))
        #expect(rig.coordinator.planGuide == nil)
    }

    // MARK: - Clear

    /// Exit ends the guide and clears the saved plan; the recording keeps going.
    @Test func exitClearsTheGuideAndTheSavedPlan() async throws {
        let rig = try makeRig()
        defer { rig.cleanup() }
        let (_, steps) = try PlanGuideRouteTests.outAndBack()
        let id = try startRecording(rig)
        rig.coordinator.start()
        #expect(rig.coordinator.beginPlanGuide(steps: steps, recordingId: id))
        #expect(rig.store.load()?.recordingId == id)
        #expect(await waitUntil { rig.coordinator.planGuide != nil })
        #expect(rig.coordinator.planGuide?.stepIndex == 0)

        rig.coordinator.endPlanGuide()

        #expect(rig.coordinator.planGuide == nil)
        #expect(rig.coordinator.planGuideSteps == nil)
        #expect(rig.store.load() == nil)
        #expect(rig.recording.activeRecording?.recordingId == id)
    }

    /// The guide goes with its recording: ending the recording clears it.
    @Test func endingTheRecordingClearsTheGuideAndTheSavedPlan() async throws {
        let rig = try makeRig()
        defer { rig.cleanup() }
        let (_, steps) = try PlanGuideRouteTests.outAndBack()
        let id = try startRecording(rig)
        rig.coordinator.start()
        #expect(rig.coordinator.beginPlanGuide(steps: steps, recordingId: id))
        #expect(await waitUntil { rig.coordinator.planGuide != nil })

        rig.recording.discardRecording()

        #expect(await waitUntil { rig.store.load() == nil && rig.coordinator.planGuide == nil })
        #expect(rig.coordinator.planGuideSteps == nil)
    }
}
