import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release B task 5: the live bar during a guided hike. Its
/// wording, the step following the guide on its own, Back and Next, Exit,
/// and the route redrawn after a relaunch.
struct PlanGuideBarTests {
    typealias G = PlanGuideEngineTests
    typealias M = PlanProgressMatcherTests

    static func status(step: Int, count: Int = 4, meters: Double, off: Double? = nil,
                       complete: Bool = false) -> PlanGuideEngine.Status {
        PlanGuideEngine.Status(stepIndex: step, stepCount: count, metersToNextTurn: meters,
                               offRouteMeters: off, isComplete: complete, gpsTrusted: true)
    }

    // MARK: - Wording

    @Test func barTextForEachState() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        #expect(s.map(\.action) == [.start, .left, .turnAround, .right])
        let first = Self.status(step: 0, meters: 1290)
        #expect(PlanGuideText.counter(first) == "Step 1 of 4")
        #expect(PlanGuideText.line(steps: s, status: first, units: .imperial)
                == "1. \(s[0].instruction), 0.8 mi to next turn")
        // The last step counts down to the finish.
        let last = Self.status(step: 3, meters: 120)
        #expect(PlanGuideText.line(steps: s, status: last, units: .imperial)
                == "4. \(s[3].instruction), 400 ft to finish")
        // Off route replaces the step with the straight-line distance to the route.
        let off = Self.status(step: 1, meters: 200, off: 46)
        #expect(PlanGuideText.line(steps: s, status: off, units: .imperial) == "Off route, 150 ft to route")
        #expect(PlanGuideText.line(steps: s, status: off, units: .metric) == "Off route, 50 m to route")
        // The finish: "Route complete", whatever else is set.
        let done = Self.status(step: 3, meters: 0, off: 60, complete: true)
        #expect(PlanGuideText.line(steps: s, status: done, units: .imperial) == "Route complete")
        // A step index past the steps (should not happen) is an empty line, not a crash.
        #expect(PlanGuideText.line(steps: s, status: Self.status(step: 9, meters: 0), units: .imperial) == "")
    }

    @Test func countdownDistanceLadder() {
        let ft = TurnCuePhrasing.feetPerMeter
        #expect(PlanGuideText.distance(meters: 0, units: .imperial) == "50 ft")
        #expect(PlanGuideText.distance(meters: 100 / ft, units: .imperial) == "100 ft")
        #expect(PlanGuideText.distance(meters: 320 / ft, units: .imperial) == "300 ft")
        #expect(PlanGuideText.distance(meters: 990 / ft, units: .imperial) == "1000 ft")
        #expect(PlanGuideText.distance(meters: 1609.344 * 0.8, units: .imperial) == "0.8 mi")
        #expect(PlanGuideText.distance(meters: 1609.344 * 12.34, units: .imperial) == "12.3 mi")
        #expect(PlanGuideText.distance(meters: 3, units: .metric) == "10 m")
        #expect(PlanGuideText.distance(meters: 456, units: .metric) == "460 m")
        #expect(PlanGuideText.distance(meters: 1440, units: .metric) == "1.4 km")
        #expect(PlanGuideText.distance(meters: .nan, units: .metric) == "10 m")
    }

    /// Walking the out and back, the bar moves on by itself, in order, never
    /// back to an earlier step, and ends on "Route complete".
    @Test func barMovesOnByItselfInOrderAndEndsComplete() throws {
        let (r, s) = try PlanGuideRouteTests.outAndBack()
        var e = G.engine(r)
        var w = M.Walker.kit()
        w.walk([(0, 0), (500, 0), (500, 300), (500, 0), (0, 0)])
        let t = G.run(&e, w.fixes)
        let lines = t.statuses.map { PlanGuideText.line(steps: s, status: $0, units: .imperial) }
        var order: [String] = []
        for l in lines {
            let key = l == "Route complete" ? "done" : String(l.prefix(2))
            if order.last != key { order.append(key) }
        }
        #expect(order == ["1.", "2.", "3.", "4.", "done"])
        #expect(lines.allSatisfy { !$0.hasPrefix("Off route") })
        #expect(t.statuses.map(\.stepIndex) == t.statuses.map(\.stepIndex).sorted())
        #expect(lines.contains { $0.hasSuffix("to finish") })
    }

    // MARK: - Map follows the guide

    @MainActor
    @Test func mapLegFollowsTheGuideOnlyDuringTheHikeItStarted() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let controller = PlanHikeController()
        controller.loadPreviewForTesting([.init(id: "best", title: "Best", route: .guided(steps: s))])
        controller.showOnMap()
        // Before Start hike the guide does not drive the map.
        controller.followGuide(step: 2)
        #expect(controller.selectedStep == nil)
        controller.markHikeStarted()
        let fit0 = controller.fitTick
        controller.followGuide(step: 0)
        #expect(controller.selectedStep == 0)
        #expect(controller.walkThrough.counter == "Step 1 of 4")
        #expect(controller.fitTick == fit0 &+ 1)
        // The same step again (every poll) does not refit and fight panning.
        controller.followGuide(step: 0)
        #expect(controller.fitTick == fit0 &+ 1)
        // Auto step, or Back and Next through the guide.
        controller.followGuide(step: 2)
        #expect(controller.selectedStep == 2 && controller.fitTick == fit0 &+ 2)
        controller.followGuide(step: 1)
        #expect(controller.selectedStep == 1)
        // Out of range is clamped into the route.
        controller.followGuide(step: 9)
        #expect(controller.selectedStep == 3)
        // Exit ends the guide: the whole route again, still drawn.
        controller.followGuide(step: nil)
        #expect(controller.selectedStep == nil && controller.preview.isShowing && controller.hikeStarted)
        // A step marker on the map matches the walked leg.
        #expect(controller.focusCoords == nil)
        controller.followGuide(step: 3)
        #expect(controller.focusCoords == s[3].coords)
    }

    @MainActor
    @Test func relaunchRedrawsTheRouteFromTheSavedSteps() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let controller = PlanHikeController()
        #expect(!controller.preview.isShowing)
        controller.restoreGuidedRoute(steps: s)
        #expect(controller.preview.isShowing && controller.hikeStarted)
        let route = try #require(controller.preview.selected?.route)
        #expect(route.steps == s)
        #expect(route.segments.count == s.count)
        #expect(abs(route.segments.reduce(0) { $0 + $1.lengthMeters } - s.reduce(0) { $0 + $1.meters }) < 1e-6)
        // An out and back ends where it started: no separate end pin.
        #expect(route.start == s[0].at && route.end == nil)
        controller.followGuide(step: 2)
        #expect(controller.selectedStep == 2)
        // A route already up is left alone.
        let fit = controller.fitTick
        controller.restoreGuidedRoute(steps: Array(s.prefix(2)))
        #expect(controller.preview.selected?.route.steps == s && controller.fitTick == fit)
        // Nothing to draw stays empty.
        let empty = PlanHikeController()
        empty.restoreGuidedRoute(steps: [])
        #expect(!empty.preview.isShowing && !empty.hikeStarted)
    }

    @Test func oneWayRedrawGetsAnEndPin() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let oneWay = Array(s.prefix(2))
        let route = PlannedRoute.guided(steps: oneWay)
        #expect(route.end == oneWay.last?.coords.last)
    }
}
