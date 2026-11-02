import Foundation

/// Top-level, UI-free entry for Plan a Hike ("Plan a Hike", Task 6).
///
/// `planOutcome` runs Task 4 eligibility and the Task 5 planner and answers one
/// of three ways: a best route plus alternatives, "nothing fits" (honest
/// message, the exact time the closest trail needs, and a fallback that walks
/// the most new ground), or "nothing to plan" (counts per reason). Pure and
/// deterministic.
enum RoutePlanningService {
    /// Longest "closest finish" worth offering.
    static let maxClosestSeconds = 24.0 * 3600
    /// "Closest finish" time is rounded up to this.
    static let roundingSeconds = 300.0
    /// Exact single-trail plans tried for the closest finish at most.
    private static let maxClosestEvaluations = 15

    static func planOutcome(_ request: PlanOutcomeRequest) -> PlanOutcome {
        let limit = request.timeLimitSeconds
        let trip: TrailEligibility.Trip = request.shape == .loop ? .loop : .oneWay
        // One eligibility pass at a 24 h budget: every trail that could ever be
        // finished. Filtering by the estimate gives exactly the within-limit set
        // (the estimate does not depend on the budget; the budget-based reach
        // prefilter only drops trails whose estimate is already over it).
        let wide = TrailEligibility.evaluate(
            network: request.network, trails: request.trails, completion: request.completion,
            options: .init(budgetSeconds: maxClosestSeconds, paceFactor: request.paceFactor, trip: trip))
        if wide.candidates.isEmpty { return .noCandidates(summary(wide)) }

        let inLimit = wide.candidates.filter { $0.estimatedSeconds <= limit }
        let base = RoutePlanRequest(
            network: request.network, candidates: inLimit, completion: request.completion,
            timeLimitSeconds: limit, maxGainFt: request.maxGainFt,
            paceFactor: request.paceFactor, shape: request.shape)
        if !inLimit.isEmpty, let best = RoutePlanner.plan(base) {
            let alts = RouteAlternatives.build(best: best, base: base, request: request, credit: inLimit)
            return .planned(best: best, alternatives: alts)
        }
        return .nothingFits(nothingFits(request, wide: wide, credit: inLimit))
    }

    // MARK: - Nothing to plan

    private static func summary(_ elig: TrailEligibility) -> NoCandidates {
        var s = NoCandidates(message: "")
        for x in elig.excluded {
            switch x.reason {
            case .completed: s.completed += 1
            case .noEdges: s.noRoute += 1
            case .splitTooFar: s.splitTooFar += 1
            case .piecesNotConnected: s.piecesNotConnected += 1
            case .disconnected: s.notReachable += 1
            case .outOfReach: s.tooFar += 1
            }
        }
        var parts: [String] = []
        func add(_ n: Int, _ text: String) { if n > 0 { parts.append("\(n) \(text)") } }
        add(s.completed, "already completed")
        add(s.notReachable, "not reachable from the start")
        add(s.splitTooFar, "can't be finished in one route")
        add(s.piecesNotConnected, "have pieces that aren't connected")
        add(s.noRoute, "have no routable path")
        add(s.tooFar, "too far to reach")
        let head = s.excludedOther == 0 && s.completed > 0
            ? "Every trail here is already completed."
            : "There's no trail left to plan from here."
        let message = parts.isEmpty ? head : head + " " + parts.joined(separator: ", ") + "."
        return NoCandidates(completed: s.completed, noRoute: s.noRoute, splitTooFar: s.splitTooFar,
                            piecesNotConnected: s.piecesNotConnected, notReachable: s.notReachable,
                            tooFar: s.tooFar, message: message)
    }

    // MARK: - Nothing fits

    private static func nothingFits(
        _ request: PlanOutcomeRequest, wide: TrailEligibility, credit: [TrailEligibility.Candidate]
    ) -> NothingFits {
        let limit = request.timeLimitSeconds
        let closest = closestFinish(request, candidates: wide.candidates)
        let fallback = NewMilesRoute.plan(
            view: request.network, credit: credit, completion: request.completion, limitSeconds: limit,
            maxGainFt: request.maxGainFt, paceFactor: request.paceFactor, shape: request.shape)

        var text = "Nothing fits in \(PlanText.duration(limit))."
        if let c = closest {
            text += " The closest finish is \(c.name) in \(PlanText.duration(c.roundedSeconds))."
        } else if request.maxGainFt != nil {
            text += " No trail can be finished within your climb limit."
        } else {
            text += " No trail can be finished in a day."
        }
        if let f = fallback {
            text += " Closest you can get: \(PlanText.miles(f.metrics.newMeters)) of new trail in "
                + "\(PlanText.duration(f.metrics.seconds))."
        } else if request.maxGainFt != nil {
            text += " No new trail can be reached within your time and climb limits."
        } else {
            text += " You're too far from any new trail to reach it and get back in time."
        }
        return NothingFits(limitSeconds: limit, closest: closest, fallback: fallback, message: text)
    }

    /// The candidate with the least EXACT time when planned alone. Candidates
    /// come cheapest-lower-bound first and a lower bound never overstates, so
    /// the search stops as soon as the next lower bound cannot beat the best
    /// exact time found.
    private static func closestFinish(
        _ request: PlanOutcomeRequest, candidates: [TrailEligibility.Candidate]
    ) -> NothingFits.Closest? {
        var best: (seconds: Double, candidate: TrailEligibility.Candidate)?
        for c in candidates.prefix(maxClosestEvaluations) {
            if let b = best, c.estimatedSeconds > b.seconds { break }
            let alone = RoutePlanRequest(
                network: request.network, candidates: [c], completion: request.completion,
                timeLimitSeconds: maxClosestSeconds, maxGainFt: request.maxGainFt,
                paceFactor: request.paceFactor, shape: request.shape)
            guard let plan = RoutePlanner.plan(alone), plan.finishedTrailKeys.contains(c.key) else { continue }
            let secs = plan.metrics.seconds
            if best == nil || (secs, c.key) < (best!.seconds, best!.candidate.key) { best = (secs, c) }
        }
        guard let b = best else { return nil }
        // Always strictly above the limit, so the re-plan action means something.
        let floor = PlanText.roundedUp(request.timeLimitSeconds + 1, step: roundingSeconds)
        let rounded = max(PlanText.roundedUp(b.seconds, step: roundingSeconds), floor)
        return .init(key: b.candidate.key, name: b.candidate.name, exactSeconds: b.seconds, roundedSeconds: rounded)
    }
}
