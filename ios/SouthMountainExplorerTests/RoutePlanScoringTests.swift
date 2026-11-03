import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike v2 scoring: routes are ranked by new miles, not by how many
/// whole trails they tick off. Brute-force agreement lives in
/// `RoutePlannerBruteForceTests`.
struct RoutePlanScoringTests {
    typealias K = PlannerKit

    // MARK: - Formula

    @Test func scoreIsNewMilesPlusTrailBonusMinusRepeatCost() {
        let mi = 1609.344
        // 1 new mi in 2 mi, one trail: 1 + 0.5 - 0.4 * 1.
        #expect(abs(RouteScore.value(newMeters: mi, totalMeters: 2 * mi, finished: 1) - 1.1) < 1e-9)
        // All new, no trail finished.
        #expect(abs(RouteScore.value(newMeters: 2 * mi, totalMeters: 2 * mi, finished: 0) - 2.0) < 1e-9)
        // Quentin's first walk: 0.98 new mi in 7.36 mi, one trail. Too much repeat ground
        // for the little it adds, so the score is negative.
        let v = RouteScore.value(newMeters: 0.98 * mi, totalMeters: 7.36 * mi, finished: 1)
        #expect(abs(v - (0.98 + 0.5 - 0.4 * 6.38)) < 1e-9)
        #expect(v < 0)
    }

    @Test func efficiencyAndLowValueThreshold() throws {
        let mi = 1609.344
        #expect(RouteScore.efficiency(newMeters: 0, totalMeters: 0) == 0)
        #expect(abs(RouteScore.efficiency(newMeters: 1 * mi, totalMeters: 4 * mi) - 0.25) < 1e-9)
        #expect(RouteScore.efficiency(newMeters: 5 * mi, totalMeters: 4 * mi) == 1)
        let t = [K.trail("A", [(0, 0), (500, 0)])]
        let o = try K.run(t, start: (0, 0), limit: 3600)
        let p = try #require(o.plan)
        // Out and back: half the walk is new.
        #expect(abs(p.efficiency - 0.5) < 1e-6)
        #expect(!p.isLowValue)
    }

    @Test func tiesGoToHigherEfficiencyThenLessTime() {
        let t = RouteScore.tolerance / 2
        #expect(RouteScore.isBetter(score: 1 + t, efficiency: 0.6, seconds: 900, than: 1, efficiency: 0.5, seconds: 600))
        #expect(!RouteScore.isBetter(score: 1, efficiency: 0.5, seconds: 600, than: 1 + t, efficiency: 0.6, seconds: 900))
        #expect(RouteScore.isBetter(score: 1, efficiency: 0.5, seconds: 600, than: 1, efficiency: 0.5, seconds: 900))
        #expect(RouteScore.isBetter(score: 2, efficiency: 0.1, seconds: 9000, than: 1, efficiency: 0.9, seconds: 60))
    }

    // MARK: - A short new trail beats a long walk to a tiny one

    /// D is a finished 3 km trail. N (500 m) hangs off its far end; M (800 m)
    /// is right at the start. Both fit in 2 h, so the old objective (most
    /// whole trails) walked 6 km of finished trail to add N. Now M alone wins.
    @Test func doesNotWalkFarOverDoneTrailsForOneTinyTrail() throws {
        let trails = [
            K.trail("D", [(0, 0), (3000, 0)]),
            K.trail("N", [(3000, 0), (3500, 0)]),
            K.trail("M", [(0, 0), (0, 800)]),
        ]
        let o = try K.run(trails, completed: ["D"], start: (0, 0), limit: 7200)
        #expect(o.eligibility.candidates.count == 2)
        let p = try #require(o.plan)
        #expect(K.ids(p) == ["M"])
        #expect(abs(p.efficiency - 0.5) < 1e-6)
        // Both really did fit: M plus N out and back is 8.6 km, about 6200 s.
        let both = K.flatSeconds(2 * 800 + 2 * 3500)
        #expect(both < 7200)
        // And the both-trails walk scores worse.
        let bothScore = RouteScore.value(newMeters: 1300, totalMeters: 8600, finished: 2)
        #expect(p.score > bothScore)
    }

    @Test func stillTakesFarTrailsWhenTheyAreWorthIt() throws {
        // Nothing is done: a long out-and-back is mostly new ground.
        let trails = [K.trail("A", [(0, 0), (2000, 0)]), K.trail("B", [(2000, 0), (2000, 1500)])]
        let o = try K.run(trails, start: (0, 0), limit: 7200)
        let p = try #require(o.plan)
        #expect(Set(K.ids(p)) == ["A", "B"])
    }

    // MARK: - Quentin's case on South Mountain data

    private struct GeomFile: Decodable {
        let id: String
        let trails: [Trail]
    }

    private static func loadGeom() throws -> GeomFile {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        return try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
    }

    private static let pinLat = 33.3664
    private static let pinLon = -112.0309

    private struct Case {
        let best: RoutePlan
        let alternatives: [RouteAlternative]
    }

    /// Dropped pin 33.3664,-112.0309, 3 h loop, default pace.
    ///
    /// ASSUMED completion state (Quentin's real one is not in the repo): he has
    /// done every South Mountain trail except `open`. Those left open are the
    /// trail he was offered plus long trails nobody finishes in 3 h.
    private static func run(open: Set<String>) throws -> Case {
        let geom = try loadGeom()
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        let net = TrailNetwork(trails: inputs)
        var done = Set<TrailNetwork.TrailKey>()
        for t in geom.trails where !open.contains(t.id) { done.insert(.init(areaId: geom.id, trailId: t.id)) }
        let view = try TrailNetworkSnapper(network: net).insert(start: .pin(lat: pinLat, lon: pinLon), end: nil)
        let req = PlanOutcomeRequest(
            network: view, trails: inputs, completion: TrailCompletionLookup(completed: done),
            timeLimitSeconds: 3 * 3600)
        guard case .planned(let best, let alts) = RoutePlanningService.planOutcome(req) else {
            throw CocoaError(.fileReadUnknown)
        }
        return Case(best: best, alternatives: alts)
    }

    private static func line(_ label: String, _ p: RoutePlan) -> String {
        String(format: "%@: %@ | %.2f mi | %@ | %.2f new mi | efficiency %.2f | score %.2f",
               label, p.finished.isEmpty ? "no whole trail" : p.finished.map(\.name).joined(separator: ", "),
               p.distanceMiles, PlanText.duration(p.metrics.seconds), p.newMiles, p.efficiency, p.score)
    }

    /// Before (old objective, whole trails first): Kachina Access Trail, about
    /// 7.4 mi and 2 h 58 m, for 0.13 new mi (efficiency 0.02). After: the best
    /// route is the one with the better score, and it is far more efficient.
    @Test func quentinsPinNoLongerGetsAWalkOverDoneTrails() throws {
        let c = try Self.run(open: ["kachina-access-trail", "national-trail", "desert-classic-trail"])
        print("T7CASE " + Self.line("after best", c.best))
        for a in c.alternatives { print("T7CASE " + Self.line("after \(a.label.title)", a.plan)) }
        #expect(c.best.efficiency > 0.98 / 7.36)
        #expect(c.best.score > RouteScore.value(newMeters: 0.98 * 1609.344, totalMeters: 7.36 * 1609.344, finished: 1))
        #expect(c.best.metrics.seconds <= 3 * 3600 + 1e-3)
    }

    /// Only Kachina Access is left: every route is mostly repeat ground. It is
    /// still returned, flagged low value, and the card says so plainly.
    @Test func mostlyDoneAreaIsFlaggedLowValue() throws {
        let c = try Self.run(open: ["kachina-access-trail"])
        print("T7CASE " + Self.line("only Kachina open", c.best))
        #expect(c.best.isLowValue)
        #expect(c.best.efficiency < RouteScore.lowValueEfficiency)
        let d = PlanResultDisplay.route(c.best, title: "Your hike", units: .imperial, isBest: true)
        let note = try #require(d.lowValueNote)
        #expect(note.hasPrefix("Most trails near here are done. Best is 0.13 new mi in 7."))
        #expect(d.summary.hasPrefix("Kachina Access Trail · 7."))
    }
}
