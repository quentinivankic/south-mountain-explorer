import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release B task 1: the planned walk as one line in walking
/// order, with a marker where each numbered step starts.
struct PlanGuideRouteTests {
    typealias K = PlannerKit

    /// Out and back: east on A, left onto the dead-end B, turn around, right
    /// back onto A to the start.
    static func outAndBack() throws -> (route: PlanGuideRoute, steps: [PlanStep]) {
        let a = K.trail("A", [(0, 0), (500, 0)])
        let b = K.trail("B", [(500, 0), (500, 300)])
        let (s, _) = try PlanStepsTests.steps([a, b], [(0, 0), (500, 0), (500, 300), (500, 0), (0, 0)])
        return (try #require(PlanGuideRoute(steps: s)), s)
    }

    @Test func outAndBackHasOneMarkerPerStepAtItsDistance() throws {
        let (r, s) = try Self.outAndBack()
        #expect(s.map(\.action) == [.start, .left, .turnAround, .right])
        #expect(r.markers.map(\.number) == [1, 2, 3, 4])
        #expect(r.markers.map(\.action) == s.map(\.action))
        #expect(r.markers.map(\.isLast) == [false, false, false, true])
        #expect(r.markers[2].isTurnAround)
        let expected = [0.0, 500, 800, 1100]
        for (m, e) in zip(r.markers, expected) { #expect(abs(m.startMeters - e) < 3) }
        #expect(abs(r.length - 1600) < 5)
        // Steps join end to start and cover the whole line.
        #expect(r.markers.first?.startMeters == 0)
        #expect(r.markers.last?.endMeters == r.length)
        for (m, n) in zip(r.markers, r.markers.dropFirst()) { #expect(m.endMeters == n.startMeters) }
        // Each marker spans its step's length.
        for (m, step) in zip(r.markers, s) { #expect(abs(m.meters - step.meters) < max(2, step.meters * 0.01)) }
    }

    @Test func repeatPassGetsItsOwnLaterDistance() throws {
        let (r, _) = try Self.outAndBack()
        // 250 m out on A and 250 m from the end on the way back are the same
        // ground, at different distances along the line and in different steps.
        let out = r.point(at: 250), back = r.point(at: 1350)
        #expect(TurnCueGeometry.distance(out, back) < 3)
        #expect(r.markerIndex(at: 250) == 0)
        #expect(r.markerIndex(at: 1350) == 3)
        // Up B and back down B: same ground, steps 2 and 3.
        #expect(TurnCueGeometry.distance(r.point(at: 650), r.point(at: 950)) < 3)
        #expect(r.markerIndex(at: 650) == 1)
        #expect(r.markerIndex(at: 950) == 2)
    }

    @Test func markerIndexAndPointClampAndSwitchAtStepStarts() throws {
        let (r, _) = try Self.outAndBack()
        #expect(r.markerIndex(at: -10) == 0)
        #expect(r.markerIndex(at: r.length + 50) == 3)
        for m in r.markers {
            #expect(r.markerIndex(at: m.startMeters) == m.index)
            if m.index > 0 { #expect(r.markerIndex(at: m.startMeters - 0.01) == m.index - 1) }
        }
        #expect(r.point(at: -5) == r.points.first)
        #expect(r.point(at: r.length + 5) == r.points.last)
        // The start is the local origin; the junction is 500 m east of it.
        #expect(TurnCueGeometry.distance(r.point(at: 0), LocalPt(x: 0, y: 0)) < 0.01)
        let j = K.ll(500, 0)
        #expect(TurnCueGeometry.distance(r.point(at: r.markers[1].startMeters), r.local(lat: j[0], lon: j[1])) < 2)
        // The turnaround marker sits at the dead end.
        let end = K.ll(500, 300)
        #expect(TurnCueGeometry.distance(r.point(at: r.markers[2].startMeters), r.local(lat: end[0], lon: end[1])) < 2)
    }

    @Test func noDuplicateVerticesWhereStepsJoin() throws {
        let (r, _) = try Self.outAndBack()
        for (a, b) in zip(r.points, r.points.dropFirst()) {
            #expect(TurnCueGeometry.distance(a, b) >= PlanGuideRoute.joinMeters)
        }
        #expect(r.cumulative.count == r.points.count)
        #expect(zip(r.cumulative, r.cumulative.dropFirst()).allSatisfy { $0 < $1 })
    }

    @Test func builtFromTheSelectedPlannedRoutesSteps() throws {
        let (_, s) = try Self.outAndBack()
        let planned = PlannedRoute(segments: [], tags: [], start: nil, end: nil).with(steps: s)
        let r = try #require(PlanGuideRoute(route: planned))
        #expect(r == PlanGuideRoute(steps: s))
    }

    @Test func noStepsMeansNoLine() {
        #expect(PlanGuideRoute(steps: []) == nil)
        #expect(PlanGuideRoute(route: .empty) == nil)
        let one = PlanStep(number: 1, action: .start, kind: .trail, trailName: "A", staysOnTrail: false,
                           meters: 0, at: .init(lat: 33.34, lon: -112), coords: [.init(lat: 33.34, lon: -112)],
                           isLast: true)
        #expect(PlanGuideRoute(steps: [one]) == nil)
    }

    @Test func oneWayStepsInWalkingOrder() throws {
        // East on A, right onto B: one way, two steps, line ends at B's far end.
        let a = K.trail("A", [(0, 0), (500, 0)])
        let b = K.trail("B", [(500, 0), (500, -400)])
        let c = K.trail("C", [(500, 0), (1000, 0)])
        let (s, _) = try PlanStepsTests.steps([a, b, c], [(0, 0), (500, 0), (500, -400)], shape: .oneWay)
        let r = try #require(PlanGuideRoute(steps: s))
        #expect(r.markers.map(\.action) == [.start, .right])
        #expect(abs(r.markers[1].startMeters - 500) < 3)
        #expect(abs(r.length - 900) < 5)
        let end = K.ll(500, -400)
        #expect(TurnCueGeometry.distance(r.points.last!, r.local(lat: end[0], lon: end[1])) < 2)
    }
}
