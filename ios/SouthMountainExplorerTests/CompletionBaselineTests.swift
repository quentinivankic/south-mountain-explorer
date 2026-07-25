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
@MainActor
struct CompletionBaselineTests {

    private func reset() {
        RecordingService.shared.resetAll()
        CoverageService.shared.resetAll()
        ProgressService.shared.resetAll()
    }

    /// A trail sitting at full fraction but NEVER officially completed must NOT
    /// be in the start baseline — otherwise completing it now reads as a revisit.
    @Test func fractionCompleteButNeverOfficiallyCompleted_isNotInBaseline() async {
        reset()
        let area = "wild-basin-wilderness-preserve-tx"
        // Fraction over the line from prior incidental coverage…
        await CoverageService.shared.mergeCoverage(areaId: area, delta: ["woodlandtrail": 1.0])
        // …but no official completion recorded (endpoints never reached).
        #expect(ProgressService.shared.isComplete(areaId: area, trailId: "woodlandtrail") == false)

        RecordingService.shared.startRecording(areaId: area, mode: .trail, trailId: "woodlandtrail")

        let baseline = RecordingService.shared.activeRecording?.priorCompleteTrailIds ?? []
        #expect(!baseline.contains("woodlandtrail"),
                "a fraction-complete-but-never-officially-completed trail must not be in the baseline")
    }

    /// A trail that HAS officially completed must be in the baseline, so
    /// re-walking it reads as a revisit (not a duplicate celebration).
    @Test func officiallyCompletedTrail_isInBaseline() async {
        reset()
        let area = "wild-basin-wilderness-preserve-tx"
        await ProgressService.shared.markComplete(areaId: area, trailId: "falls-trail")

        RecordingService.shared.startRecording(areaId: area, mode: .roam)

        let baseline = RecordingService.shared.activeRecording?.priorCompleteTrailIds ?? []
        #expect(baseline.contains("falls-trail"),
                "an officially-completed trail must be in the baseline so a re-walk is a revisit")
    }
}
