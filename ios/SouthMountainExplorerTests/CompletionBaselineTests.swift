import CoreLocation
import Testing
import Foundation
@testable import SouthMountainExplorer

/// Regression for the completion-classification bug found in build 247 from a
/// real export (Wild Basin, "woodlandtrail").
///
/// The "already complete" baseline captured at recording START decides whether a
/// trail that completes DURING the recording is "newly completed" (fires the
/// celebration, shows in the post-hike breakdown) or "revisited" (silent). It
/// used bare coverage FRACTION >= threshold. But mergeCoverage credits every
/// trail a GPS path touches, so an earlier recording can push a trail's fraction
/// over the line WITHOUT it ever reaching its endpoints — i.e. without it ever
/// officially completing. Such a trail landed in the baseline, so the recording
/// that finally completed it (endpoints reached) classified it "revisited" and
/// no celebration ever fired.
///
/// Fix: source the baseline from ProgressService's OFFICIAL, endpoint-gated
/// completion record, not coverage fraction.
///
/// Each test records on its own RecordingService (temporary history file,
/// private defaults suite, fake location). Recording on
/// `RecordingService.shared` woke the host app's live observers: the turn cue
/// coordinator released the audio session synchronously on the main thread,
/// and that queued main-actor work blocked the next @MainActor suite on CI
/// (DataBackupManagerTests hung until xcodebuild restarted the run). It also
/// left a live recording in standard defaults that the relaunched app resumed.
@Suite(.timeLimit(.minutes(1)))
@MainActor
struct CompletionBaselineTests {

    @MainActor
    private final class FakeLocation: RecordingLocationControlling {
        var liveLocation: CLLocationCoordinate2D? = nil
        var liveAltitude: Double? = nil
        func acquireRecordingLocation() {}
        func releaseRecordingLocation() {}
    }

    /// An isolated recorder plus the cleanup that removes its files and defaults.
    private func makeService() throws -> (RecordingService, () -> Void) {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("completion-baseline-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let suiteName = "CompletionBaselineTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        let service = RecordingService(
            historyStore: RecordingHistoryStore(fileURL: directory.appendingPathComponent("hike-history.json")),
            userDefaults: defaults,
            locationService: FakeLocation()
        )
        return (service, {
            service.discardRecording()
            defaults.removePersistentDomain(forName: suiteName)
            try? FileManager.default.removeItem(at: directory)
        })
    }

    private func reset() {
        CoverageService.shared.resetAll()
        ProgressService.shared.resetAll()
    }

    /// A trail sitting at full fraction but NEVER officially completed must NOT
    /// be in the start baseline — otherwise completing it now reads as a revisit.
    @Test func fractionCompleteButNeverOfficiallyCompleted_isNotInBaseline() async throws {
        reset()
        let (recording, cleanup) = try makeService()
        defer { cleanup() }
        let area = "wild-basin-wilderness-preserve-tx"
        // Fraction over the line from prior incidental coverage…
        await CoverageService.shared.mergeCoverage(areaId: area, delta: ["woodlandtrail": 1.0])
        // …but no official completion recorded (endpoints never reached).
        #expect(ProgressService.shared.isComplete(areaId: area, trailId: "woodlandtrail") == false)

        #expect(recording.startRecording(areaId: area, mode: .trail, trailId: "woodlandtrail") == .started)

        let baseline = recording.activeRecording?.priorCompleteTrailIds ?? []
        #expect(!baseline.contains("woodlandtrail"),
                "a fraction-complete-but-never-officially-completed trail must not be in the baseline")
    }

    /// A trail that HAS officially completed must be in the baseline, so
    /// re-walking it reads as a revisit (not a duplicate celebration).
    @Test func officiallyCompletedTrail_isInBaseline() async throws {
        reset()
        let (recording, cleanup) = try makeService()
        defer { cleanup() }
        let area = "wild-basin-wilderness-preserve-tx"
        await ProgressService.shared.markComplete(areaId: area, trailId: "falls-trail")

        #expect(recording.startRecording(areaId: area, mode: .roam) == .started)

        let baseline = recording.activeRecording?.priorCompleteTrailIds ?? []
        #expect(baseline.contains("falls-trail"),
                "an officially-completed trail must be in the baseline so a re-walk is a revisit")
    }
}
