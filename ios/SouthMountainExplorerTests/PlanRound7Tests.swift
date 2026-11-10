import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike round 7: Walk through (one numbered leg at a time).
struct PlanRound7Tests {
    typealias K = PlannerKit

    // MARK: - Route building

    @Test func outAndBackRouteHasLegRanges() throws {
        let a = K.trail("A", [(0, 0), (400, 0), (800, 0)])
        let net = TrailNetwork(trails: K.inputs([a]))
        let plan = try PlanStepsTests.plan(net, through: [(0, 0), (800, 0), (0, 0)])
        let names = Dictionary(uniqueKeysWithValues: K.inputs([a]).map { ($0.key, $0.trail.name) })
        let steps = PlanStepBuilder.steps(plan: plan, network: net, names: names)
        let route = PlannedRouteBuilder.build(plan: plan, edges: net.edges).with(steps: steps)
        #expect(route.pieces.count == 2)
        #expect(route.pieces.map(\.walkIndex) == [0, 1])
        // Steps know their plan steps, and each leg draws only its own walk.
        #expect(steps.map(\.walkRange) == [0..<1, 1..<2])
        let back = route.segments(of: steps[1])
        #expect(back.count == 1)
        let first = try #require(back.first?.coords.first), end = K.ll(800, 0)
        #expect(Area.meters(first.lat, first.lon, end[0], end[1]) < 1)
        // Total length matches the plan.
        #expect(abs(route.segments.reduce(0) { $0 + $1.lengthMeters } - plan.metrics.meters) < 1e-6)
    }

    // MARK: - Walk through state

    @Test func walkThroughNextBackAndBounds() {
        var w = PlanWalkThrough(count: 3)
        #expect(!w.isActive && w.counter == nil && !w.canGoNext && !w.canGoBack)
        var moved = w.next()
        #expect(!moved)
        moved = w.back()
        #expect(!moved)
        moved = w.enter()
        #expect(moved && w.index == 0 && w.counter == "Step 1 of 3")
        #expect(!w.canGoBack && w.canGoNext)
        moved = w.back()
        #expect(!moved && w.index == 0)
        w.next()
        moved = w.next()
        #expect(moved && w.index == 2 && w.counter == "Step 3 of 3")
        moved = w.next()
        #expect(!w.canGoNext && !moved && w.index == 2)
        moved = w.back()
        #expect(moved && w.index == 1)
        w.exit()
        #expect(!w.isActive && w.index == nil)
        // Entering is clamped into the route; an empty route never starts.
        w.enter(at: 9)
        #expect(w.index == 2)
        w.enter(at: -3)
        #expect(w.index == 0)
        var empty = PlanWalkThrough(count: 0)
        moved = empty.enter()
        #expect(!moved && !empty.isActive)
        #expect(PlanWalkThrough.announcement(counter: "Step 3 of 9", line: "3. Left onto Lake Loop, 0.80 mi")
                == "Step 3 of 9. 3. Left onto Lake Loop, 0.80 mi")
    }

    @MainActor
    @Test func listTapEntersWalkThroughAtThatStep() throws {
        let a = K.trail("A", [(0, 0), (500, 0)])
        let b = K.trail("B", [(500, 0), (500, -500)])
        let c = K.trail("C", [(500, 0), (1000, 0)])
        let (steps, plan) = try PlanStepsTests.steps([a, b, c], [(0, 0), (500, 0), (500, -500), (500, 0), (1000, 0)],
                                                     shape: .oneWay)
        #expect(steps.count >= 3)
        let net = TrailNetwork(trails: K.inputs([a, b, c]))
        let route = PlannedRouteBuilder.build(plan: plan, edges: net.edges).with(steps: steps)
        let controller = PlanHikeController()
        controller.loadPreviewForTesting([.init(id: "best", title: "Best", route: route)])
        controller.showOnMap()
        #expect(controller.selectedStep == nil)
        let ticks = controller.walkThroughStartTick
        controller.selectStep(2)
        #expect(controller.selectedStep == 2)
        #expect(controller.walkThrough.counter == "Step 3 of \(steps.count)")
        #expect(controller.walkThroughStartTick == ticks + 1)
        #expect(controller.focusCoords == steps[2].coords)
        controller.previousStep()
        #expect(controller.selectedStep == 1)
        // A second tap while walking moves to that step without restarting.
        controller.selectStep(0)
        #expect(controller.selectedStep == 0 && controller.walkThroughStartTick == ticks + 1)
        controller.previousStep()
        #expect(controller.selectedStep == 0)
        controller.exitWalkThrough()
        #expect(controller.selectedStep == nil && controller.focusCoords == nil)
        // Out of range taps are clamped; Walk through starts at step 1 by default.
        controller.startWalkThrough()
        #expect(controller.selectedStep == 0)
        controller.selectStep(nil)
        #expect(!controller.walkThrough.isActive)
    }
}
