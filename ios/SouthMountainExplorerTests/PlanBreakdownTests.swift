import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike round 6: new miles in a never-visited area, and the distance
/// breakdown ("3.48 new + 0.87 repeat + 0.06 off-trail = 4.41 mi") that adds
/// up to the route total.
struct PlanBreakdownTests {
    typealias K = PlannerKit
    static let mi = 1609.344

    /// Distinct trail metres the walk covers, counted independently of RouteMetrics.
    static func uniqueTrailMeters(_ plan: RoutePlan, _ net: TrailNetwork) -> (unique: Double, again: Double) {
        var seen = Set<Int>()
        var unique = 0.0, again = 0.0
        for s in plan.steps {
            let e = net.edges[s.edgeId]
            guard e.kind == .trail else { continue }
            if seen.insert(e.id).inserted { unique += e.lengthMeters } else { again += e.lengthMeters }
        }
        return (unique, again)
    }

    static func hundredthsSum(_ m: RouteMetrics) -> (parts: Int, total: Int) {
        let b = PlanResultDisplay.breakdownParts(m, units: .imperial)
        return (b.parts.reduce(0) { $0 + $1.hundredths }, b.totalHundredths)
    }

    @Test func neverVisitedAreaCountsEveryFirstPassMileAsNew() throws {
        // A loop with a dead-end spur (walked out and back) and a start 50 m off-trail.
        let ring = K.trail("R", [(0, 0), (800, 0), (800, 800), (0, 800), (0, 0)])
        let spur = K.trail("S", [(800, 400), (1300, 400)])
        let v = try K.view([ring, spur], start: (-50, 0))
        let none = TrailCompletionLookup(completed: [])
        let outcome = RoutePlanningService.planOutcome(
            PlanOutcomeRequest(network: v, trails: K.inputs([ring, spur]), completion: none,
                               timeLimitSeconds: K.flatSeconds(8000)))
        guard case .planned(let best, _) = outcome else { Issue.record("no plan"); return }
        let m = best.metrics
        let (unique, again) = Self.uniqueTrailMeters(best, v.network)
        #expect(best.finished.count == 2)
        #expect(abs(m.newMeters - unique) < 1e-6, "new \(m.newMeters) unique \(unique)")
        #expect(again > 400, "the spur is walked twice")
        #expect(abs((m.trailMeters - m.newMeters) - again) < 1e-6)
        #expect(m.connectorMeters > 0)
        // The drawn new line is the same ground.
        let route = PlannedRouteBuilder.build(plan: best, edges: v.network.edges, completion: none)
        #expect(abs(route.newTrailMeters - m.newMeters) < 1e-6)
        // The breakdown adds up to the total, exactly in shown hundredths.
        let h = Self.hundredthsSum(m)
        #expect(h.parts == h.total)
        let shown = PlanResultDisplay.route(best, title: "x", units: .imperial)
        #expect(shown.breakdown.contains(" new + "))
        #expect(shown.breakdown.contains(" repeat"))
        #expect(shown.breakdown.contains(" off-trail = "))
    }

    @Test func breakdownRoundsSoThePartsAddUp() throws {
        let net = TrailNetwork(trails: K.inputs([K.trail("A", [(0, 0), (100, 0)])]))
        var m = try #require(RouteMetrics(network: net, steps: []))
        // Three parts of 0.336 mi: rounded one by one they show 1.02, the total is 1.01.
        m.newMeters = 0.336 * Self.mi
        m.trailMeters = 0.672 * Self.mi
        m.connectorMeters = 0.336 * Self.mi
        m.meters = m.trailMeters + m.connectorMeters
        let b = PlanResultDisplay.breakdown(m, units: .imperial)
        #expect(b.text == "0.34 new + 0.34 repeat + 0.33 off-trail = 1.01 mi")
        #expect(b.spoken == "Distance is 0.34 mi new, 0.34 mi repeat, 0.33 mi off-trail.")
        // Parts that round to nothing are left out; metric uses km.
        m.newMeters = 1000
        m.trailMeters = 1000
        m.connectorMeters = 2
        m.meters = 1002
        #expect(PlanResultDisplay.breakdown(m, units: .metric).text == "1.00 new = 1.00 km")
    }

    // MARK: - Østerådalen (Denmark), the case Quentin reported

    private struct GeomFile: Decodable {
        let id: String
        let trails: [Trail]
    }

    private static func geom(_ id: String) throws -> AreaTrailSet {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let url = repo.appendingPathComponent("public/areas/geom/\(id).json")
        let g = try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
        return AreaTrailSet(areaId: g.id, trails: g.trails)
    }

    @Test func osteradalenNeverVisitedFromTheBottomRight() throws {
        // Primary plus the two Danish neighbors inside a 3 h loop's reach, as the app loads them.
        let sets = try ["osteradalen-dk", "egholm-dk", "hammer-bakker-dk"].map(Self.geom)
        let merged = MultiAreaTrails.merge(areas: sets)
        let net = TrailNetwork(trails: merged.inputs)
        // A pin by the trail near the main road, bottom right of the area.
        let v = try TrailNetworkSnapper(network: net).insert(start: .pin(lat: 57.0118, lon: 9.9185))
        let none = TrailCompletionLookup(completed: [])
        let outcome = RoutePlanningService.planOutcome(
            PlanOutcomeRequest(network: v, trails: merged.inputs, completion: none, timeLimitSeconds: 3 * 3600))
        guard case .planned(let best, _) = outcome else { Issue.record("no plan"); return }
        let m = best.metrics

        // The area's listed total adds each trail's own length: 2.62 + 0.85 + 0.70.
        let area = sets[0].trails
        #expect(abs(area.reduce(0) { $0 + $1.distanceMi } - 4.17) < 1e-9)
        // Its distinct ground is less, because two trails share stretches of path.
        let areaEdges = v.network.edges.filter { $0.kind == .trail && $0.trailKeys.contains { $0.areaId == "osteradalen-dk" } }
        let areaUnique = areaEdges.reduce(0) { $0 + $1.lengthMeters }
        let perTrail = v.network.trailKeys.filter { $0.areaId == "osteradalen-dk" }
            .reduce(0.0) { sum, k in sum + v.network.edgeIds(of: k).reduce(0) { $0 + v.network.edges[$1].lengthMeters } }
        #expect(abs(perTrail / Self.mi - 4.16) < 0.01)
        #expect(abs(areaUnique / Self.mi - 3.48) < 0.01)
        #expect(abs((perTrail - areaUnique) / Self.mi - 0.68) < 0.01)

        // The plan finishes all three and every first-pass mile is new.
        #expect(Set(best.finished.map(\.key.trailId)) == ["ster-dalen-rundt", "fugleruten", "indkilderuten"])
        let (unique, again) = Self.uniqueTrailMeters(best, v.network)
        #expect(abs(m.newMeters - unique) < 1e-6)
        #expect(abs(m.newMeters - areaUnique) < 1, "new should be all of the area's distinct ground")
        #expect(!v.network.edges.contains { $0.kind == .bridge })
        #expect(abs(m.trailMeters - m.newMeters - again) < 1e-6)
        let route = PlannedRouteBuilder.build(plan: best, edges: v.network.edges, completion: none)
        #expect(abs(route.newTrailMeters - m.newMeters) < 1e-6)

        // The numbers he saw, now with where the 0.9 mi comes from.
        let shown = PlanResultDisplay.route(best, title: "Your hike", units: .imperial)
        #expect(shown.summary.contains(" · 4.4 mi · "), "\(shown.summary)")
        #expect(shown.summary.hasSuffix(" · 3.5 new mi"), "\(shown.summary)")
        #expect(shown.breakdown == "3.48 new + 0.87 repeat + 0.06 off-trail = 4.41 mi", "\(shown.breakdown)")
        let h = Self.hundredthsSum(m)
        #expect(h.parts == h.total)

        // Steps on real data: numbered in order, legs join, distances add up.
        let names = Dictionary(merged.inputs.map { ($0.key, $0.trail.name) }, uniquingKeysWith: { a, _ in a })
        let steps = PlanStepBuilder.steps(plan: best, network: v.network, names: names)
        #expect(steps.count >= 5)
        PlanStepsTests.checkInvariants(steps, best)
        #expect(steps[0].instruction == "Start off-trail")
        #expect(steps[1].instruction == "Right onto Østerådalen rundt")
    }
}
