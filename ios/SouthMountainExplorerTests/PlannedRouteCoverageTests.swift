import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike round 5: the drawn route is split into new and done runs by the
/// same coverage rule as the plan's new miles.
struct PlannedRouteCoverageTests {
    typealias K = PlannerKit
    typealias W = WalkedGroundTests

    private func planned(_ trails: [Trail], completed: Set<TrailNetwork.TrailKey> = [], walked: [[GpsPoint]],
                         limit: Double) throws -> (plan: RoutePlan, route: PlannedRoute, view: TrailNetworkWithEndpoints) {
        let v = try K.view(trails)
        let lookup = TrailCompletionLookup(completed: completed, walked: WalkedGround(paths: walked))
        let outcome = RoutePlanningService.planOutcome(
            PlanOutcomeRequest(network: v, trails: K.inputs(trails), completion: lookup, timeLimitSeconds: limit))
        let plan: RoutePlan
        switch outcome {
        case .planned(let best, _): plan = best
        case .nothingFits(let n): plan = try #require(n.fallback)
        case .noCandidates: throw CancellationError()
        }
        return (plan, PlannedRouteBuilder.build(plan: plan, edges: v.network.edges, completion: lookup), v)
    }

    private func repeatMeters(_ p: RoutePlan) -> Double { p.metrics.trailMeters - p.metrics.newMeters }

    @Test func runsSumToNewAndRepeatMiles() throws {
        // A is walked for its first 400 m; out and back over it.
        let a = K.trail("A", [(0, 0), (1000, 0)])
        let o = try planned([a], walked: [W.track(0, 400)], limit: K.flatSeconds(2600))
        #expect(o.route.mixesNewAndDone)
        #expect(o.plan.metrics.newMeters > 500 && o.plan.metrics.newMeters < 620, "new \(o.plan.metrics.newMeters)")
        #expect(abs(o.route.newTrailMeters - o.plan.metrics.newMeters) < 1e-6)
        #expect(abs(o.route.doneTrailMeters - repeatMeters(o.plan)) < 1e-6)
        // Walking order: done (walked part), new (the rest, out), done (the whole way back).
        let trail = o.route.segments.filter { $0.kind == .trail }
        #expect(trail.map(\.isNew) == [false, true, false])
        // The new run starts about where the track ended (400 m plus the 10 m buffer).
        let newStart = try #require(trail[1].coords.first)
        let origin = K.ll(0, 0)
        let startX = Area.meters(newStart.lat, newStart.lon, origin[0], origin[1])
        #expect(abs(startX - 410) < 15, "new run starts at \(startX) m")
        // Pieces join without gaps or duplicate points.
        let all = o.route.allCoords
        for (p, q) in zip(all, all.dropFirst()) { #expect(Area.meters(p.lat, p.lon, q.lat, q.lon) < 25) }
    }

    @Test func completedTrailIsDoneAndNewTrailIsNew() throws {
        // B (west) is marked complete; A (east) is untouched. Time for both.
        let a = K.trail("A", [(0, 0), (600, 0)])
        let b = K.trail("B", [(0, 0), (-600, 0)])
        let o = try planned([a, b], completed: [K.key("B")], walked: [], limit: K.flatSeconds(5000))
        #expect(abs(o.route.newTrailMeters - o.plan.metrics.newMeters) < 1e-6)
        #expect(abs(o.route.doneTrailMeters - repeatMeters(o.plan)) < 1e-6)
        #expect(o.plan.metrics.newMeters > 0)
        // Every new run lies on A (east of the start).
        for s in o.route.segments where s.kind == .trail && s.isNew {
            for c in s.coords { #expect(c.lon >= K.ll(0, 0)[1] - 1e-9) }
        }
    }

    @Test func withoutCompletionEveryTrailStepIsNew() throws {
        let a = K.trail("A", [(0, 0), (1000, 0)])
        let o = try planned([a], walked: [W.track(0, 400)], limit: K.flatSeconds(2600))
        let plain = PlannedRouteBuilder.build(plan: o.plan, edges: o.view.network.edges)
        let trailRuns = plain.segments.filter { $0.kind == .trail }
        #expect(!trailRuns.isEmpty && trailRuns.allSatisfy { $0.isNew })
        #expect(!plain.mixesNewAndDone)
    }

    @Test func unwalkedRunsMatchTheFraction() {
        let line = W.coords([(0, 0), (1000, 0)])
        let g = WalkedGround(paths: [W.track(0, 300) + W.track(600, 1000)])
        let r = g.unwalkedRuns(of: line)
        #expect(r.runs.count == 1)
        let run = r.runs[0]
        #expect(abs(run.from - 310) < 10 && abs(run.to - 590) < 10, "run \(run)")
        #expect(WalkedGround.fraction(r) == g.unwalkedFraction(of: line))
        #expect(WalkedGround.none.unwalkedRuns(of: line).totalMeters == nil)
    }
}
