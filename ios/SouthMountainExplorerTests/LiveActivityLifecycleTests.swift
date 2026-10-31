import Foundation
import Testing
@testable import SouthMountainExplorer

struct LiveActivityLifecycleTests {
    typealias L = LiveActivityLifecycle
    private let rec = LiveActivityRecordingIdentity(id: "r1", startedAt: Date(timeIntervalSince1970: 1_800_000_000))

    private func input(recording: LiveActivityRecordingIdentity? = nil,
                       sawStopping: Bool = false, enabled: Bool = true,
                       existing: [LiveActivityExisting] = [],
                       previous: String? = nil, current: String? = nil,
                       blocked: Bool = false, dismissed: String? = nil) -> L.Input {
        .init(recording: recording, sawStopping: sawStopping, activitiesEnabled: enabled,
              existing: existing, previouslyTrackedRecordingId: previous, currentActivityId: current,
              isRecoveryBlocked: blocked, userDismissedRecordingId: dismissed)
    }
    private func act(_ id: String, _ recordingId: String, ended: Bool = false) -> LiveActivityExisting {
        .init(id: id, recordingId: recordingId, isEnded: ended)
    }

    @Test func recordingWithoutActivityStarts() {
        #expect(L.decide(input(recording: rec)) == [.start])
    }
    @Test func matchingActivityFromOldProcessIsAdopted() {
        #expect(L.decide(input(recording: rec, existing: [act("a", "r1")])) == [.adopt(activityId: "a")])
    }
    @Test func matchingActivityCreatedHereUpdates() {
        #expect(L.decide(input(recording: rec, existing: [act("a", "r1")], current: "a")) == [.update(activityId: "a")])
    }
    @Test func duplicatesKeepOne() {
        let actions = L.decide(input(recording: rec, existing: [act("a", "r1"), act("b", "r1")], current: "b"))
        #expect(actions.contains(.endImmediately(activityId: "a")))
        #expect(actions.contains(.update(activityId: "b")))
        #expect(actions.count == 2)
    }
    @Test func differentRecordingActivityEndsAndNewStarts() {
        let actions = L.decide(input(recording: rec, existing: [act("x", "old")]))
        #expect(actions == [.endImmediately(activityId: "x"), .start])
    }
    @Test func endedActivitiesAreIgnored() {
        #expect(L.decide(input(recording: rec, existing: [act("a", "r1", ended: true)])) == [.start])
    }
    @Test func finishedAfterStop() {
        let actions = L.decide(input(sawStopping: true, existing: [act("a", "r1")], previous: "r1"))
        #expect(actions == [.endFinished(activityId: "a", dismissAfter: 8)])
    }
    @Test func discardEndsImmediately() {
        #expect(L.decide(input(existing: [act("a", "r1")], previous: "r1")) == [.endImmediately(activityId: "a")])
    }
    @Test func noRecordingNoActivityDoesNothing() {
        #expect(L.decide(input()).isEmpty)
    }
    @Test func staleOtherActivityEndsImmediatelyEvenAfterStop() {
        let actions = L.decide(input(sawStopping: true, existing: [act("z", "other")], previous: "r1"))
        #expect(actions == [.endImmediately(activityId: "z")])
    }
    @Test func disabledNeverStartsAndEndsExisting() {
        #expect(L.decide(input(recording: rec, enabled: false)).isEmpty)
        #expect(L.decide(input(recording: rec, enabled: false, existing: [act("a", "r1")])) == [.endImmediately(activityId: "a")])
    }
    @Test func recoveryBlockedDoesNotStartOrTouchMatching() {
        #expect(L.decide(input(recording: rec, blocked: true)).isEmpty)
        #expect(L.decide(input(recording: rec, existing: [act("a", "r1")], blocked: true)).isEmpty)
    }
    @Test func userDismissedDoesNotRestart() {
        #expect(L.decide(input(recording: rec, dismissed: "r1")).isEmpty)
        #expect(L.decide(input(recording: rec, dismissed: "other")) == [.start])
    }
    @Test func legacyFallbackIdIsStable() {
        let d = Date(timeIntervalSince1970: 1_800_000_123.9)
        #expect(LiveActivityRecordingIdentity.fallbackId(startedAt: d) == "legacy-1800000123")
        #expect(LiveActivityRecordingIdentity.fallbackId(startedAt: d) == LiveActivityRecordingIdentity.fallbackId(startedAt: d))
    }
}
