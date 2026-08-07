import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `RecordingService.newlyCompletedTrailIds` — the stop-time
/// classification that must still credit a trail finished MID-HIKE.
///
/// A live-tick `mergeCoverage` marks such a trail complete during recording, so
/// deriving "newly completed" from the already-marked state reports 0 (the
/// "Hike Complete, nothing new" bug, and the trail then gets mislabelled a
/// revisit). Deriving it from session-coverage-vs-start-snapshot fixes it.
struct StopCompletionClassificationTests {
    private func complete() -> CoverageScore {
        CoverageScore(fraction: 0.99, endpointsVisited: true, longestSkippedRunM: 0)
    }
    private func partial() -> CoverageScore {
        CoverageScore(fraction: 0.40, endpointsVisited: false, longestSkippedRunM: 0)
    }

    @Test func completedThisHikeCounts() {
        let cov = ["a": complete(), "c": partial()]
        let newly = RecordingService.newlyCompletedTrailIds(sessionCoverage: cov, priorComplete: [])
        #expect(newly == ["a"])
    }

    @Test func alreadyCompleteAtStartIsNotNewly() {
        let cov = ["a": complete(), "b": complete()]
        let newly = Set(RecordingService.newlyCompletedTrailIds(sessionCoverage: cov, priorComplete: ["b"]))
        #expect(newly == ["a"])
    }

    @Test func midHikeCompletionStillCounts() {
        // The device case: complete in the union, NOT complete at hike start —
        // must be newly-completed regardless of a mid-hike markComplete having
        // already fired.
        let cov = ["hau-pal-loop-trail": complete()]
        let newly = RecordingService.newlyCompletedTrailIds(
            sessionCoverage: cov, priorComplete: []
        )
        #expect(newly == ["hau-pal-loop-trail"])
    }

    @Test func nothingCompleteYieldsEmpty() {
        let cov = ["a": partial(), "b": partial()]
        #expect(RecordingService.newlyCompletedTrailIds(sessionCoverage: cov, priorComplete: []).isEmpty)
    }
}
