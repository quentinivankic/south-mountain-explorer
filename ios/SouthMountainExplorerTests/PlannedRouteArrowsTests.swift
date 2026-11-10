import CoreGraphics
import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike round 4: walking-direction chevrons on the planned route.
struct PlannedRouteArrowsTests {
    typealias A = PlannedRouteArrows

    static func place(_ pts: [CGPoint], spacing: CGFloat = 70) -> [A.Chevron] {
        A.place(along: pts, spacing: spacing, laneGap: 14, laneOffset: 6)
    }

    static func same(_ a: [CGFloat], _ b: [CGFloat]) -> Bool {
        a.count == b.count && zip(a, b).allSatisfy { abs($0 - $1) < 1e-9 }
    }

    @Test func evenSpacingFromHalfASpacingIn() {
        let c = Self.place([CGPoint(x: 0, y: 0), CGPoint(x: 300, y: 0)])
        #expect(Self.same(c.map(\.point.x), [35, 105, 175, 245]))
        #expect(c.allSatisfy { $0.point.y == 0 && $0.angle == 0 })
    }

    @Test func spacingFollowsTheLineAcrossVertices() {
        // An L: 50 east then 100 south (y down). Arc positions 35, 105.
        let c = Self.place([CGPoint(x: 0, y: 0), CGPoint(x: 50, y: 0), CGPoint(x: 50, y: 100)])
        #expect(c.count == 2)
        #expect(abs(c[0].point.x - 35) < 1e-9 && c[0].point.y == 0)
        #expect(abs(c[1].point.x - 50) < 1e-9 && abs(c[1].point.y - 55) < 1e-9)
        #expect(abs(c[1].angle - .pi / 2) < 1e-9)
    }

    @Test func spacingIsInScreenUnitsSoLowZoomDoesNotCrowd() {
        // The same route on screen at half the zoom is half as long: half the chevrons.
        let near = Self.place([CGPoint(x: 0, y: 0), CGPoint(x: 1400, y: 0)])
        let far = Self.place([CGPoint(x: 0, y: 0), CGPoint(x: 700, y: 0)])
        #expect(near.count == 20)
        #expect(far.count == 10)
        // Too short for one gap: no chevron.
        #expect(Self.place([CGPoint(x: 0, y: 0), CGPoint(x: 30, y: 0)]).isEmpty)
        #expect(A.place(along: [CGPoint(x: 0, y: 0)], spacing: 70, laneGap: 14, laneOffset: 6).isEmpty)
    }

    @Test func loopPointsRoundInWalkingOrder() {
        // Clockwise square on screen (y down): east, south, west, north.
        let sq = [CGPoint(x: 0, y: 0), CGPoint(x: 140, y: 0), CGPoint(x: 140, y: 140),
                  CGPoint(x: 0, y: 140), CGPoint(x: 0, y: 0)]
        let c = Self.place(sq)
        #expect(c.count == 8)
        let headings = c.map { Int(($0.angle * 180 / .pi).rounded()) }
        #expect(headings == [0, 0, 90, 90, 180, 180, -90, -90])
        // Walked the other way, every chevron flips.
        let back = Self.place(sq.reversed())
        for (x, y) in zip(c, back.reversed()) { #expect(abs(cos(x.angle - y.angle) + 1) < 1e-9) }
    }

    @Test func outAndBackSplitsBothPassesOntoTwoLanes() {
        // Out 105 east and straight back: positions 35 and 105 out, then 175 (x 35) back.
        let c = Self.place([CGPoint(x: 0, y: 0), CGPoint(x: 105, y: 0), CGPoint(x: 0, y: 0)])
        #expect(c.count == 3)
        // The outbound chevron at x 35 and the return one at x 35 face opposite ways
        // and each moves to its own right: outbound down (+y), return up (-y).
        let out35 = c[0], back35 = c[2]
        #expect(abs(out35.point.x - 35) < 1e-9 && abs(out35.point.y - 6) < 1e-9)
        #expect(abs(back35.point.x - 35) < 1e-9 && abs(back35.point.y + 6) < 1e-9)
        #expect(cos(out35.angle - back35.angle) < 0)
        // The turnaround chevron at x 105 has no opposite partner close by: stays on the line.
        #expect(abs(c[1].point.x - 105) < 1e-9 && c[1].point.y == 0)
    }

    @Test func oneWayPlanPointsFromStartToEnd() {
        let c = Self.place([CGPoint(x: 0, y: 200), CGPoint(x: 0, y: 0)])
        #expect(!c.isEmpty)
        #expect(c.allSatisfy { abs($0.angle + .pi / 2) < 1e-9 })
        #expect(Self.same(c.map(\.point.y), [165, 95, 25]))
    }

    @Test func sameDirectionNeighboursAreNotShifted() {
        let c = A.separatingOppositePasses(
            [A.Chevron(point: .zero, angle: 0), A.Chevron(point: CGPoint(x: 5, y: 0), angle: 0.1)],
            laneGap: 14, laneOffset: 6)
        #expect(c[0].point == .zero && c[1].point == CGPoint(x: 5, y: 0))
    }
}
