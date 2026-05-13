import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `RecordingService.retargeted(_:newTrailId:)` — the
/// pure-function form of the build-11-PR-2 mid-recording trail
/// retarget. The instance method `retargetTrail` wraps this with
/// activeRecording lookup + persist; the math is here so it can be
/// exercised without touching the @MainActor singleton.
struct RetargetActiveRecordingTests {

    private static let baseRecording = ActiveRecording(
        areaId: "south-mountain",
        mode: .trail,
        trailId: "alta",
        startedAt: Date(timeIntervalSince1970: 1_700_000_000),
        path: [
            [33.3, -112.0, 1_700_000_000],
            [33.3001, -112.0, 1_700_000_010],
        ],
        distanceMi: 0.5,
        priorCompleteTrailIds: ["pima-canyon-loop-trail"]
    )

    // MARK: - Happy path

    @Test func retargetSwitchesTrailIdOnly() throws {
        let out = try #require(
            RecordingService.retargeted(Self.baseRecording, newTrailId: "javelina-canyon-trail")
        )
        #expect(out.trailId == "javelina-canyon-trail")
        // Everything else preserved.
        #expect(out.areaId == Self.baseRecording.areaId)
        #expect(out.mode == Self.baseRecording.mode)
        #expect(out.startedAt == Self.baseRecording.startedAt)
        #expect(out.path == Self.baseRecording.path)
        #expect(out.distanceMi == Self.baseRecording.distanceMi)
        #expect(out.priorCompleteTrailIds == Self.baseRecording.priorCompleteTrailIds)
    }

    // MARK: - No-op cases

    @Test func retargetReturnsNilForRoamMode() {
        let roam = ActiveRecording(
            areaId: "south-mountain",
            mode: .roam,
            trailId: nil,
            startedAt: Date(),
            path: [],
            distanceMi: 0,
            priorCompleteTrailIds: []
        )
        let out = RecordingService.retargeted(roam, newTrailId: "alta")
        #expect(out == nil, "Roam-mode recordings have no trail to switch from")
    }

    @Test func retargetReturnsNilWhenSameTrailId() {
        let out = RecordingService.retargeted(Self.baseRecording, newTrailId: "alta")
        #expect(out == nil, "Retargeting to the current trail is a no-op")
    }

    @Test func retargetSucceedsEvenWhenPathIsEmpty() throws {
        // Edge case: user tapped Start then immediately tried to
        // switch trails before any GPS samples arrived. Should
        // still work — there's nothing inherent about an empty
        // path that prevents a retarget.
        let empty = ActiveRecording(
            areaId: "south-mountain",
            mode: .trail,
            trailId: "alta",
            startedAt: Date(),
            path: [],
            distanceMi: 0,
            priorCompleteTrailIds: nil
        )
        let out = try #require(RecordingService.retargeted(empty, newTrailId: "javelina-canyon-trail"))
        #expect(out.trailId == "javelina-canyon-trail")
        #expect(out.path.isEmpty)
    }

    @Test func retargetPreservesNilPriorCompleteTrailIds() throws {
        // Old recordings (from before priorCompleteTrailIds was
        // added) have nil here. Retarget must not synthesize a
        // value — it preserves nil so the field's
        // "loaded from pre-build-6 history" semantics carry over.
        let legacy = ActiveRecording(
            areaId: "south-mountain",
            mode: .trail,
            trailId: "alta",
            startedAt: Date(),
            path: [],
            distanceMi: 0,
            priorCompleteTrailIds: nil
        )
        let out = try #require(RecordingService.retargeted(legacy, newTrailId: "javelina-canyon-trail"))
        #expect(out.priorCompleteTrailIds == nil)
    }
}
