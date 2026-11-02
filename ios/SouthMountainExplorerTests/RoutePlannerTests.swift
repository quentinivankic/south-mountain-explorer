import Foundation
import Testing
@testable import SouthMountainExplorer

/// Route planner behaviour on hand-built scenes ("Plan a Hike", Task 5).
struct RoutePlannerTests {
    typealias K = PlannerKit

    private func close(_ a: Double, _ b: Double, _ tol: Double = 3) -> Bool { abs(a - b) <= tol }

    // MARK: - Basics

    @Test func singleTrailLoopIsOutAndBack() throws {
        let t = [K.trail("A", [(0, 0), (500, 0)])]
        let o = try K.run(t, limit: 800)
        #expect(K.validate(o) == [])
        #expect(K.ids(o.plan) == ["A"])
        #expect(close(try #require(o.plan).metrics.seconds, K.flatSeconds(1000)))
        // Not enough time: no plan at all.
        #expect(try K.run(t, limit: 600).plan == nil)
    }

    @Test func noCandidatesGivesNoPlan() throws {
        let t = [K.trail("A", [(0, 0), (500, 0)])]
        #expect(try K.run(t, completed: ["A"], limit: 3600).plan == nil)
    }

    @Test func prefersMoreTrailsOverLongerOnes() throws {
        // Spurs of 300, 400 and 900 m off the start. Out and back: 600 + 800
        // = 1400 m for two trails, 1800 m for the long one alone.
        let t = [
            K.trail("S300", [(0, 0), (300, 0)]),
            K.trail("S400", [(0, 0), (0, 400)]),
            K.trail("S900", [(0, 0), (-900, 0)]),
        ]
        let o = try K.run(t, limit: 1300)
        #expect(K.validate(o) == [])
        #expect(Set(K.ids(o.plan)) == ["S300", "S400"])
        // With room for all of it, all three.
        let all = try K.run(t, limit: K.flatSeconds(3200) + 5)
        #expect(Set(K.ids(all.plan)) == ["S300", "S400", "S900"])
        #expect(K.validate(all) == [])
    }

    // MARK: - Walk through completed ground

    @Test func walksThroughCompletedTrailsAsConnectors() throws {
        // A completed 900 m access trail from the start, then two uncompleted
        // trails at its far end.
        let t = [
            K.trail("C", [(0, 0), (900, 0)]),
            K.trail("U1", [(900, 0), (900, 300)]),
            K.trail("U2", [(900, 0), (1200, 0)]),
        ]
        let limit = K.flatSeconds(2 * 900 + 2 * 300 + 2 * 300) + 20
        let o = try K.run(t, completed: ["C"], limit: limit)
        #expect(K.validate(o) == [])
        let plan = try #require(o.plan)
        #expect(Set(K.ids(plan)) == ["U1", "U2"])
        #expect(!plan.finishedTrailKeys.contains(K.key("C")))
        // The completed trail is walked twice, never credited as new ground.
        let cEdges = o.net.edgeIds(of: K.key("C"))
        #expect(plan.steps.filter { cEdges.contains($0.edgeId) }.count == 2 * cEdges.count)
        #expect(close(plan.metrics.newMeters, 600))
        // A budget that cannot pay for the access walk finds nothing.
        #expect(try K.run(t, completed: ["C"], limit: K.flatSeconds(1800)).plan == nil)
    }

    @Test func walksAroundACompletedRingToReachTrailsBeyondIt() throws {
        // A completed 600 m square ring around the start corner, with two
        // uncompleted trails at the far corner. Half the ring each way.
        let t = [
            K.trail("Ring", [(0, 0), (600, 0), (600, 600), (0, 600), (0, 0)]),
            K.trail("U1", [(600, 600), (900, 600)]),
            K.trail("U2", [(600, 600), (600, 900)]),
        ]
        let o = try K.run(t, completed: ["Ring"], limit: K.flatSeconds(3600) + 20)
        #expect(K.validate(o) == [])
        let plan = try #require(o.plan)
        #expect(Set(K.ids(plan)) == ["U1", "U2"])
        #expect(close(plan.metrics.seconds, K.flatSeconds(3600), 5))
        #expect(close(plan.metrics.newMeters, 600))
        // Only 30 min of the budget is spent getting there and back.
        let ringSteps = zip(plan.steps, plan.stepInfo).filter { $0.1.trailKey == K.key("Ring") }
        #expect(close(ringSteps.reduce(0.0) { $0 + o.net.edges[$1.0.edgeId].lengthMeters }, 2400))
    }

    @Test func spurOffTheMiddleOfATrailIsWalkedInPlace() throws {
        // One-way along A (0,0) to (1000,0), with spur B at the halfway point.
        // Walked in place: 1000 + 2 * 300 = 1600 m. Treating A and B as
        // separate stops would retrace half of A.
        let t = [K.trail("A", [(0, 0), (1000, 0)]), K.trail("B", [(500, 0), (500, 300)])]
        let o = try K.run(t, end: (1000, 0), limit: K.flatSeconds(1600) + 10, shape: .oneWay)
        #expect(K.validate(o) == [])
        #expect(Set(K.ids(o.plan)) == ["A", "B"])
        #expect(close(try #require(o.plan).metrics.seconds, K.flatSeconds(1600), 5))
    }

    @Test func eligibilityEstimateIsAValidLowerBoundOnSteepGround() throws {
        // The way out climbs 150 ft, the way back is the same hill downhill.
        // The estimate must cost the return leg as walking back, not out.
        let t = [
            K.trail("S", [(0, 0), (400, 0)], profile: K.ramp(150)),
            K.trail("U", [(400, 0), (400, 200)], profile: K.flat()),
        ]
        let o = try K.run(t, completed: ["S"], limit: 3600)
        let c = try #require(o.eligibility.candidates.first { $0.key == K.key("U") })
        let costs = RouteCosts(network: o.net, paceFactor: 1)
        let out = try #require(o.net.shortestPaths(from: o.view.start.nodeId, costs: costs)).seconds[c.entryNodeId]
        let back = try #require(o.net.shortestPaths(from: c.entryNodeId, costs: costs)).seconds[o.view.start.nodeId]
        #expect(back < out - 30)
        let cover = c.edgeIds.reduce(0.0) {
            $0 + min(costs.walk($1, .forward).seconds, costs.walk($1, .backward).seconds)
        }
        #expect(abs(c.estimatedSeconds - (out + cover + back)) < 1e-6)
        let plan = try #require(o.plan)
        #expect(plan.metrics.seconds >= c.estimatedSeconds - 1e-6)
    }

    // MARK: - Gain limit

    @Test func maxGainPicksAFlatterSet() throws {
        // A is short and climbs 100 ft going out. B and C are flat. All three
        // fit the time; with a 60 ft cap A is out.
        let t = [
            K.trail("A", [(0, 0), (200, 0)], profile: K.ramp(100)),
            K.trail("B", [(0, 0), (0, 400)], profile: K.flat()),
            K.trail("C", [(0, 0), (0, -400)], profile: K.flat()),
        ]
        let free = try K.run(t, limit: 1700)
        #expect(Set(K.ids(free.plan)) == ["A", "B", "C"])
        #expect(try #require(free.plan).metrics.gainFt > 90)
        let capped = try K.run(t, limit: 1700, maxGain: 60)
        #expect(K.validate(capped) == [])
        #expect(Set(K.ids(capped.plan)) == ["B", "C"])
        #expect(try #require(capped.plan).metrics.gainFt <= 60)
    }

    @Test func gainLimitTooTightGivesNoPlan() throws {
        let t = [K.trail("A", [(0, 0), (300, 0)], profile: K.ramp(150))]
        #expect(try K.run(t, limit: 3600).plan != nil)
        #expect(try K.run(t, limit: 3600, maxGain: 20).plan == nil)
    }

    // MARK: - Loop versus one-way

    @Test func oneWayEndsAtTheEndAndFinishesAtLeastALoop() throws {
        let t = [
            K.trail("A", [(0, 0), (1000, 0)]),
            K.trail("B", [(1000, 0), (2000, 0)]),
            K.trail("D", [(500, 0), (500, -300)]),
        ]
        let limit = K.flatSeconds(2000) + 30
        let loop = try K.run(t, limit: limit, shape: .loop)
        let one = try K.run(t, end: (2000, 0), limit: limit, shape: .oneWay)
        #expect(K.validate(loop) == [])
        #expect(K.validate(one) == [])
        let plan = try #require(one.plan)
        #expect(plan.endNodeId == one.view.end?.nodeId)
        #expect(Set(K.ids(plan)) == ["A", "B"])
        #expect((loop.plan?.finished.count ?? 0) < plan.finished.count)
        #expect(plan.finished.count >= (loop.plan?.finished.count ?? 0))
        // A loop returns to the start.
        #expect(loop.plan?.endNodeId == loop.view.start.nodeId)
    }

    @Test func oneWayWithoutAnEndEndsWherever() throws {
        let t = [K.trail("A", [(0, 0), (1000, 0)])]
        let o = try K.run(t, limit: K.flatSeconds(1000) + 5, shape: .oneWay)
        #expect(K.validate(o) == [])
        #expect(close(try #require(o.plan).metrics.seconds, K.flatSeconds(1000)))
    }

    @Test func oneWayWalkCanFinishATrailOnTheWayToTheEnd() throws {
        // The direct walk to the end already covers A: credited with no jobs.
        let t = [K.trail("A", [(0, 0), (800, 0)])]
        let o = try K.run(t, end: (800, 0), limit: K.flatSeconds(800) + 5, shape: .oneWay)
        #expect(K.validate(o) == [])
        #expect(K.ids(o.plan) == ["A"])
    }

    // MARK: - Trail shapes

    @Test func branchedTrailIsCoveredFully() throws {
        // A T: stem 400 m east, a 300 m branch north and a 400 m branch east,
        // all one trail. Cover = 400 + 2*300 + 2*400 + 400 back = 2200 m.
        let t = [K.trail("T", [[(0, 0), (400, 0)], [(400, 0), (400, 300)], [(400, 0), (800, 0)]])]
        let o = try K.run(t, limit: K.flatSeconds(2200) + 10)
        #expect(K.validate(o) == [])
        #expect(K.ids(o.plan) == ["T"])
        #expect(close(try #require(o.plan).metrics.seconds, K.flatSeconds(2200), 5))
        #expect(try K.run(t, limit: K.flatSeconds(2200) - 30).plan == nil)
    }

    @Test func loopTrailIsLappedOnce() throws {
        // 500 m access, then a 400 x 400 ring: out 500, lap 1600, back 500.
        let t = [
            K.trail("Access", [(0, 0), (500, 0)]),
            K.trail("Ring", [(500, 0), (500, 400), (900, 400), (900, 0), (500, 0)]),
        ]
        let o = try K.run(t, completed: ["Access"], limit: K.flatSeconds(2600) + 10)
        #expect(K.validate(o) == [])
        #expect(K.ids(o.plan) == ["Ring"])
        #expect(close(try #require(o.plan).metrics.seconds, K.flatSeconds(2600), 5))
    }

    @Test func trailWithPiecesJoinedThroughAnotherIsCovered() throws {
        // Q has two pieces 200 m apart, joined only by K.
        let t = [
            K.trail("K", [(0, 0), (400, 0)]),
            K.trail("Q", [[(100, 0), (100, 80)], [(300, 0), (300, 80)]]),
        ]
        let o = try K.run(t, completed: ["K"], limit: 3600)
        #expect(K.validate(o) == [])
        #expect(K.ids(o.plan) == ["Q"])
    }

    @Test func startingOnATrailMidwayUsesThatPoint() throws {
        // Start 400 m along a 1000 m trail. Either order of visiting the two
        // ends costs 400 + 1000 + 600 = 2000 m.
        let t = [K.trail("A", [(0, 0), (1000, 0)])]
        let o = try K.run(t, start: (400, 0), limit: K.flatSeconds(2000) + 10)
        #expect(K.validate(o) == [])
        #expect(K.ids(o.plan) == ["A"])
        #expect(close(try #require(o.plan).metrics.seconds, K.flatSeconds(2000), 5))
        #expect(try K.run(t, start: (400, 0), limit: K.flatSeconds(2000) - 30).plan == nil)
    }

    // MARK: - Incidental finishes

    @Test func incidentalFinishIsCredited() throws {
        // Reaching L means walking all of A and B on the way out and back.
        let t = [
            K.trail("A", [(0, 0), (600, 0)]),
            K.trail("B", [(600, 0), (900, 0)]),
            K.trail("L", [(900, 0), (900, 500)]),
        ]
        let o = try K.run(t, limit: K.flatSeconds(2800) + 10)
        #expect(K.validate(o) == [])
        #expect(K.ids(o.plan) == ["A", "B", "L"])
        let plan = try #require(o.plan)
        #expect(close(plan.metrics.seconds, K.flatSeconds(2800), 5))
        #expect(plan.finished.map(\.completedAtStep) == plan.finished.map(\.completedAtStep).sorted())
    }

    // MARK: - Bridges and connectors are flagged

    @Test func bridgeIsWalkedAndFlagged() throws {
        // A 20 m gap between A and B is bridged (Task 1) and is unverified.
        let t = [K.trail("A", [(0, 0), (300, 0)]), K.trail("B", [(320, 0), (700, 0)])]
        let o = try K.run(t, limit: 3600)
        #expect(K.validate(o) == [])
        let plan = try #require(o.plan)
        #expect(Set(K.ids(plan)) == ["A", "B"])
        #expect(close(plan.metrics.bridgeMeters, 40, 1))
        let flagged = zip(plan.steps, plan.stepInfo).filter { $0.1.isFlagged }
        #expect(flagged.count == 2)
        #expect(flagged.allSatisfy { $0.1.kind == .bridge && $0.1.trailKey == nil })
        #expect(plan.flaggedMeters > 39)
    }

    @Test func offTrailConnectorIsWalkedAndFlagged() throws {
        let t = [K.trail("A", [(0, 0), (500, 0)])]
        let o = try K.run(t, start: (0, -50), limit: 3600)
        #expect(K.validate(o) == [])
        let plan = try #require(o.plan)
        #expect(close(plan.metrics.connectorMeters, 100, 1))
        #expect(plan.stepInfo.filter { $0.kind == .connector }.count == 2)
        #expect(plan.stepInfo.filter { $0.kind == .connector }.allSatisfy { $0.isFlagged && $0.trailKey == nil })
    }

    // MARK: - Pace

    @Test func fasterPaceIsFasterAndNeverFinishesLess() throws {
        let t = [K.trail("A", [(0, 0), (500, 0)]), K.trail("B", [(0, 0), (0, 500)])]
        let slow = try K.run(t, limit: 2000, pace: 0.8)
        let fast = try K.run(t, limit: 2000, pace: 1.25)
        let s = try #require(slow.plan), f = try #require(fast.plan)
        #expect(f.finished.count >= s.finished.count)
        #expect(close(f.metrics.seconds, K.flatSeconds(2000, pace: 1.25), 5))
        #expect(K.validate(fast) == [])
    }

    // MARK: - Step info

    @Test func stepsNameTheirTrailAndMarkGoalCover() throws {
        let t = [K.trail("C", [(0, 0), (500, 0)]), K.trail("U", [(500, 0), (500, 300)])]
        let o = try K.run(t, completed: ["C"], limit: 3600)
        let plan = try #require(o.plan)
        let cEdges = Set(o.net.edgeIds(of: K.key("C")))
        for (s, info) in zip(plan.steps, plan.stepInfo) {
            if cEdges.contains(s.edgeId) {
                #expect(info.trailKey == K.key("C") && !info.isGoalCover)
            } else {
                #expect(info.trailKey == K.key("U") && info.isGoalCover)
            }
        }
    }

    // MARK: - Determinism

    @Test func sameInputGivesTheSamePlanAndOrderDoesNotMatter() throws {
        let t = [
            K.trail("A", [(0, 0), (500, 0)]), K.trail("B", [(500, 0), (500, 400)]),
            K.trail("C", [(0, 0), (0, -400)]), K.trail("D", [(0, -400), (300, -400)]),
            K.trail("E", [(500, 400), (200, 400)]),
        ]
        let a = try K.run(t, limit: 3000)
        let b = try K.run(t, limit: 3000)
        let c = try K.run(t, limit: 3000, reverseCandidates: true)
        let pa = try #require(a.plan), pb = try #require(b.plan), pc = try #require(c.plan)
        #expect(pa.steps == pb.steps && pa.finishedTrailKeys == pb.finishedTrailKeys)
        #expect(pa.steps == pc.steps && pa.finishedTrailKeys == pc.finishedTrailKeys)
        #expect(K.validate(a) == [])
    }
}
