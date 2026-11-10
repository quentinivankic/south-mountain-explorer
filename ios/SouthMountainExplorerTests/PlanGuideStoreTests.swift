import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release B task 4: Start hike hands the plan to the guide,
/// saved with its recording, and a relaunch resumes the guide on the same
/// plan and step, searching forward only.
struct PlanGuideStoreTests {
    typealias G = PlanGuideEngineTests
    typealias M = PlanProgressMatcherTests

    static func tempStore() -> PlanGuideStore {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("plan-guide-tests-\(UUID().uuidString)", isDirectory: true)
        return PlanGuideStore(fileURL: dir.appendingPathComponent("plan-guide.json"))
    }

    // MARK: - Store

    @Test func savedPlanComesBackAsTheSameLine() throws {
        let (r, s) = try PlanGuideRouteTests.outAndBack()
        let store = Self.tempStore()
        store.save(PlanGuideSnapshot(recordingId: "rec-1", steps: s, stepIndex: 2, isComplete: false))
        let loaded = try #require(store.load())
        #expect(loaded.recordingId == "rec-1")
        #expect(loaded.stepIndex == 2)
        #expect(loaded.isComplete == false)
        let steps = try #require(loaded.planSteps)
        #expect(steps.map(\.action) == s.map(\.action))
        #expect(steps.map(\.number) == s.map(\.number))
        #expect(steps.map(\.trailName) == s.map(\.trailName))
        #expect(steps.map(\.coords) == s.map(\.coords))
        #expect(PlanGuideRoute(steps: steps) == r)
    }

    @Test func everyActionAndKindRoundTrips() throws {
        let actions: [PlanStep.Action] = [.start, .straight, .keepLeft, .keepRight, .left, .right,
                                          .sharpLeft, .sharpRight, .turnAround]
        let kinds: [PlannedRoute.Kind] = [.trail, .connector, .bridge]
        var steps: [PlanStep] = []
        for (i, a) in actions.enumerated() {
            let c = PlannedRoute.Coord(lat: 33.3 + Double(i) * 0.001, lon: -112.0)
            let n = PlannedRoute.Coord(lat: 33.3 + Double(i + 1) * 0.001, lon: -112.0)
            steps.append(PlanStep(number: i + 1, action: a, kind: kinds[i % kinds.count],
                                  trailName: i % 2 == 0 ? "T\(i)" : nil, staysOnTrail: i % 3 == 0,
                                  meters: 111, at: c, coords: [c, n], isLast: i == actions.count - 1))
        }
        let snap = PlanGuideSnapshot(recordingId: "r", steps: steps)
        let decoded = try JSONDecoder().decode(PlanGuideSnapshot.self, from: JSONEncoder().encode(snap))
        #expect(decoded == snap)
        let back = try #require(decoded.planSteps)
        #expect(back == steps)
    }

    @Test func missingUnreadableOrNewerFileIsNoPlan() throws {
        let store = Self.tempStore()
        #expect(store.load() == nil)
        try FileManager.default.createDirectory(
            at: store.fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("not json".utf8).write(to: store.fileURL)
        #expect(store.load() == nil)
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        var snap = PlanGuideSnapshot(recordingId: "r", steps: s)
        snap.version = PlanGuideSnapshot.currentVersion + 1
        try JSONEncoder().encode(snap).write(to: store.fileURL)
        #expect(store.load() == nil)
        // A step value this build does not know is no plan, not a wrong plan.
        let good = try JSONEncoder().encode(PlanGuideSnapshot(recordingId: "r", steps: s))
        let json = String(decoding: good, as: UTF8.self)
            .replacingOccurrences(of: "\"turnAround\"", with: "\"uTurn\"")
        try Data(json.utf8).write(to: store.fileURL)
        let odd = try #require(store.load())
        #expect(odd.planSteps == nil)
    }

    @Test func clearRemovesTheSavedPlan() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let store = Self.tempStore()
        store.clear()
        store.save(PlanGuideSnapshot(recordingId: "r", steps: s))
        #expect(store.load() != nil)
        store.clear()
        #expect(store.load() == nil)
        #expect(!FileManager.default.fileExists(atPath: store.fileURL.path))
    }

    @Test func savedPlanIsRestoredOnlyForItsOwnRecording() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let snap = PlanGuideSnapshot(recordingId: "rec-1", steps: s)
        #expect(PlanGuideStore.decide(snap, activeRecordingId: "rec-1") == .restore)
        #expect(PlanGuideStore.decide(snap, activeRecordingId: "rec-2") == .discard)
        // No recording yet (or a recovery still pending): keep it for later.
        #expect(PlanGuideStore.decide(snap, activeRecordingId: nil) == .keep)
        #expect(PlanGuideStore.decide(snap, activeRecordingId: "") == .keep)
    }

    // MARK: - Relaunch

    /// Out and back, the app is closed on the way back down the dead end
    /// (same ground as the way out). The restored guide stays on the way
    /// back: it never returns to step 2, cues only the last turn, finishes once.
    @Test func relaunchOnTheWayBackResumesForwardFromTheSavedStep() throws {
        let (r, s) = try PlanGuideRouteTests.outAndBack()
        var w = M.Walker.kit()
        w.walk([(0, 0), (500, 0), (500, 300), (500, 0), (0, 0)])
        let fixes = w.fixes

        // Before the relaunch: walk until the guide is on step 3 (the way back).
        var before = G.engine(r)
        var split = 0
        for (i, f) in fixes.enumerated() {
            _ = before.ingest(f, now: f.timestamp)
            if before.stepIndex == 2 { split = i + 1; break }
        }
        #expect(split > 0)
        // Part way back down the dead end, 100 m further on.
        while split < fixes.count, before.matcher.cursorMeters < r.markers[2].startMeters + 100 {
            _ = before.ingest(fixes[split], now: fixes[split].timestamp)
            split += 1
        }
        #expect(before.stepIndex == 2)

        let store = Self.tempStore()
        store.save(PlanGuideSnapshot(recordingId: "rec-1", steps: s,
                                     stepIndex: before.stepIndex, isComplete: before.isComplete))

        // After: a new process loads the saved plan, 60 s later.
        let saved = try #require(store.load())
        #expect(PlanGuideStore.decide(saved, activeRecordingId: "rec-1") == .restore)
        let savedSteps = try #require(saved.planSteps)
        let route = try #require(PlanGuideRoute(steps: savedSteps))
        var after = G.engine(route)
        after.restore(toStep: saved.stepIndex, isComplete: saved.isComplete)
        #expect(after.stepIndex == 2)
        #expect(after.status.isComplete == false)
        let rest = fixes[split...].map {
            TurnCueFix(lat: $0.lat, lon: $0.lon, timestamp: $0.timestamp + 60,
                       horizontalAccuracy: $0.horizontalAccuracy, speed: $0.speed)
        }
        let t = G.run(&after, rest)
        #expect(t.stepAt.allSatisfy { $0 >= 2 })
        #expect(t.stepAt.last == 3)
        #expect(t.cues.map(\.junctionId) == [3, 3])
        #expect(t.cues.map(\.spoken) == ["Turn right in 300 feet", "Turn right"])
        #expect(t.of(.offTrail).isEmpty)
        #expect(t.completed.count == 1)
    }

    @Test func relaunchAfterTheFinishStaysFinishedWithoutASecondBuzz() throws {
        let (r, s) = try PlanGuideRouteTests.outAndBack()
        var w = M.Walker.kit()
        w.walk([(0, 0), (500, 0), (500, 300), (500, 0), (0, 0)])
        var before = G.engine(r)
        let t1 = G.run(&before, w.fixes)
        #expect(t1.completed.count == 1)

        let store = Self.tempStore()
        store.save(PlanGuideSnapshot(recordingId: "rec-1", steps: s,
                                     stepIndex: before.stepIndex, isComplete: before.isComplete))
        let saved = try #require(store.load())
        #expect(saved.isComplete)
        let savedSteps = try #require(saved.planSteps)
        var after = G.engine(try #require(PlanGuideRoute(steps: savedSteps)))
        after.restore(toStep: saved.stepIndex, isComplete: saved.isComplete)
        #expect(after.status.isComplete)
        #expect(after.status.stepIndex == 3)
        // Standing at the finish after the relaunch.
        w.stand((0, 0), seconds: 30)
        let t2 = G.run(&after, Array(w.fixes.suffix(30)))
        #expect(t2.completed.isEmpty)
        #expect(t2.cues.isEmpty)
    }
}
