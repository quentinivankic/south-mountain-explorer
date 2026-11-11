import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release B task 3: the guide. GPS trust, a heads-up about
/// 300 ft before each turn and a cue at the turn, the finish, and the
/// off-route warning, replayed over synthetic walks with GPS noise.
struct PlanGuideEngineTests {
    typealias K = PlannerKit
    typealias M = PlanProgressMatcherTests

    /// What a replay did, fix by fix.
    struct Trace {
        /// Fix index, event, and the step after that fix.
        var events: [(i: Int, e: TurnCueEvent, step: Int)] = []
        /// Fix indices where the route was completed.
        var completed: [Int] = []
        var stepAt: [Int] = []
        var statuses: [PlanGuideEngine.Status] = []

        func of(_ k: TurnCueEvent.Kind) -> [TurnCueEvent] { events.map(\.e).filter { $0.kind == k } }
        /// Heads-up and turn cues in order.
        var cues: [TurnCueEvent] { events.map(\.e).filter { $0.kind == .approach || $0.kind == .atJunction } }
    }

    static func engine(_ r: PlanGuideRoute, units: UnitsPreference = .imperial) -> PlanGuideEngine {
        PlanGuideEngine(route: r, config: PlanGuideConfig(units: units))
    }

    static func run(_ e: inout PlanGuideEngine, _ fixes: [TurnCueFix], into t: inout Trace) {
        for f in fixes {
            let i = t.stepAt.count
            let u = e.ingest(f, now: f.timestamp)
            for ev in u.events { t.events.append((i, ev, e.stepIndex)) }
            if u.routeCompleted { t.completed.append(i) }
            t.stepAt.append(e.stepIndex)
            t.statuses.append(e.status)
        }
    }

    static func run(_ e: inout PlanGuideEngine, _ fixes: [TurnCueFix]) -> Trace {
        var t = Trace()
        run(&e, fixes, into: &t)
        return t
    }

    // MARK: - Turn cues

    @Test func outAndBackGetsAHeadsUpAndACueAtEveryTurnThenRouteComplete() throws {
        let (r, s) = try PlanGuideRouteTests.outAndBack()
        #expect(s.map(\.action) == [.start, .left, .turnAround, .right])
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (500, 0), (500, 300), (500, 0), (0, 0)])
        let t = Self.run(&e, w.fixes)
        #expect(t.cues.map(\.junctionId) == [1, 1, 2, 2, 3, 3])
        #expect(t.cues.map(\.kind) == [.approach, .atJunction, .approach, .atJunction, .approach, .atJunction])
        #expect(t.cues.map(\.spoken) == ["Turn left in 300 feet", "Turn left",
                                         "Turn around in 300 feet", "Turn around",
                                         "Turn right in 300 feet", "Turn right"])
        // Direction only: no trail names anywhere.
        #expect(t.events.allSatisfy { !$0.e.spoken.contains("A") && !$0.e.spoken.contains("B") })
        #expect(t.of(.offTrail).isEmpty && t.of(.backOnTrail).isEmpty && t.of(.gpsWeak).isEmpty)
        let junctions: [Int: (Double, Double)] = [1: (500, 0), 2: (500, 300), 3: (500, 0)]
        for c in t.events where c.e.kind == .approach || c.e.kind == .atJunction {
            let id = try #require(c.e.junctionId)
            let j = try #require(junctions[id])
            let p = w.truth[c.i]
            let away = TurnCueGeometry.distance(p, LocalPt(x: j.0, y: j.1))
            if c.e.kind == .approach {
                // About 300 ft before the turn.
                #expect(away > 60 && away < 100, "heads-up \(id) at \(away) m")
                #expect((c.e.distanceMeters ?? 0) <= PlanGuideConfig(units: .imperial).headsUpMeters)
            } else {
                // At the turn, before the bar moves on.
                #expect(away < 30, "turn \(id) at \(away) m")
                #expect(c.step == id - 1)
            }
        }
        // Finish: once, near the end, with no voice.
        #expect(t.completed.count == 1)
        let done = try #require(t.completed.first)
        #expect(TurnCueGeometry.distance(w.truth[done], LocalPt(x: 0, y: 0)) < 25)
        #expect(!t.events.contains { $0.i >= done })
        #expect(e.status.isComplete && e.status.stepIndex == 3 && e.status.metersToNextTurn == 0)
        // Each cue is stale once its turn is behind you.
        for c in t.cues { #expect(e.isStale(c)) }
    }

    @Test func distanceToNextTurnCountsDown() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (480, 0)])
        let t = Self.run(&e, w.fixes)
        let ds = t.statuses.dropFirst(5).map(\.metersToNextTurn)
        #expect(zip(ds, ds.dropFirst()).allSatisfy { $0 >= $1 })
        #expect(abs((ds.last ?? 0) - 20) < 10)
        #expect(t.statuses.dropFirst(5).allSatisfy { $0.stepIndex == 0 && $0.stepCount == 4 && $0.gpsTrusted })
    }

    @Test func standingStillAtAJunctionSpeaksEachCueOnce() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (498, 0)])
        w.sigma = 2.5
        w.stand((500, 0), seconds: 180)
        w.sigma = 3
        w.walk([(500, 0), (500, 60)])
        let t = Self.run(&e, w.fixes)
        #expect(t.cues.map(\.junctionId) == [1, 1])
        #expect(t.cues.map(\.kind) == [.approach, .atJunction])
        #expect(t.of(.offTrail).isEmpty)
        #expect(e.stepIndex == 1)
    }

    @Test func aTurnTooCloseForAHeadsUpGetsOnlyTheCueAtTheTurn() throws {
        // 25 m to the turn: inside the heads-up floor (the cue at the turn plus 10 m).
        let a = K.trail("A", [(0, 0), (25, 0)]), b = K.trail("B", [(25, 0), (25, 400)])
        let (s, _) = try PlanStepsTests.steps([a, b], [(0, 0), (25, 0), (25, 400)], shape: .oneWay)
        #expect(s.map(\.action) == [.start, .left])
        let r = try #require(PlanGuideRoute(steps: s))
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (25, 0), (25, 400)])
        let t = Self.run(&e, w.fixes)
        #expect(t.cues.map(\.kind) == [.atJunction])
        #expect(t.cues.first?.spoken == "Turn left")
        #expect(t.completed.count == 1)
    }

    // MARK: - Back and Next

    @Test func nextSkipsATurnAndBackReArmsIt() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        // Next at 100 m: turn 1 is behind the guide, so it is never cued.
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (100, 0)])
        var t = Self.run(&e, w.fixes)
        e.move(toStep: 1)
        #expect(e.status.stepIndex == 1)
        var n = w.fixes.count
        w.walk([(100, 0), (500, 0), (500, 300)])
        Self.run(&e, Array(w.fixes[n...]), into: &t)
        #expect(t.cues.map(\.junctionId) == [2, 2])

        // Back after the heads-up for turn 1: walking the leg again cues it again.
        var e2 = Self.engine(r)
        var w2 = M.Walker.kit()
        w2.walk([(0, 0), (450, 0)])
        var t2 = Self.run(&e2, w2.fixes)
        #expect(t2.cues.map(\.kind) == [.approach])
        e2.move(toStep: 0)
        #expect(e2.status.stepIndex == 0 && e2.matcher.cursorMeters == 0)
        n = w2.fixes.count
        w2.walk([(0, 0), (500, 0), (500, 100)])
        Self.run(&e2, Array(w2.fixes[n...]), into: &t2)
        #expect(t2.cues.map(\.kind) == [.approach, .approach, .atJunction])
        #expect(t2.cues.allSatisfy { $0.junctionId == 1 })
    }

    @Test func backAfterTheFinishReopensTheGuide() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (500, 0), (500, 300), (500, 0), (0, 0)])
        _ = Self.run(&e, w.fixes)
        #expect(e.isComplete)
        e.move(toStep: 2)
        #expect(!e.isComplete && e.status.stepIndex == 2 && e.status.metersToNextTurn > 250)
    }

    // MARK: - Skipping ahead

    @Test func shortcutWarnsOffRouteThenSkipsTheMissedTurnsSilently() throws {
        let (s, _) = try PlanStepsTests.steps(M.square, M.squareWalk)
        #expect(s.count == 4)
        let r = try #require(PlanGuideRoute(steps: s))
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (300, 0), (300, 600), (0, 600), (0, 0)])
        let t = Self.run(&e, w.fixes)
        // 600 m across the middle: warned, then once more 2 min later and
        // 50 m farther (the repeat rule), then back on route on C.
        let off = t.events.filter { $0.e.kind == .offTrail }
        #expect(off.count == 2)
        if off.count == 2 { #expect(w.fixes[off[1].i].timestamp - w.fixes[off[0].i].timestamp >= 120) }
        #expect(t.of(.backOnTrail).count == 1)
        #expect(t.cues.map(\.junctionId) == [3, 3])
        #expect(t.completed.count == 1)
    }

    // MARK: - Off route

    @Test func eightyMetreWrongTurnWarnsOnceThenBackOnRoute() throws {
        let a = K.trail("A", [(0, 0), (500, 0)]), b = K.trail("B", [(500, 0), (500, 500)])
        let c = K.trail("C", [(500, 0), (1000, 0)])
        let (s, _) = try PlanStepsTests.steps([a, b, c], [(0, 0), (500, 0), (500, 500)], shape: .oneWay)
        let r = try #require(PlanGuideRoute(steps: s))
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (500, 0), (580, 0), (500, 0), (500, 500)])
        let t = Self.run(&e, w.fixes)
        let off = t.of(.offTrail), back = t.of(.backOnTrail)
        #expect(off.count == 1 && back.count == 1)
        #expect(off.first?.spoken == "Off route" && off.first?.shortText == "Off route")
        #expect(back.first?.spoken == "Back on route")
        // The warning carries the straight-line distance to the route.
        let d = try #require(off.first?.distanceMeters)
        #expect(d > 40 && d < 85)
        let warned = try #require(t.events.first { $0.e.kind == .offTrail }?.i)
        let cleared = try #require(t.events.first { $0.e.kind == .backOnTrail }?.i)
        #expect(t.statuses[warned..<cleared].allSatisfy { $0.offRouteMeters != nil })
        #expect(t.statuses[cleared].offRouteMeters == nil)
        // The step never moved during the detour; the turn is cued once.
        #expect(t.stepAt[...cleared].allSatisfy { $0 == 0 })
        #expect(t.cues.map(\.kind) == [.approach, .atJunction])
        #expect(t.completed.count == 1)
    }

    @Test func shortWrongTurnInsideTheLimitIsSilent() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (200, 0), (200, -30), (200, 0), (480, 0)])
        let t = Self.run(&e, w.fixes)
        #expect(t.of(.offTrail).isEmpty && t.of(.backOnTrail).isEmpty)
    }

    @Test func offRouteRepeatsOnlyAfterTwoMinutesAndFiftyMetresFarther() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (200, 0), (200, -100)])
        let standFrom = w.fixes.count
        w.stand((200, -100), seconds: 300)
        let standTo = w.fixes.count
        w.walk([(200, -100), (200, -200)])
        w.stand((200, -200), seconds: 150)
        let t = Self.run(&e, w.fixes)
        let off = t.events.filter { $0.e.kind == .offTrail }
        // First on the way out, again on the way to 200 m, and once more
        // standing at 200 m. Never while standing still at 100 m.
        #expect(off.count == 3)
        #expect(!off.contains { $0.i >= standFrom && $0.i < standTo })
        for (a, b) in zip(off, off.dropFirst()) {
            #expect(w.fixes[b.i].timestamp - w.fixes[a.i].timestamp >= 120)
            #expect((b.e.distanceMeters ?? 0) - (a.e.distanceMeters ?? 0) >= 50)
        }
        #expect(t.of(.backOnTrail).isEmpty)
        #expect(abs((e.status.offRouteMeters ?? 0) - 200) < 15)
    }

    @Test func noOffRouteWarningBeforeTenSecondsOnTheRoute() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        // Starts 100 m from the route and stays there.
        w.stand((100, -100), seconds: 120)
        let t = Self.run(&e, w.fixes)
        #expect(t.of(.offTrail).isEmpty)
    }

    // MARK: - GPS trust

    @Test func weakGpsThroughAJunctionIsSilentFreezesTheStepAndResumes() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (400, 0)])
        let good = w.fixes.count
        w.accuracy = 65
        w.walk([(400, 0), (500, 0), (500, 40)])
        let weakEnd = w.fixes.count
        w.accuracy = 5
        w.walk([(500, 40), (500, 300), (500, 0), (0, 0)])
        let t = Self.run(&e, w.fixes)
        #expect(t.of(.gpsWeak).count == 1)
        #expect(!t.events.contains { $0.i >= good && $0.i < weakEnd && $0.e.kind != .gpsWeak })
        #expect(t.statuses[good..<weakEnd].allSatisfy { !$0.gpsTrusted && $0.stepIndex == 0 && $0.offRouteMeters == nil })
        // Turn 1 went by under weak GPS: never cued late. The rest is cued as usual.
        #expect(t.cues.map(\.junctionId) == [2, 2, 3, 3])
        #expect(t.of(.offTrail).isEmpty)
        #expect(t.completed.count == 1)
        // About 5 s of good GPS before the guide picks up again.
        let resumed = try #require(t.statuses.indices.first { $0 >= weakEnd && t.statuses[$0].gpsTrusted })
        #expect(resumed - weakEnd >= 4 && resumed - weakEnd <= 7)
        #expect(t.stepAt[resumed] == 1)
    }

    @Test func aStalledFixStreamStopsTrustingGps() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var e = Self.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (100, 0)])
        _ = Self.run(&e, w.fixes)
        #expect(e.status.gpsTrusted)
        let last = try #require(w.fixes.last?.timestamp)
        #expect(e.tick(now: last + 5).events.isEmpty && e.status.gpsTrusted)
        _ = e.tick(now: last + 11)
        #expect(!e.status.gpsTrusted)
    }

    // MARK: - Phrasing

    @Test func cuesAreDirectionOnly() {
        #expect(TurnCuePhrasing.planApproach(symbol: .turnLeft, meters: 91.44, units: .imperial) == "Turn left in 300 feet")
        #expect(TurnCuePhrasing.planApproach(symbol: .keepRight, meters: 91.44, units: .metric) == "Keep right in 90 meters")
        #expect(TurnCuePhrasing.planApproach(symbol: .turnAround, meters: 91.44, units: .imperial) == "Turn around in 300 feet")
        #expect(TurnCuePhrasing.near(symbol: .straight) == "Continue straight")
        #expect(TurnCuePhrasing.near(symbol: .turnAround) == "Turn around")
        #expect(PlanGuideEngine.symbol(for: .start) == nil)
        let mapped: [(PlanStep.Action, TurnCueSymbol)] = [
            (.straight, .straight), (.keepLeft, .keepLeft), (.keepRight, .keepRight), (.left, .turnLeft),
            (.right, .turnRight), (.sharpLeft, .sharpLeft), (.sharpRight, .sharpRight), (.turnAround, .turnAround)]
        for (a, sym) in mapped { #expect(PlanGuideEngine.symbol(for: a) == sym) }
    }
}
