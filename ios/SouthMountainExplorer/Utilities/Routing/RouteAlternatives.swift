import Foundation

/// Alternative routes for a planned hike, shown as chips ("Plan a Hike" v2).
///
/// Up to three, each inside the same time and climb limits as the best route:
///
/// - **Shorter.** Re-plan, whole-trail and new-ground walk both, with the time
///   limit cut to 75%, 50% and 33% of the best route's time (same climb cap).
///   Of the results that take at most 80% of the best route's time, take the
///   one with the best `RouteScore`. When the best route is low value this is
///   the closer, shorter option.
/// - **Less climb.** Only when the best route climbs at least 100 ft. Re-plan
///   with the climb cap cut to 75%, 50% and 25% of the best route's climb (same
///   time limit). Take the flattest result that finishes at least `best - 1`
///   whole trails (and at least 1) and climbs at least 10% less. The ladder
///   stops at the first step that fails the trail test.
/// - **More new.** The new-miles walk over the full limits (whole trails not
///   required). Kept only if it walks at least 5% (and 0.1 mi) more new ground
///   than the best route.
///
/// An alternative is dropped when it is nearly identical to the best route or to
/// an earlier alternative: same finished trails and under 5% difference in time.
/// Alternatives are considered in the order above.
enum RouteAlternatives {
    static let timeLadder = [0.75, 0.50, 0.33]
    /// "Shorter" must take at most this share of the best route's time.
    static let shorterMaxShare = 0.80
    static let climbLadder = [0.75, 0.50, 0.25]
    static let nearlyIdenticalTime = 0.05
    private static let minClimbToReduceFt = 100.0
    private static let minClimbSaving = 0.10
    private static let minNewGain = 0.05
    private static let minNewGainMeters = 160.9344

    static func build(
        best: RoutePlan, base: RoutePlanRequest, request: PlanOutcomeRequest, credit: [TrailEligibility.Candidate],
        newMiles: RoutePlan? = nil
    ) -> [RouteAlternative] {
        let floorCount = max(1, best.finished.count - 1)
        var found: [(RouteAlternative.Label, RoutePlan)] = []

        if let p = shorter(best: best, base: base, request: request, credit: credit) { found.append((.shortest, p)) }
        if let p = leastClimb(best: best, base: base, floorCount: floorCount) { found.append((.leastClimb, p)) }
        if let p = newMiles ?? NewMilesRoute.plan(
            view: request.network, credit: credit, completion: request.completion,
            limitSeconds: request.timeLimitSeconds, maxGainFt: request.maxGainFt,
            paceFactor: request.paceFactor, shape: request.shape),
           p.metrics.newMeters >= best.metrics.newMeters * (1 + minNewGain),
           p.metrics.newMeters - best.metrics.newMeters >= minNewGainMeters {
            found.append((.mostNewMiles, p))
        }

        var kept: [RouteAlternative] = []
        var seen: [RoutePlan] = [best]
        for (label, plan) in found {
            if seen.contains(where: { nearlyIdentical($0, plan) }) { continue }
            seen.append(plan)
            kept.append(RouteAlternative(label: label, plan: plan, why: why(label, plan, versus: best)))
        }
        return kept
    }

    // MARK: - Rungs

    /// Whole-trail plans and new-ground walks at each cut time; the best
    /// score among those at most 80% of the best route's time wins.
    private static func shorter(
        best: RoutePlan, base: RoutePlanRequest, request: PlanOutcomeRequest,
        credit: [TrailEligibility.Candidate]
    ) -> RoutePlan? {
        var pick: RoutePlan?
        func offer(_ p: RoutePlan?) {
            guard let p, p.metrics.seconds <= best.metrics.seconds * shorterMaxShare else { return }
            if let q = pick,
               !RouteScore.isBetter(score: p.score, efficiency: p.efficiency, seconds: p.metrics.seconds,
                                    than: q.score, efficiency: q.efficiency, seconds: q.metrics.seconds) { return }
            pick = p
        }
        for f in timeLadder {
            let cut = best.metrics.seconds * f
            offer(RoutePlanner.plan(with(base, time: cut, gain: base.maxGainFt)))
            offer(NewMilesRoute.plan(
                view: request.network, credit: credit, completion: request.completion,
                limitSeconds: cut, maxGainFt: request.maxGainFt,
                paceFactor: request.paceFactor, shape: request.shape))
        }
        return pick
    }

    private static func leastClimb(best: RoutePlan, base: RoutePlanRequest, floorCount: Int) -> RoutePlan? {
        guard best.metrics.gainFt >= minClimbToReduceFt else { return nil }
        var pick: RoutePlan?
        for f in climbLadder {
            guard let p = RoutePlanner.plan(with(base, time: base.timeLimitSeconds, gain: best.metrics.gainFt * f)),
                  p.finished.count >= floorCount else { break }
            if p.metrics.gainFt <= best.metrics.gainFt * (1 - minClimbSaving),
               p.metrics.gainFt < (pick?.metrics.gainFt ?? .infinity) { pick = p }
        }
        return pick
    }

    private static func with(_ r: RoutePlanRequest, time: Double, gain: Double?) -> RoutePlanRequest {
        RoutePlanRequest(network: r.network, candidates: r.candidates, completion: r.completion,
                         timeLimitSeconds: time, maxGainFt: gain, paceFactor: r.paceFactor, shape: r.shape)
    }

    // MARK: - Duplicates and wording

    static func nearlyIdentical(_ a: RoutePlan, _ b: RoutePlan) -> Bool {
        guard Set(a.finishedTrailKeys) == Set(b.finishedTrailKeys) else { return false }
        let hi = max(a.metrics.seconds, b.metrics.seconds)
        return hi <= 0 || abs(a.metrics.seconds - b.metrics.seconds) / hi < nearlyIdenticalTime
    }

    /// "28 min shorter, 1 fewer trail".
    static func why(_ label: RouteAlternative.Label, _ plan: RoutePlan, versus best: RoutePlan) -> String {
        let lead: String
        switch label {
        case .shortest:
            lead = "\(PlanText.duration(max(0, best.metrics.seconds - plan.metrics.seconds))) shorter"
        case .leastClimb:
            lead = "\(Int((best.metrics.gainFt - plan.metrics.gainFt).rounded())) ft less climbing"
        case .mostNewMiles:
            lead = "\(PlanText.miles(plan.metrics.newMeters - best.metrics.newMeters)) more new ground"
        }
        let diff = plan.finished.count - best.finished.count
        let trails: String
        if diff < 0 {
            trails = diff == -1 ? "1 fewer trail" : "\(-diff) fewer trails"
        } else if diff > 0 {
            trails = diff == 1 ? "1 more trail" : "\(diff) more trails"
        } else if Set(plan.finishedTrailKeys) == Set(best.finishedTrailKeys) {
            trails = "same trails"
        } else {
            trails = "different trails"
        }
        return "\(lead), \(trails)"
    }
}
