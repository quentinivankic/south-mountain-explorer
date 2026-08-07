import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `SavedRecording.displayRevisitedTrailIds` — the invariant that a
/// trail this hike newly completed is never ALSO shown as "previously
/// completed." The stored arrays can overlap after `rebuildCoverageFromHistory`
/// credits a first-time completion without clearing the stop-time revisit tag
/// (the real Crosscut / Max Delta case from a device backup), so display and
/// counts must read the de-overlapped view.
struct SavedRecordingRevisitTests {

    private func rec(
        completed: [String],
        revisited: [String],
        multiAreaCompletions: [String: [String]]? = nil,
        multiAreaRevisited: [String: [String]]? = nil,
        mode: RecordingMode = .roam
    ) -> SavedRecording {
        SavedRecording(
            id: "h", areaId: "a",
            startedAt: Date(timeIntervalSince1970: 0),
            endedAt: Date(timeIntervalSince1970: 60),
            distanceMi: 1, durationSeconds: 60,
            completedTrailIds: completed, path: [], trailId: nil,
            revisitedTrailIds: revisited,
            multiAreaCompletions: multiAreaCompletions,
            multiAreaRevisited: multiAreaRevisited,
            mode: mode
        )
    }

    @Test func overlappingTrailsDropOutOfRevisited() {
        // The exact device case: both trails in both arrays.
        let r = rec(completed: ["crosscut-trail", "max-delta-trail"],
                    revisited: ["crosscut-trail", "max-delta-trail"])
        #expect(r.displayRevisitedTrailIds.isEmpty)
    }

    @Test func genuineRevisitSurvives() {
        let r = rec(completed: ["a"], revisited: ["a", "b"])
        #expect(r.displayRevisitedTrailIds == ["b"])
    }

    @Test func noOverlapIsUnchanged() {
        let r = rec(completed: ["a"], revisited: ["b", "c"])
        #expect(r.displayRevisitedTrailIds == ["b", "c"])
    }

    @Test func perAreaExcludesThatAreasCompletions() {
        let r = rec(
            completed: [],
            revisited: [],
            multiAreaCompletions: ["a": ["x"], "b": ["y"]],
            multiAreaRevisited: ["a": ["x", "z"], "b": ["y"]],
            mode: .walk
        )
        #expect(r.displayRevisitedTrailIds(in: "a") == ["z"])
        #expect(r.displayRevisitedTrailIds(in: "b").isEmpty)
    }
}
