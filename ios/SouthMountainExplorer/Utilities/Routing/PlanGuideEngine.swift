import Foundation

// "Plan a Hike", Release B task 3: the guide. Turns the planned steps into
// turn cues and off-route warnings while you walk. Pure and deterministic,
// like `TurnCueEngine`: fixes in, `TurnCueEvent`s out, so the existing
// dispatcher, voice, haptics and Lock Screen code deliver them unchanged.
// GPS trust rules and off-route numbers are copied from `TurnCueEngine`
// (left untouched so trail cues cannot regress).

struct PlanGuideConfig: Sendable, Equatable {
    var units: UnitsPreference
    /// A fix worse than this is untrustworthy: the guide goes silent.
    var maxAcceptableAccuracy = 50.0
    /// A fix older than this (against `now`) is stale.
    var staleFixSeconds = 10.0
    /// A discontinuity in the fix stream (`GpsIngest.gapMs`).
    var gapSeconds = GpsIngest.gapMs / 1000
    /// Min seconds between spoken cues (a cue at the turn ignores it).
    var minSpokenSpacing = 6.0
    var weakAfterDegradedSeconds = 15.0
    var weakNeedsGoodSeconds = 30.0
    var weakMinIntervalSeconds = 300.0
    /// The heads-up comes about 300 ft before each turn.
    var headsUpMeters = 300 / TurnCuePhrasing.feetPerMeter
    /// "Route complete" once you are this close to the end of the plan.
    var finishMeters = 15.0
    /// Off route: farther than this from the planned line (or 2x accuracy
    /// plus 15 m if larger)...
    var offRouteMeters = 40.0
    /// ...for this long, over this many fixes, with this much movement.
    var offRouteSeconds = 20.0
    var offRouteFixes = 3
    var offRouteMoveMeters = 10.0
    /// No warning until you have been on the route this long.
    var armSeconds = 10.0
    /// Back on route within this share of the off-route distance, for this long.
    var backOnFraction = 0.6
    var backOnSeconds = 6.0
    /// A repeat warning needs this long since the last one and this much
    /// farther from the route.
    var repeatSeconds = 120.0
    var repeatFartherMeters = 50.0
    var matcher = PlanProgressMatcher.Config()

    init(units: UnitsPreference) {
        self.units = units
    }
}

/// The live guide for one planned hike.
///
/// Field-trust rule (as the trail engine): while GPS is degraded (poor
/// accuracy, stale, or a gap) there are no cues, no off-route warning and
/// the step is frozen; at most one `gpsWeak` notice per episode. Each turn
/// gets a heads-up about 300 ft before and a cue at the turn, once each.
/// The finish is reported as `routeCompleted` (no voice).
struct PlanGuideEngine: Sendable {
    /// What one fix or tick did.
    struct Update: Sendable, Equatable {
        var events: [TurnCueEvent] = []
        var stepChanged = false
        /// You reached the end of the plan with this fix (once).
        var routeCompleted = false
    }

    /// What the bar and the Live Activity show.
    struct Status: Sendable, Equatable {
        /// 0-based index into the route's steps.
        let stepIndex: Int
        let stepCount: Int
        /// To the start of the next step, or to the finish on the last step.
        let metersToNextTurn: Double
        /// Straight line to the nearest point of the plan, while off route.
        let offRouteMeters: Double?
        let isComplete: Bool
        let gpsTrusted: Bool
    }

    private enum Stage: Sendable { case idle, headsUp, atTurn }

    private struct TurnState: Sendable {
        var stage: Stage = .idle
        var nearCount = 0
    }

    let route: PlanGuideRoute
    var config: PlanGuideConfig

    private(set) var matcher: PlanProgressMatcher
    private(set) var advisory: TurnCueAdvisory?
    private(set) var isComplete = false
    private var turns: [TurnState]
    private var lastSpokenT: Double?

    // quality
    private var degraded = true
    private var initialWarmup = true
    private var lastFixT: Double?
    private var goodStreak = 0
    private var streakStartT = 0.0
    private var goodSince: Double?
    private var lastGoodT = 0.0
    private var goodRunBeforeEpisode = 0.0
    private var episodeStart: Double?
    private var weakEmittedThisEpisode = false
    private var lastWeakT: Double?

    // off route
    private var lineMeters: Double?
    private var onSince: Double?
    private var armed = false
    private var offSince: Double?
    private var offFixes = 0
    private var offStart = LocalPt(x: 0, y: 0)
    private var offMaxMove = 0.0
    private(set) var offAnnounced = false
    private var lastOffT: Double?
    private var lastOffPerp = 0.0
    private var backSince: Double?

    init(route: PlanGuideRoute, config: PlanGuideConfig) {
        self.route = route
        self.config = config
        self.matcher = PlanProgressMatcher(route: route, config: config.matcher)
        self.turns = Array(repeating: TurnState(), count: route.markers.count)
    }

    var stepIndex: Int { isComplete ? route.markers.count - 1 : matcher.stepIndex }

    var status: Status {
        let i = stepIndex
        let end = i + 1 < route.markers.count ? route.markers[i + 1].startMeters : route.length
        return Status(
            stepIndex: i, stepCount: route.markers.count,
            metersToNextTurn: isComplete ? 0 : max(0, end - matcher.cursorMeters),
            offRouteMeters: offAnnounced && !degraded ? lineMeters : nil,
            isComplete: isComplete, gpsTrusted: !degraded)
    }

    /// Back and Next: the guide moves to step `index`. Cues for that step's
    /// turn and the ones after it fire again; auto tracking continues from there.
    mutating func move(toStep index: Int) {
        matcher.move(toStep: index)
        isComplete = false
        for k in turns.indices where k > matcher.stepIndex { turns[k] = TurnState() }
        advisory = currentAdvisory()
    }

    /// After a relaunch: back on saved step `index`, tracking forward from
    /// its start. A guide that had finished stays finished (no second buzz).
    mutating func restore(toStep index: Int, isComplete complete: Bool) {
        move(toStep: index)
        isComplete = complete
        advisory = currentAdvisory()
    }

    /// True once the event's turn is behind you, or the notice no longer
    /// applies. Adapters drop stale cues instead of speaking them late.
    func isStale(_ event: TurnCueEvent) -> Bool {
        switch event.kind {
        case .approach, .atJunction:
            guard let id = event.junctionId else { return false }
            return isComplete || id <= matcher.stepIndex
        case .offTrail:
            return !offAnnounced
        case .backOnTrail, .gpsWeak:
            return false
        }
    }

    // MARK: - Ingest

    mutating func ingest(_ fix: TurnCueFix, now: TimeInterval) -> Update {
        guard fix.lat.isFinite, fix.lon.isFinite, abs(fix.lat) <= 90, abs(fix.lon) <= 180,
              fix.timestamp.isFinite, now.isFinite, fix.timestamp <= now + 5 else { return Update() }
        let t = fix.timestamp
        if let last = lastFixT, t <= last { return Update() }

        let acc = fix.horizontalAccuracy
        let accBad = !(acc.isFinite && acc >= 0 && acc <= config.maxAcceptableAccuracy)
        let stale = now - t > config.staleFixSeconds
        let prevT = lastFixT
        lastFixT = t

        if let prev = prevT, t - prev > config.gapSeconds {
            enterDegraded(at: prev)
        }
        if accBad || stale {
            enterDegraded(at: t)
            advisory = nil
            return Update(events: weakEventIfDue(at: t))
        }

        // Good fix.
        if !degraded {
            lastGoodT = t
        } else {
            goodStreak += 1
            if goodStreak == 1 { streakStartT = t }
            let needSpan = initialWarmup ? 2.0 : 5.0
            if goodStreak >= 3, t - streakStartT >= needSpan {
                degraded = false
                initialWarmup = false
                goodSince = streakStartT
                lastGoodT = t
                episodeStart = nil
            } else {
                advisory = nil
                return Update()
            }
        }

        let u = track(fix, acc: acc)
        advisory = currentAdvisory()
        return u
    }

    /// Call periodically (about 1 Hz) when no new fix arrived, so a stalled
    /// stream is noticed.
    mutating func tick(now: TimeInterval) -> Update {
        guard let last = lastFixT, now.isFinite else { return Update() }
        if !degraded, now - last > config.staleFixSeconds {
            enterDegraded(at: last)
            advisory = nil
        }
        guard degraded else { return Update() }
        return Update(events: weakEventIfDue(at: now))
    }

    // MARK: - Quality

    private mutating func enterDegraded(at t: Double) {
        goodStreak = 0
        if !degraded {
            degraded = true
            goodRunBeforeEpisode = max(0, lastGoodT - (goodSince ?? lastGoodT))
            episodeStart = t
            weakEmittedThisEpisode = false
        } else if episodeStart == nil, !initialWarmup {
            episodeStart = t
        }
        offSince = nil
        onSince = nil
        backSince = nil
    }

    private mutating func weakEventIfDue(at t: Double) -> [TurnCueEvent] {
        guard degraded, !initialWarmup, let start = episodeStart, !weakEmittedThisEpisode,
              goodRunBeforeEpisode >= config.weakNeedsGoodSeconds,
              t - start >= config.weakAfterDegradedSeconds else { return [] }
        if let last = lastWeakT, t - last < config.weakMinIntervalSeconds { return [] }
        weakEmittedThisEpisode = true
        lastWeakT = t
        return [TurnCueEvent(
            kind: .gpsWeak, junctionId: nil, symbol: .gpsWeak,
            spoken: TurnCuePhrasing.gpsWeak, shortText: TurnCuePhrasing.gpsWeak, distanceMeters: nil
        )]
    }

    // MARK: - Tracking

    private mutating func track(_ fix: TurnCueFix, acc: Double) -> Update {
        guard !isComplete else { return Update() }
        let t = fix.timestamp
        let p = route.local(lat: fix.lat, lon: fix.lon)
        let perp = Self.distanceToLine(p, route)
        lineMeters = perp
        let tOff = max(config.offRouteMeters, 2 * acc + 15)
        let tOn = config.backOnFraction * tOff

        var u = Update()
        u.events = offRouteStep(perp: perp, p: p, t: t, tOff: tOff, tOn: tOn)

        let before = matcher.stepIndex
        let m = matcher.ingest(fix)
        u.stepChanged = matcher.stepIndex != before
        guard m.kind == .matched || m.kind == .skippedAhead, perp <= tOff else { return u }

        if matcher.cursorMeters >= route.length - config.finishMeters {
            isComplete = true
            offAnnounced = false
            u.routeCompleted = true
            u.stepChanged = u.stepChanged || before != stepIndex
            return u
        }
        // A skip ahead passes the missed turns silently.
        if m.kind == .matched { u.events.append(contentsOf: evaluateTurn(t: t, acc: acc, fix: fix)) }
        return u
    }

    /// The next step's turn: a heads-up about 300 ft before, then a cue at
    /// the turn. A turn too close for a heads-up gets only the cue at the turn.
    private mutating func evaluateTurn(t: Double, acc: Double, fix: TurnCueFix) -> [TurnCueEvent] {
        let next = matcher.stepIndex + 1
        guard next < route.markers.count else { return [] }
        let marker = route.markers[next]
        guard let symbol = Self.symbol(for: marker.action) else { return [] }
        let ahead = marker.startMeters - matcher.cursorMeters
        let v = fix.speed.map { max(0.8, min(3.0, $0)) } ?? 1.3
        let dNear = max(15, min(30, v * 8 + acc))
        var st = turns[next]
        defer { turns[next] = st }
        var events: [TurnCueEvent] = []

        if st.stage == .idle, ahead <= config.headsUpMeters {
            if ahead > dNear + 10 {
                let spacingOK = lastSpokenT.map { t - $0 >= config.minSpokenSpacing } ?? true
                if spacingOK {
                    events.append(TurnCueEvent(
                        kind: .approach, junctionId: next, symbol: symbol,
                        spoken: TurnCuePhrasing.planApproach(symbol: symbol, meters: ahead, units: config.units),
                        shortText: TurnCuePhrasing.near(symbol: symbol), distanceMeters: ahead))
                    st.stage = .headsUp
                    lastSpokenT = t
                }
            } else {
                st.stage = .headsUp
            }
        }
        if st.stage == .headsUp {
            if ahead <= dNear {
                st.nearCount += 1
                if st.nearCount >= 2 || ahead <= 10 {
                    events.append(TurnCueEvent(
                        kind: .atJunction, junctionId: next, symbol: symbol,
                        spoken: TurnCuePhrasing.near(symbol: symbol),
                        shortText: TurnCuePhrasing.near(symbol: symbol), distanceMeters: max(0, ahead)))
                    st.stage = .atTurn
                    lastSpokenT = t
                }
            } else {
                st.nearCount = 0
            }
        }
        return events
    }

    private func currentAdvisory() -> TurnCueAdvisory? {
        if degraded || isComplete { return nil }
        if offAnnounced {
            return TurnCueAdvisory(symbol: .offTrail, headline: TurnCuePhrasing.offRoute,
                                   distanceMeters: lineMeters, junctionId: nil)
        }
        let next = matcher.stepIndex + 1
        guard next < route.markers.count, turns[next].stage != .idle,
              let symbol = Self.symbol(for: route.markers[next].action) else { return nil }
        let ahead = route.markers[next].startMeters - matcher.cursorMeters
        return TurnCueAdvisory(symbol: symbol, headline: TurnCuePhrasing.near(symbol: symbol),
                               distanceMeters: max(0, ahead), junctionId: next)
    }

    // MARK: - Off route

    /// Same rule and numbers as the trail engine's off-trail warning, measured
    /// against the whole planned line.
    private mutating func offRouteStep(perp: Double, p: LocalPt, t: Double, tOff: Double, tOn: Double) -> [TurnCueEvent] {
        var events: [TurnCueEvent] = []
        if perp <= tOn {
            if onSince == nil { onSince = t }
            if !armed, let s = onSince, t - s >= config.armSeconds { armed = true }
            offSince = nil
            if offAnnounced {
                if backSince == nil { backSince = t }
                if let s = backSince, t - s >= config.backOnSeconds {
                    events.append(TurnCueEvent(
                        kind: .backOnTrail, junctionId: nil, symbol: .backOnTrail,
                        spoken: TurnCuePhrasing.backOnRoute, shortText: TurnCuePhrasing.backOnRoute,
                        distanceMeters: nil))
                    offAnnounced = false
                    backSince = nil
                }
            }
        } else {
            onSince = nil
            backSince = nil
            if perp > tOff {
                if offSince == nil {
                    offSince = t; offFixes = 0; offStart = p; offMaxMove = 0
                }
                offFixes += 1
                offMaxMove = max(offMaxMove, TurnCueGeometry.distance(p, offStart))
                if armed, let s = offSince, t - s >= config.offRouteSeconds,
                   offFixes >= config.offRouteFixes, offMaxMove >= config.offRouteMoveMeters {
                    var allowed = true
                    if let lt = lastOffT {
                        allowed = t - lt >= config.repeatSeconds && perp - lastOffPerp >= config.repeatFartherMeters
                    }
                    if allowed {
                        events.append(TurnCueEvent(
                            kind: .offTrail, junctionId: nil, symbol: .offTrail,
                            spoken: TurnCuePhrasing.offRoute, shortText: TurnCuePhrasing.offRoute,
                            distanceMeters: perp))
                        offAnnounced = true
                        lastOffT = t
                        lastOffPerp = perp
                    }
                }
            } else {
                offSince = nil
            }
        }
        return events
    }

    // MARK: - Helpers

    /// The cue for a step's action. Nil for the first step (nothing to turn).
    static func symbol(for action: PlanStep.Action) -> TurnCueSymbol? {
        switch action {
        case .start: return nil
        case .straight: return .straight
        case .keepLeft: return .keepLeft
        case .keepRight: return .keepRight
        case .left: return .turnLeft
        case .right: return .turnRight
        case .sharpLeft: return .sharpLeft
        case .sharpRight: return .sharpRight
        case .turnAround: return .turnAround
        }
    }

    /// Straight-line distance from `p` to the nearest point of the planned line.
    static func distanceToLine(_ p: LocalPt, _ route: PlanGuideRoute) -> Double {
        var best = Double.infinity
        for (a, b) in zip(route.points, route.points.dropFirst()) {
            best = min(best, TurnCueGeometry.projectOnSegment(p, a, b).dist)
        }
        return best
    }
}
