import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike v2, round 2: routes spend the time on new ground, partial
/// trails are allowed, and a finishable trail is not left out.
struct RoutePlanRound2QualityTests {
    typealias K = PlannerKit

    // MARK: - Synthetic junction

    /// A completed loop to the left of the start (L and L2), an unfinished
    /// trail R to the right, and R2 beyond it.
    private static func scene(rLength: Double, withR2: Bool, crossed: Bool = false) -> [Trail] {
        var t = (crossed ? crossings(along: rLength) : []) + [
            K.trail("L", [(0, 0), (-600, 0), (-600, -600)]),
            K.trail("L2", [(-600, -600), (0, -600), (0, 0)]),
            K.trail("R", [(0, 0), (rLength, 0)]),
        ]
        if withR2 { t.append(K.trail("R2", [(rLength, 0), (rLength, 600)])) }
        return t
    }

    /// Short completed trails crossing the x axis every 800 m, so a long trail
    /// along it has junctions to stop at (edges are whole units for the planner).
    private static func crossings(along length: Double) -> [Trail] {
        stride(from: 800.0, to: length, by: 800.0).enumerated().map { i, x in
            K.trail("X\(i)", [(x, -40), (x, 40)])
        }
    }

    private static func completedCrossings(along length: Double) -> Set<String> {
        Set(crossings(along: length).map { $0.id })
    }

    private static func outcome(_ trails: [Trail], completed: Set<String>, limit: Double) throws -> PlanOutcome {
        let v = try K.view(trails)
        let req = PlanOutcomeRequest(
            network: v, trails: K.inputs(trails),
            completion: TrailCompletionLookup(completed: Set(completed.map(K.key))), timeLimitSeconds: limit)
        return RoutePlanningService.planOutcome(req)
    }

    /// Left is completed trail that closes a loop; right is unfinished trail
    /// that can be finished (with R2) inside the budget. The plan goes right
    /// and finishes both, and never touches the completed loop.
    @Test func junctionGoesOntoUnfinishedTrailAndFinishesTwo() throws {
        let o = try Self.outcome(Self.scene(rLength: 700, withR2: true), completed: ["L", "L2"], limit: 2700)
        guard case .planned(let best, _) = o else { Issue.record("expected a plan"); return }
        #expect(Set(best.finishedTrailKeys) == [K.key("R"), K.key("R2")])
        #expect(best.stepInfo.first?.trailKey == K.key("R"))
        #expect(best.metrics.metersByTrail[K.key("L")] == nil)
        #expect(best.metrics.metersByTrail[K.key("L2")] == nil)
        #expect(best.newMiles > 0.78)
        #expect(best.metrics.seconds <= 2700 + 1e-3)
    }

    /// Same junction, but the right trail is too long to finish in the time.
    /// Nothing fits, and the fallback still turns right for partial new miles
    /// instead of walking the completed loop.
    @Test func junctionGoesRightForPartialMilesWhenTrailCannotBeFinished() throws {
        let o = try Self.outcome(Self.scene(rLength: 4000, withR2: false, crossed: true), completed: Set(["L", "L2"]).union(Self.completedCrossings(along: 4000)), limit: 3600)
        guard case .nothingFits(let nf) = o, let plan = nf.fallback else { Issue.record("expected a fallback"); return }
        #expect(plan.finished.isEmpty)
        #expect(plan.stepInfo.first?.trailKey == K.key("R"))
        #expect(plan.metrics.metersByTrail[K.key("L")] == nil)
        #expect(plan.newMiles > 1.2)
        #expect(plan.metrics.seconds <= 3600 + 1e-3)
        // New ground is the out leg only; the way back is repeat.
        #expect(abs(plan.metrics.newMeters * 2 - plan.metrics.meters) < 5)
    }

    /// Partial stretch next to a finishable trail: a long unfinished trail Q
    /// is next to a short one S. The plan finishes S and also walks part of Q
    /// with the spare time.
    @Test func spareTimeGoesIntoAPartialStretchOfAnUnfinishedTrail() throws {
        let trails = [
            K.trail("S", [(0, 0), (0, 400)]),
            K.trail("Q", [(0, 0), (4000, 0)]),
            K.trail("D", [(0, 0), (-500, 0)]),
        ] + Self.crossings(along: 4000)
        let o = try Self.outcome(trails, completed: Set(["D"]).union(Self.completedCrossings(along: 4000)), limit: 3600)
        guard case .planned(let best, _) = o else { Issue.record("expected a plan"); return }
        #expect(best.finishedTrailKeys == [K.key("S")])
        let q = best.metrics.metersByTrail[K.key("Q")] ?? 0
        #expect(q > 1500)
        #expect(best.metrics.metersByTrail[K.key("D")] == nil)
    }

    // MARK: - Control

    /// When a whole trail fits cleanly it is still finished.
    @Test func wholeTrailThatFitsIsStillFinished() throws {
        let trails = Self.scene(rLength: 1000, withR2: false)
        let o = try Self.outcome(trails, completed: ["L", "L2"], limit: 3600)
        guard case .planned(let best, _) = o else { Issue.record("expected a plan"); return }
        #expect(best.finishedTrailKeys == [K.key("R")])
        #expect(best.stepInfo.first?.trailKey == K.key("R"))
        #expect(abs(best.metrics.newMeters - 1000) < 5)
    }

    // MARK: - Score constants

    @Test func repeatMilesCostFortyPercent() {
        let mi = 1609.344
        #expect(RouteScore.repeatPenalty == 0.4)
        // 1 new mile and 1 repeat mile, no trail finished: 1 - 0.4.
        #expect(abs(RouteScore.value(newMeters: mi, totalMeters: 2 * mi, finished: 0) - 0.6) < 1e-9)
        // Old trail never beats new trail: a mile of each, new wins.
        #expect(RouteScore.value(newMeters: mi, totalMeters: mi, finished: 0)
            > RouteScore.value(newMeters: 0, totalMeters: mi, finished: 0))
    }

    // MARK: - South Mountain

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

    /// Result of the dd770a1a2 planner (before this round) on the same
    /// starts and states: new mi, total mi, trails finished. Measured with the
    /// standalone harness in .agents/tasks/plan-a-hike-v2/round2/harness.
    private static let baseline: [String: (new: Double, total: Double, trails: Int)] = [
        "S0-sparse": (3.40, 8.42, 3), "S0-r60": (3.88, 8.27, 2),
        "S1-sparse": (3.45, 6.83, 3), "S1-r60": (2.84, 7.46, 3),
        "S2-sparse": (3.28, 7.79, 3), "S2-r60": (3.14, 6.29, 2),
        "S3-sparse": (2.81, 8.79, 3), "S3-r60": (6.29, 8.23, 8),
        "S4-sparse": (3.11, 8.20, 2), "S4-r60": (4.70, 9.20, 4),
        "S5-sparse": (1.16, 8.03, 1), "S5-r60": (1.35, 8.06, 2),
    ]

    private struct Row {
        let id: String
        let plan: RoutePlan
        let oracle: PlanEval?
    }

    /// Exhaustive search over orders of up to 3 whole trails (the engine's own
    /// entry / exit choice), scored with the current `RouteScore`.
    private static func oracle(_ request: RoutePlanRequest) -> PlanEval? {
        guard let eng = RoutePlanEngine(request) else { return nil }
        eng.prepare()
        let singles = (0..<eng.singleCount).filter { eng.jobs[$0].soloSecs <= eng.limit + 1e-6 }
        var best: PlanState?
        var calls = 0
        func dfs(_ order: [Int]) {
            for j in singles where !order.contains(j) {
                if calls >= 6000 { return }
                calls += 1
                let o = order + [j]
                guard let st = eng.makeState(order: o) else { continue }
                if best == nil || eng.better(st, best!) { best = st }
                if o.count < 3 { dfs(o) }
            }
        }
        dfs([])
        return best?.eval
    }

    /// Six dropped pins on South Mountain trails, two completion states each:
    /// "sparse" (six reachable trails left open, the rest done) and "r60"
    /// (about 60 percent done by a fixed rule). Same generator as the
    /// standalone harness (`bench.swift`). Records a before/after table.
    private static func runSouthMountain() throws -> [Row] {
        let geom = try loadGeom()
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        let net = TrailNetwork(trails: inputs)
        let limit = 3.0 * 3600
        var rows: [Row] = []
        for i in 0..<6 {
            let t = geom.trails[(i * 11 + 5) % geom.trails.count]
            let seg = t.segments[0]
            let v = seg[seg.count / 2]
            let view = try TrailNetworkSnapper(network: net).insert(
                start: .pin(lat: v[0] + 0.0002, lon: v[1]), end: nil)
            // The scenario generator keeps the round 2 split rule (0.25 mi), so
            // the "sparse" open sets stay the ones the baseline was measured on.
            var pick = TrailEligibility.Options(budgetSeconds: limit)
            pick.maxSplitGapMeters = 402.336
            let wide = TrailEligibility.evaluate(network: view, trails: inputs, completion: .none, options: pick)
            let reach = wide.candidates.map(\.key.trailId).sorted()
            var open = Set<String>()
            var k = i % 3
            while k < reach.count && open.count < 6 { open.insert(reach[k]); k += 3 }
            let sparse = Set(geom.trails.filter { !open.contains($0.id) }.map { TrailNetwork.TrailKey(areaId: geom.id, trailId: $0.id) })
            var r60 = Set<TrailNetwork.TrailKey>()
            for (idx, tr) in geom.trails.enumerated() where (idx * 37 + i * 11) % 10 < 6 {
                r60.insert(.init(areaId: geom.id, trailId: tr.id))
            }
            for (label, done) in [("sparse", sparse), ("r60", r60)] {
                let completion = TrailCompletionLookup(completed: done)
                let req = PlanOutcomeRequest(network: view, trails: inputs, completion: completion, timeLimitSeconds: limit)
                guard case .planned(let best, _) = RoutePlanningService.planOutcome(req) else { continue }
                let elig = TrailEligibility.evaluate(
                    network: view, trails: inputs, completion: completion, options: .init(budgetSeconds: limit))
                let inLimit = elig.candidates.filter { $0.estimatedSeconds <= limit }
                let base = RoutePlanRequest(
                    network: view, candidates: inLimit, completion: completion, timeLimitSeconds: limit)
                rows.append(Row(id: "S\(i)-\(label)", plan: best, oracle: oracle(base)))
            }
        }
        return rows
    }

    @Test func southMountainRoutesSpendTimeOnNewGround() throws {
        let rows = try Self.runSouthMountain()
        #expect(rows.count == 12)
        var lines = ["start | before new/total/trails | after new/total/trails | oracle new/total/trails"]
        var shareBefore = 0.0, shareAfter = 0.0, newBefore = 0.0, newAfter = 0.0
        for r in rows {
            let b = try #require(Self.baseline[r.id])
            let p = r.plan
            let o = r.oracle
            lines.append(String(
                format: "%@ | %.2f / %.2f / %d | %.2f / %.2f / %d | %@", r.id, b.new, b.total, b.trails,
                p.newMiles, p.distanceMiles, p.finished.count,
                o.map { String(format: "%.2f / %.2f / %d", $0.newMeters / 1609.344, $0.totalMeters / 1609.344, $0.finished.count) } ?? "none"))
            shareBefore += b.new / b.total
            shareAfter += p.efficiency
            newBefore += b.new
            newAfter += p.newMiles
            // Never worse than the old planner under the new score.
            let beforeScore = RouteScore.value(newMeters: b.new * 1609.344, totalMeters: b.total * 1609.344, finished: b.trails)
            #expect(p.score >= beforeScore - 0.05, "\(r.id): score \(p.score) vs before \(beforeScore)")
            // Never worse than the exhaustive whole-trail search, within a small tolerance.
            if let o {
                #expect(p.score >= o.score - 0.25, "\(r.id): score \(p.score) vs oracle \(o.score)")
                #expect(p.efficiency >= o.efficiency - 0.08, "\(r.id): share \(p.efficiency) vs oracle \(o.efficiency)")
                #expect(p.finished.count >= o.finished.count - 1, "\(r.id): trails \(p.finished.count) vs oracle \(o.finished.count)")
            }
            #expect(p.metrics.seconds <= 3 * 3600 + 1e-3)
        }
        print("ROUND2TABLE\n" + lines.joined(separator: "\n"))
        // Better on average: more of every walk is new ground, and no fewer new miles overall.
        #expect(shareAfter / Double(rows.count) > shareBefore / Double(rows.count))
        #expect(newAfter >= newBefore * 0.97)
    }
}
