import Foundation
import Testing
@testable import SouthMountainExplorer

/// `RoutePlanningService.planOutcome` on hand-built scenes ("Plan a Hike", Task 6).
struct RoutePlanOutcomeTests {
    typealias K = PlannerKit

    private func close(_ a: Double, _ b: Double, _ tol: Double = 0.02) -> Bool { abs(a - b) <= tol * max(abs(a), abs(b), 1) }

    private func request(
        _ trails: [Trail], completed: Set<String> = [], start: (Double, Double) = (0, 0),
        end: (Double, Double)? = nil, limit: Double, maxGain: Double? = nil,
        shape: RoutePlanRequest.Shape = .loop
    ) throws -> PlanOutcomeRequest {
        PlanOutcomeRequest(
            network: try K.view(trails, start: start, end: end), trails: K.inputs(trails),
            completion: TrailCompletionLookup(completed: Set(completed.map(K.key))),
            timeLimitSeconds: limit, maxGainFt: maxGain, shape: shape)
    }

    /// A is 1500 m with a 600 m branch B at 500 m. Loop times: A 3000 m, B 2200 m.
    private var forkScene: [Trail] {
        [K.trail("A", [(0, 0), (1500, 0)]), K.trail("B", [(500, 0), (500, 600)])]
    }

    private func nothingFits(_ o: PlanOutcome) -> NothingFits? {
        if case .nothingFits(let n) = o { return n }
        return nil
    }

    private func planned(_ o: PlanOutcome) -> (best: RoutePlan, alts: [RouteAlternative])? {
        if case .planned(let b, let a) = o { return (b, a) }
        return nil
    }

    // MARK: - Nothing fits

    @Test func tinyBudgetGivesClosestFinishAndFallback() throws {
        let req = try request(forkScene, limit: 1200)
        let n = try #require(nothingFits(RoutePlanningService.planOutcome(req)))
        let c = try #require(n.closest)
        // B alone is the cheapest whole trail: 2 x 500 m + 2 x 600 m of flat walking.
        #expect(c.key == K.key("B"))
        #expect(c.name == "Trail B")
        #expect(close(c.exactSeconds, K.flatSeconds(2200)))
        #expect(c.roundedSeconds >= c.exactSeconds)
        #expect(c.roundedSeconds - c.exactSeconds < 300 + 1e-6)
        #expect(c.roundedSeconds.truncatingRemainder(dividingBy: 300) == 0)
        #expect(n.replanSeconds == c.roundedSeconds)
        #expect(n.message.contains("Nothing fits in 20 min."))
        #expect(n.message.contains("closest finish is Trail B in \(PlanText.duration(c.roundedSeconds))"))

        // Fallback: new ground inside the limit, no whole trail.
        let f = try #require(n.fallback)
        #expect(f.metrics.newMeters > 0)
        #expect(f.metrics.seconds <= 1200 + 1e-3)
        #expect(f.finished.isEmpty)
        #expect(f.metrics.isContinuous)
        #expect(f.endNodeId == f.startNodeId)

        // The one-tap action: planning with that time works and finishes B.
        let again = try request(forkScene, limit: try #require(n.replanSeconds))
        let p = try #require(planned(RoutePlanningService.planOutcome(again)))
        #expect(p.best.finishedTrailKeys.contains(K.key("B")))
    }

    @Test func fallbackRespectsGainLimit() throws {
        // Flat fork plus a long steep trail U (with a stub V, so U is cut into
        // edges). Nothing finishes whole in 20 min.
        let t = forkScene + [
            K.trail("U", [(0, 0), (0, 1500)], profile: K.ramp(400)),
            K.trail("V", [(0, 1000), (100, 1000)]),
        ]
        let limit = 1200.0
        let free = try #require(nothingFits(RoutePlanningService.planOutcome(try request(t, limit: limit))))
        let ff = try #require(free.fallback)
        #expect(ff.metrics.seconds <= limit + 1e-3)
        let cap = 20.0
        let capped = try #require(nothingFits(RoutePlanningService.planOutcome(try request(t, limit: limit, maxGain: cap))))
        let cf = try #require(capped.fallback)
        #expect(cf.metrics.gainFt <= cap + 1e-6)
        #expect(cf.metrics.newMeters > 0)
        // Only climbing available: uncapped there is a fallback, with a cap too
        // small for any of it there is none, and the message says so.
        let steep = [K.trail("U", [(0, 0), (0, 900)], profile: K.ramp(300)), K.trail("S1", [(0, 150), (60, 150)])]
        let some = try #require(nothingFits(RoutePlanningService.planOutcome(try request(steep, limit: 300))))
        #expect(some.fallback != nil)
        let none = try #require(nothingFits(RoutePlanningService.planOutcome(try request(steep, limit: 300, maxGain: 10))))
        #expect(none.fallback == nil)
        #expect(none.message.contains("climb limit"))
    }

    @Test func fallbackRespectsOneWay() throws {
        // End point on B, 300 m up it. Whole A (1500 m out) and whole B do not fit in 10 min.
        let req = try request(forkScene, end: (500, 300), limit: 600, shape: .oneWay)
        let n = try #require(nothingFits(RoutePlanningService.planOutcome(req)))
        let f = try #require(n.fallback)
        #expect(f.shape == .oneWay)
        #expect(f.endNodeId == req.network.end?.nodeId)
        #expect(f.endNodeId != f.startNodeId)
        #expect(f.metrics.seconds <= 600 + 1e-3)
        #expect(f.metrics.newMeters > 0)
        #expect(f.metrics.isContinuous)
    }

    @Test func fallbackMissingWhenStartIsTooFar() throws {
        // The only trail starts 3 km from the start point: a 10 min walk cannot reach it.
        let t = [K.trail("Far", [(3000, 0), (3500, 0)]), K.trail("Link", [(0, 0), (3000, 0)])]
        let o = try #require(nothingFits(RoutePlanningService.planOutcome(try request(t, completed: ["Link"], limit: 600))))
        #expect(o.fallback == nil)
        #expect(o.closest?.key == K.key("Far"))
        #expect(o.message.contains("too far"))
    }

    // MARK: - Nothing to plan

    @Test func allCompletedIsNoCandidates() throws {
        let o = RoutePlanningService.planOutcome(try request(forkScene, completed: ["A", "B"], limit: 3600))
        guard case .noCandidates(let s) = o else { Issue.record("expected noCandidates"); return }
        #expect(s.completed == 2)
        #expect(s.total == 2)
        #expect(s.excludedOther == 0)
        #expect(s.message.contains("already completed"))
    }

    @Test func allExcludedCountsPerReason() throws {
        let t = [
            K.trail("A", [(0, 0), (500, 0)]),                                    // completed
            K.trail("Z", [(5000, 5000), (5500, 5000)]),                          // other half of the network
            K.trail("W", [[(2000, 2000), (2100, 2000)], [(2700, 2000), (2800, 2000)]]), // pieces 600 m apart
        ]
        let o = RoutePlanningService.planOutcome(try request(t, completed: ["A"], limit: 3600))
        guard case .noCandidates(let s) = o else { Issue.record("expected noCandidates"); return }
        #expect(s.completed == 1)
        #expect(s.notReachable == 1)
        #expect(s.splitTooFar == 1)
        #expect(s.total == 3)
        #expect(s.excludedOther == 2)
        #expect(s.message.contains("1 already completed"))
        #expect(s.message.contains("1 not reachable"))
    }

    // MARK: - Alternatives

    /// Three spurs off the start (flat 400 m, flat 1200 m, steep 700 m) plus a
    /// second flat spur, so there is room to trade time, climb and trails.
    private var hillScene: [Trail] {
        [
            K.trail("P", [(0, 0), (400, 0)]),
            K.trail("R", [(0, 0), (-1200, 0)]),
            K.trail("Q", [(0, 0), (0, 700)], profile: K.ramp(250)),
            K.trail("S", [(0, 0), (0, -500)]),
        ]
    }

    private func checkAlternatives(_ req: PlanOutcomeRequest, _ p: (best: RoutePlan, alts: [RouteAlternative])) {
        #expect(p.alts.count <= 3)
        #expect(Set(p.alts.map(\.label)).count == p.alts.count)
        for (i, a) in p.alts.enumerated() {
            let m = a.plan.metrics
            #expect(m.seconds <= req.timeLimitSeconds + 1e-3, "\(a.label) over time")
            if let g = req.maxGainFt { #expect(m.gainFt <= g + 1e-6, "\(a.label) over gain") }
            #expect(m.isContinuous)
            #expect(!RouteAlternatives.nearlyIdentical(p.best, a.plan), "\(a.label) matches best")
            for b in p.alts[(i + 1)...] {
                #expect(!RouteAlternatives.nearlyIdentical(a.plan, b.plan), "\(a.label) matches \(b.label)")
            }
            #expect(a.why == expectedWhy(a, best: p.best), "\(a.why)")
            switch a.label {
            case .shortest: #expect(m.seconds < p.best.metrics.seconds)
            case .leastClimb: #expect(m.gainFt < p.best.metrics.gainFt)
            case .mostNewMiles: #expect(m.newMeters > p.best.metrics.newMeters)
            }
        }
    }

    /// Independent restatement of the wording rule.
    private func expectedWhy(_ a: RouteAlternative, best: RoutePlan) -> String {
        let lead: String
        switch a.label {
        case .shortest:
            lead = PlanText.duration(best.metrics.seconds - a.plan.metrics.seconds) + " shorter"
        case .leastClimb:
            lead = "\(Int((best.metrics.gainFt - a.plan.metrics.gainFt).rounded())) ft less climbing"
        case .mostNewMiles:
            lead = String(format: "%.1f mi", (a.plan.metrics.newMeters - best.metrics.newMeters) / 1609.344) + " more new ground"
        }
        let d = a.plan.finished.count - best.finished.count
        let same = Set(a.plan.finishedTrailKeys) == Set(best.finishedTrailKeys)
        let tail = d < 0 ? (d == -1 ? "1 fewer trail" : "\(-d) fewer trails")
            : d > 0 ? (d == 1 ? "1 more trail" : "\(d) more trails")
            : same ? "same trails" : "different trails"
        return "\(lead), \(tail)"
    }

    @Test func alternativesDifferAndRespectLimits() throws {
        var sawAny = false
        for limit in [2400.0, 3000, 3600, 4800] {
            for cap in [nil, 300.0] as [Double?] {
                let req = try request(hillScene, limit: limit, maxGain: cap)
                guard let p = planned(RoutePlanningService.planOutcome(req)) else { continue }
                checkAlternatives(req, p)
                if !p.alts.isEmpty { sawAny = true }
                print("T6HILL limit=\(Int(limit / 60)) min cap=\(cap.map { "\(Int($0))" } ?? "-") best=\(K.ids(p.best)) " +
                      "\(Int(p.best.metrics.seconds / 60)) min gain=\(Int(p.best.metrics.gainFt)) alts=\(p.alts.map { "\($0.label.title): \($0.why)" })")
            }
        }
        #expect(sawAny, "no scene produced an alternative")
    }

    @Test func shortestDropsTrailsOnlyDownToBestMinusOne() throws {
        let req = try request(hillScene, limit: 4800)
        let p = try #require(planned(RoutePlanningService.planOutcome(req)))
        if let s = p.alts.first(where: { $0.label == .shortest }) {
            #expect(s.plan.finished.count >= max(1, p.best.finished.count - 1))
            #expect(s.plan.metrics.seconds < p.best.metrics.seconds)
        }
    }

    @Test func leastClimbIsFlatterAndKeepsMostTrails() throws {
        let req = try request(hillScene, limit: 4800)
        let p = try #require(planned(RoutePlanningService.planOutcome(req)))
        #expect(p.best.metrics.gainFt >= 100)
        let a = try #require(p.alts.first { $0.label == .leastClimb })
        #expect(a.plan.metrics.gainFt <= p.best.metrics.gainFt * 0.9 + 1e-6)
        #expect(a.plan.finished.count >= p.best.finished.count - 1)
    }

    @Test func singleTrailHasNoAlternatives() throws {
        let t = [K.trail("A", [(0, 0), (500, 0)])]
        let req = try request(t, limit: 3600)
        let p = try #require(planned(RoutePlanningService.planOutcome(req)))
        #expect(K.ids(p.best) == ["A"])
        #expect(p.alts.isEmpty)
    }

    @Test func nearlyIdenticalRule() throws {
        let req = try request(hillScene, limit: 4800)
        let p = try #require(planned(RoutePlanningService.planOutcome(req)))
        // A plan against itself is a duplicate.
        #expect(RouteAlternatives.nearlyIdentical(p.best, p.best))
        // A plan finishing fewer trails is not.
        let one = try #require(planned(RoutePlanningService.planOutcome(try request(hillScene, limit: 1000))))
        if one.best.finished.count != p.best.finished.count {
            #expect(!RouteAlternatives.nearlyIdentical(p.best, one.best))
        }
    }

    @Test func mostNewMilesWalkBeatsTinyWholeTrails() throws {
        // Four 20 m stubs off the start are the only whole trails that fit. A
        // 3 km road is cut into 500 m edges by completed crossing trails, so
        // walking down it (without finishing it) is far more new ground.
        var t = [
            K.trail("s0", [(0, 0), (0, 20)]), K.trail("s1", [(0, 0), (0, -20)]),
            K.trail("s2", [(0, 0), (-20, 0)]), K.trail("s3", [(0, 0), (0, 40)]),
            K.trail("Road", [(0, 0), (3000, 0)]),
        ]
        var done: Set<String> = []
        for x in [500.0, 1000, 1500, 2000, 2500] {
            t.append(K.trail("x\(Int(x))", [(x, 0), (x, 60)]))
            done.insert("x\(Int(x))")
        }
        let req = try request(t, completed: done, limit: 2000)
        let p = try #require(planned(RoutePlanningService.planOutcome(req)))
        #expect(Set(K.ids(p.best)) == ["s0", "s1", "s2", "s3"])
        checkAlternatives(req, p)
        let m = try #require(p.alts.first { $0.label == .mostNewMiles })
        #expect(m.plan.metrics.newMeters > 1000)
        // The walk passes the stubs on the way out, so the same four are finished.
        #expect(m.why.hasSuffix("same trails"))
        #expect(m.why.contains("more new ground"))
    }

    @Test func outcomeIsDeterministic() throws {
        let req = try request(hillScene, limit: 4800)
        let a = try #require(planned(RoutePlanningService.planOutcome(req)))
        let b = try #require(planned(RoutePlanningService.planOutcome(req)))
        #expect(a.best.steps == b.best.steps)
        #expect(a.alts.map(\.label) == b.alts.map(\.label))
        #expect(a.alts.map(\.plan.steps) == b.alts.map(\.plan.steps))
        #expect(a.alts.map(\.why) == b.alts.map(\.why))
        let f1 = try request(forkScene, limit: 1200), f2 = try request(forkScene, limit: 1200)
        let n1 = try #require(nothingFits(RoutePlanningService.planOutcome(f1)))
        let n2 = try #require(nothingFits(RoutePlanningService.planOutcome(f2)))
        #expect(n1.message == n2.message)
        #expect(n1.fallback?.steps == n2.fallback?.steps)
    }

    // MARK: - Wording

    @Test func durationAndRounding() {
        #expect(PlanText.duration(15 * 60) == "15 min")
        #expect(PlanText.duration(3600) == "1 h")
        #expect(PlanText.duration(95 * 60) == "1 h 35 m")
        #expect(PlanText.roundedUp(5701) == 6000)
        #expect(PlanText.roundedUp(5700) == 5700)
        #expect(PlanText.roundedUp(1) == 300)
    }
}
