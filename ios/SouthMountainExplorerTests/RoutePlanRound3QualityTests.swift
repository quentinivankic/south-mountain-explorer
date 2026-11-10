import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike v2, round 3: Quentin's real South Mountain progress.
///
/// The completion state below is derived from his hike history the way the
/// app does it (completed trail ids per area, then ProgressService). It holds
/// trail ids and completion flags only: no GPS points, no timestamps. The
/// starts are dropped pins on a fixed 200 m lattice over the park, not points
/// from his tracks.
///
/// Before round 3 (982cb6d2c) only the single best whole-trail plan got the
/// detour search, so a plan that ranked lower before detours, but finished a
/// second trail and turned onto unfinished trail instead of walking back over
/// old ground, was never considered. Numbers in the comments come from the
/// standalone harness in .agents/tasks/plan-a-hike-v2/round3/harness.
struct RoutePlanRound3QualityTests {
    typealias Key = TrailNetwork.TrailKey

    /// South Mountain trails his history marks completed (46 of 77).
    static let completedIds: Set<String> = [
        "35th-ave-access-trail", "beacon-hill-trail", "beverly-canyon-trail", "beverly-pima-connector-trail",
        "bursera-canyon", "cholla-flats-loop", "crosscut-trail", "dc-ray-connector", "de-las-lomas-access-trail",
        "degoba-loop", "dobbins-lookout-connector", "gpt-to-dc-connector", "hau-pal-loop-trail", "helipad",
        "hidden-valley-connector-trail", "hidden-valley-trail", "holbert-trail", "javelina-to-national-connector",
        "javelina-to-ridgeline", "judith-tunnell-accessible-trail", "judith-tunnell-challenge-trail", "kiwanis-trail",
        "las-lomitas-trail", "lost-ranch-to-cholla", "ma-ha-tuak-connector", "ma-ha-tuak-perimeter-trail",
        "ma-ha-tuak-trail", "max-delta-trail", "midlife-crisis", "mormon-loop-trail", "pcl-to-beacon",
        "pima-canyon-loop-trail", "pima-east-loop", "pima-wash-trail", "pima-west-loop-trail", "prospector-loop",
        "prospector-to-national", "pyramid-trail", "ranger-trail", "ridgeline-connector", "ridgeline-trail",
        "shaughnessey-connector", "telegraph-pass-trail", "telegraph-pass-trailhead-paved", "upper-gila-trail",
        "young-man-trail",
    ]

    private struct GeomFile: Decodable {
        let id: String
        let trails: [Trail]
    }

    private struct Case {
        let plan: RoutePlan
        let view: TrailNetworkWithEndpoints
        let done: Set<Key>
        let limit: Double
    }

    private static func plan(lat: Double, lon: Double, minutes: Double) throws -> Case {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        let geom = try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        let done = Set(completedIds.map { Key(areaId: geom.id, trailId: $0) })
        #expect(done.count == 46)
        #expect(done.allSatisfy { k in geom.trails.contains { $0.id == k.trailId } })
        let view = try TrailNetworkSnapper(network: TrailNetwork(trails: inputs)).insert(
            start: .pin(lat: lat, lon: lon), end: nil)
        let req = PlanOutcomeRequest(network: view, trails: inputs, completion: TrailCompletionLookup(completed: done),
                                     timeLimitSeconds: minutes * 60)
        guard case .planned(let best, _) = RoutePlanningService.planOutcome(req) else {
            throw PlanError.notPlanned
        }
        return Case(plan: best, view: view, done: done, limit: minutes * 60)
    }

    private enum PlanError: Error { case notPlanned }

    /// New and repeat miles walked on one trail (first walk of an unfinished
    /// edge is new; everything else on that trail is repeat).
    private static func miles(_ c: Case, on trailId: String) -> (new: Double, repeat: Double) {
        let net = c.view.network
        var seen = Set<Int>()
        var new = 0.0, rep = 0.0
        for s in c.plan.steps {
            let e = net.edges[s.edgeId]
            guard e.kind == .trail, e.trailKeys.contains(where: { $0.trailId == trailId }) else { continue }
            let isNew = e.trailKeys.contains { !c.done.contains($0) }
            if isNew && seen.insert(e.id).inserted { new += e.lengthMeters } else { rep += e.lengthMeters }
        }
        return (new / 1609.344, rep / 1609.344)
    }

    private static func checkLimits(_ c: Case) {
        #expect(c.plan.metrics.isContinuous)
        #expect(c.plan.metrics.seconds <= c.limit + 1e-3)
        #expect(c.plan.endNodeId == c.view.start.nodeId)
    }

    /// Dropped pin 250 m from his 33.3664, -112.0309 pin, 2 h 45 m loop.
    /// Before: 7.82 mi, 3.89 new, 1 trail (Javelina Canyon). It walked Mormon
    /// Trail out, Javelina Canyon Trail out and 2.94 mi back over it, then
    /// Mormon Trail back. Exhaustive oracle and now: Devestator Trail and
    /// Mormon Trail finished, 4.63 new mi in 6.87 mi. Where the old plan turned
    /// round on Javelina Canyon it now goes onto unfinished National Trail.
    @Test func realStateNearHisPinFinishesTheSecondTrailAndTurnsOntoNewGround() throws {
        let c = try Self.plan(lat: 33.36429, lon: -112.03, minutes: 165)
        let keys = Set(c.plan.finishedTrailKeys.map(\.trailId))
        #expect(keys.isSuperset(of: ["mormon-trail", "devestator-trail"]), "finished \(keys)")
        #expect(c.plan.newMiles >= 4.5, "new \(c.plan.newMiles)")
        #expect(c.plan.score >= 4.74 - 0.05, "score \(c.plan.score) vs oracle 4.74")
        #expect(Self.miles(c, on: "javelina-canyon-trail").repeat <= 0.5)
        #expect(Self.miles(c, on: "national-trail").new >= 1.0)
        Self.checkLimits(c)
    }

    /// Dropped pin on the west side, 2 h 45 m loop.
    /// Before: 7.73 mi, 3.85 new, 1 trail (West Alta), then 2.69 mi back over
    /// Alta Trail. Now: Alta-Bajada Connector and Alta to Hau'pal Connector
    /// finished, 4.57 new mi in 7.63 mi (oracle: the same trails and new miles
    /// in 7.20 mi, score 4.52), turning onto Bajada Trail at the junction
    /// instead of walking Alta Trail back.
    @Test func realStateTurnsOntoBajadaInsteadOfWalkingAltaBack() throws {
        let c = try Self.plan(lat: 33.33555, lon: -112.11598, minutes: 165)
        let keys = Set(c.plan.finishedTrailKeys.map(\.trailId))
        #expect(keys.isSuperset(of: ["alta-bajada-connector", "alta-to-hau-pal-connector"]), "finished \(keys)")
        #expect(c.plan.newMiles >= 4.4, "new \(c.plan.newMiles)")
        #expect(c.plan.score >= 4.35 - 0.05, "score \(c.plan.score) vs oracle 4.35")
        #expect(Self.miles(c, on: "bajada-trail").new >= 1.5)
        #expect(Self.miles(c, on: "alta-trail").repeat <= 1.0)
        Self.checkLimits(c)
    }

    /// The split rule moved from 0.25 to 0.5 mi. From this pin the plan walks
    /// every piece of Bajada Trail (pieces 668 m apart) and now gets credit for
    /// finishing it: 8.51 mi, 4.85 new, Alta-Bajada Connector and Bajada Trail.
    @Test func bajadaTrailIsCreditedWhenWalkedInFull() throws {
        let c = try Self.plan(lat: 33.34273, lon: -112.09019, minutes: 165)
        let keys = Set(c.plan.finishedTrailKeys.map(\.trailId))
        #expect(keys.contains("bajada-trail"), "finished \(keys)")
        #expect(c.plan.finished.count >= 2)
        Self.checkLimits(c)
    }

    /// The first whole-trail plan handed to the detour search is still the
    /// planner's own best, so `RoutePlanner.plan` and `planTop` agree.
    @Test func planTopStartsWithThePlannersBest() throws {
        let c = try Self.plan(lat: 33.36429, lon: -112.03, minutes: 165)
        let inputs = c.view.network.trailKeys
        let completion = TrailCompletionLookup(completed: c.done)
        let elig = TrailEligibility.evaluate(
            network: c.view, trails: [], completion: completion, options: .init(budgetSeconds: c.limit))
        #expect(!inputs.isEmpty)
        let req = RoutePlanRequest(network: c.view, candidates: elig.candidates, completion: completion,
                                   timeLimitSeconds: c.limit)
        let top = RoutePlanner.planTop(req, count: RoutePlanningService.polishedWholePlans)
        let one = try #require(RoutePlanner.plan(req))
        #expect(top.first?.steps == one.steps)
        #expect(top.count > 1)
        #expect(top.count <= RoutePlanningService.polishedWholePlans)
        let sets = top.map { Set($0.finishedTrailKeys) }
        #expect(Set(sets).count == sets.count, "each plan finishes a different set of trails")
    }
}
