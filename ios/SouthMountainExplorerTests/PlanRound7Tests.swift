import CoreGraphics
import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike round 7: side-by-side passes (one lane per walk of an edge)
/// and Walk through (one numbered leg at a time).
struct PlanRound7Tests {
    typealias K = PlannerKit
    typealias L = PlannedRouteLanes

    // MARK: - Pass index per edge

    @Test func outAndBackGivesTwoPassesInOppositeDirections() {
        let p = L.passes([(edgeId: 5, direction: .forward), (edgeId: 5, direction: .backward)])
        #expect(p.map(\.index) == [0, 1])
        #expect(p.map(\.count) == [2, 2])
        #expect(p.map(\.sameDirectionAsFirst) == [true, false])
        // Both on the walker's right, which is opposite sides of the line.
        #expect(p.map(\.lane) == [0.5, 0.5])
        let sides = p.map { $0.lane * ($0.sameDirectionAsFirst ? 1 : -1) }
        #expect(sides == [0.5, -0.5])
    }

    @Test func loopWalksEachEdgeOnce() {
        let p = L.passes([(edgeId: 1, direction: .forward), (edgeId: 2, direction: .forward),
                          (edgeId: 3, direction: .backward)])
        #expect(p.map(\.count) == [1, 1, 1])
        #expect(p.map(\.index) == [0, 0, 0])
        #expect(p.allSatisfy { $0.lane == 0 })
    }

    @Test func threePassesSpreadAroundTheLine() {
        let p = L.passes([(edgeId: 7, direction: .forward), (edgeId: 7, direction: .forward),
                          (edgeId: 7, direction: .forward)])
        #expect(p.map(\.lane) == [1, 0, -1])
        // An unknown edge (negative id) is a single pass and does not count.
        let q = L.passes([(edgeId: -1, direction: .forward), (edgeId: -1, direction: .backward)])
        #expect(q.map(\.count) == [1, 1] && q.allSatisfy { $0.lane == 0 })
    }

    // MARK: - Lane offset math

    @Test func laneOffsetIsConstantOnScreen() {
        // Plus or minus 4 pt for two passes, at every zoom.
        for scale: CGFloat in [0.001, 0.5, 2, 37] {
            #expect(abs(L.offset(lane: 0.5, pointsPerUnit: scale) * scale - 4) < 1e-9)
            #expect(abs(L.offset(lane: -0.5, pointsPerUnit: scale) * scale + 4) < 1e-9)
        }
        #expect(L.offset(lane: 0, pointsPerUnit: 3) == 0)
        #expect(L.offset(lane: 0.5, pointsPerUnit: 0) == 0)
        #expect(L.offset(lane: 0.5, pointsPerUnit: .infinity) == 0)
    }

    @Test func offsetMovesToTheRightOfTravel() {
        // y grows downward: right of east is +y.
        let east = L.offset([CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)], by: 4)
        #expect(east == [CGPoint(x: 0, y: 4), CGPoint(x: 10, y: 4)])
        let west = L.offset([CGPoint(x: 10, y: 0), CGPoint(x: 0, y: 0)], by: 4)
        #expect(west == [CGPoint(x: 10, y: -4), CGPoint(x: 0, y: -4)])
        #expect(L.offset([CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)], by: 0) == [CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)])
    }

    @Test func offsetKeepsItsDistanceThroughABend() {
        // East then south: the corner moves along the mitre, 1 from both legs.
        let pts = L.offset([CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0), CGPoint(x: 10, y: 10)], by: 1)
        #expect(abs(pts[1].x - 9) < 1e-9 && abs(pts[1].y - 1) < 1e-9)
        #expect(abs(pts[2].x - 9) < 1e-9 && abs(pts[2].y - 10) < 1e-9)
        // A full turn-around keeps the incoming side, no spike.
        let back = L.offset([CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0), CGPoint(x: 0, y: 0)], by: 1)
        #expect(abs(back[1].y - 1) < 1e-9 && abs(back[1].x - 10) < 1e-9)
    }

    @Test func layoutStepsAcrossBetweenLanes() {
        let a = [CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)]
        let b = [CGPoint(x: 10, y: 0), CGPoint(x: 20, y: 0)]
        let out = L.layout([(a, 0), (b, 4)])
        #expect(out[0] == a)
        // The second line starts at the first one's end, then steps into its lane.
        #expect(out[1] == [CGPoint(x: 10, y: 0), CGPoint(x: 10, y: 4), CGPoint(x: 20, y: 4)])
        // Same lane: no extra point.
        #expect(L.layout([(a, 4), (b, 4)])[1].count == 2)
    }

    // MARK: - Route building

    @Test func outAndBackRouteHasTwoLanesAndLegRanges() throws {
        let a = K.trail("A", [(0, 0), (400, 0), (800, 0)])
        let net = TrailNetwork(trails: K.inputs([a]))
        let plan = try PlanStepsTests.plan(net, through: [(0, 0), (800, 0), (0, 0)])
        let names = Dictionary(uniqueKeysWithValues: K.inputs([a]).map { ($0.key, $0.trail.name) })
        let steps = PlanStepBuilder.steps(plan: plan, network: net, names: names)
        let route = PlannedRouteBuilder.build(plan: plan, edges: net.edges).with(steps: steps)
        #expect(route.hasLanes)
        #expect(route.pieces.count == 2)
        #expect(route.pieces.map(\.segment.lane) == [0.5, 0.5])
        #expect(route.pieces.map(\.walkIndex) == [0, 1])
        // Steps know their plan steps, and each leg draws only its own pass.
        #expect(steps.map(\.walkRange) == [0..<1, 1..<2])
        let back = route.segments(of: steps[1])
        #expect(back.count == 1)
        let first = try #require(back.first?.coords.first), end = K.ll(800, 0)
        #expect(Area.meters(first.lat, first.lon, end[0], end[1]) < 1)
        // Total length is unchanged by the lanes.
        #expect(abs(route.segments.reduce(0) { $0 + $1.lengthMeters } - plan.metrics.meters) < 1e-6)
    }

    @Test func loopRouteHasNoLanes() throws {
        let a = K.trail("A", [(0, 0), (500, 0), (500, 500)])
        let b = K.trail("B", [(500, 500), (0, 500), (0, 0)])
        let net = TrailNetwork(trails: K.inputs([a, b]))
        let plan = try PlanStepsTests.plan(net, through: [(0, 0), (500, 500), (0, 0)], via: ["A", "B"])
        let route = PlannedRouteBuilder.build(plan: plan, edges: net.edges)
        #expect(!route.hasLanes)
        #expect(route.segments.allSatisfy { $0.lane == 0 })
        #expect(route.segments.count == 1)
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
