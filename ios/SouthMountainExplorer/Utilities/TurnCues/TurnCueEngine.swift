import Foundation

struct TurnCueFix: Sendable, Equatable {
    var lat: Double
    var lon: Double
    /// `CLLocation.timestamp.timeIntervalSince1970`.
    var timestamp: TimeInterval
    /// Metres; negative means invalid.
    var horizontalAccuracy: Double
    /// m/s, nil when invalid.
    var speed: Double?
}

struct TurnCueConfig: Sendable, Equatable {
    var units: UnitsPreference
    /// A fix worse than this is untrustworthy: the engine goes silent.
    var maxAcceptableAccuracy = 50.0
    /// A fix older than this (against `now`) is stale.
    var staleFixSeconds = 10.0
    /// Matches `GpsIngest.gapMs`: a discontinuity in the fix stream.
    var gapSeconds = GpsIngest.gapMs / 1000
    /// Min seconds between spoken cues (a near cue ignores it).
    var minSpokenSpacing = 6.0
    var weakAfterDegradedSeconds = 15.0
    var weakNeedsGoodSeconds = 30.0
    var weakMinIntervalSeconds = 300.0

    init(units: UnitsPreference) {
        self.units = units
    }
}

struct TurnCueEvent: Sendable, Equatable {
    enum Kind: Sendable, Equatable { case approach, atJunction, offTrail, backOnTrail, gpsWeak }
    let kind: Kind
    let junctionId: Int?
    let symbol: TurnCueSymbol
    /// Full sentence, already in the configured units.
    let spoken: String
    /// Banner headline.
    let shortText: String
    /// Distance to the junction when the event fired.
    let distanceMeters: Double?
}

/// Live banner state: the next cued junction ahead, or an off-trail notice.
struct TurnCueAdvisory: Sendable, Equatable {
    let symbol: TurnCueSymbol
    let headline: String
    let distanceMeters: Double?
    let junctionId: Int?
}

/// Pure, deterministic cue engine. Inputs are the route geometry, a stream
/// of fixes (with accuracy) and a config; outputs are `TurnCueEvent`s. It
/// knows nothing about voice, haptics or settings (see `TurnCueDispatcher`).
///
/// Field-trust rule: while the fix stream is degraded (bad accuracy, stale,
/// or a gap) the engine emits nothing except, at most, one `gpsWeak` notice
/// per episode.
struct TurnCueEngine: Sendable {
    enum Direction: Sendable, Equatable { case unknown, forward, backward }

    private enum Stage: Sendable { case idle, farFired, nearFired, passed }

    private struct JState: Sendable {
        var stage: Stage = .idle
        var nearCount = 0
        var cued = false
        var blockedUntil = -Double.infinity
        var lastCueT: Double?
    }

    private struct Cue {
        let symbol: TurnCueSymbol
    }

    static let maxFarMeters = 100.0

    let route: TurnCueRoute
    var config: TurnCueConfig

    private(set) var advisory: TurnCueAdvisory?
    private(set) var direction: Direction = .unknown
    /// Chain arc of the tracked position (metres), nil until acquired.
    private(set) var cursorArc: Double?
    private var cursorChain: Int?
    private var cursorT = 0.0
    private var unwrapped = 0.0
    private var peak = 0.0
    private var anchorU = 0.0
    private var anchorT = 0.0
    private var ring: [(u: Double, t: Double)] = []
    private var js: [Int: JState] = [:]

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

    // acquisition
    private var needsReacquire = true
    private var reacqCount = 0
    private var rejectCount = 0
    private var lastSpokenT: Double?

    // off trail
    private var onSince: Double?
    private var armed = false
    private var offSince: Double?
    private var offFixes = 0
    private var offStart = LocalPt(x: 0, y: 0)
    private var offMaxMove = 0.0
    private var offAnnounced = false
    private var lastOffT: Double?
    private var lastOffPerp = 0.0
    private var backSince: Double?

    init(route: TurnCueRoute, config: TurnCueConfig) {
        self.route = route
        self.config = config
    }

    mutating func reset() {
        self = TurnCueEngine(route: route, config: config)
    }

    /// True once the hiker is past the event's junction, or the notice no
    /// longer applies. Adapters drop stale cues instead of speaking them late.
    func isStale(_ event: TurnCueEvent) -> Bool {
        switch event.kind {
        case .approach, .atJunction:
            guard let id = event.junctionId else { return false }
            return js[id]?.stage == .passed
        case .offTrail:
            return !offAnnounced
        case .backOnTrail, .gpsWeak:
            return false
        }
    }

    // MARK: - Ingest

    mutating func ingest(_ fix: TurnCueFix, now: TimeInterval) -> [TurnCueEvent] {
        guard fix.lat.isFinite, fix.lon.isFinite, abs(fix.lat) <= 90, abs(fix.lon) <= 180,
              fix.timestamp.isFinite, now.isFinite, fix.timestamp <= now + 5,
              !route.isEmpty else { return [] }
        let t = fix.timestamp
        if let last = lastFixT, t <= last { return [] }

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
            return weakEventIfDue(at: t)
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
                return []
            }
        }

        return track(fix, acc: acc)
    }

    /// Call periodically (about 1 Hz) when no new fix arrived, so a stalled
    /// stream is noticed.
    mutating func tick(now: TimeInterval) -> [TurnCueEvent] {
        guard let last = lastFixT, now.isFinite else { return [] }
        if !degraded, now - last > config.staleFixSeconds {
            enterDegraded(at: last)
            advisory = nil
        }
        guard degraded else { return [] }
        return weakEventIfDue(at: now)
    }

    // MARK: - Quality

    private mutating func enterDegraded(at t: Double) {
        goodStreak = 0
        needsReacquire = true
        reacqCount = 0
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

    private struct GlobalProj {
        var chain: Int
        var arc: Double
        var perp: Double
    }

    private func globalProjection(_ p: LocalPt) -> GlobalProj? {
        var best: GlobalProj?
        for (ci, chain) in route.chains.enumerated() {
            if let r = chain.project(p), best == nil || r.perpendicular < best!.perp {
                best = GlobalProj(chain: ci, arc: r.arc, perp: r.perpendicular)
            }
        }
        return best
    }

    private func arcDelta(from a: Double, to b: Double, chain: TurnCueRoute.Chain) -> Double {
        var d = b - a
        if chain.isLoop {
            let l = chain.length
            if d > l / 2 { d -= l } else if d < -l / 2 { d += l }
        }
        return d
    }

    private mutating func track(_ fix: TurnCueFix, acc: Double) -> [TurnCueEvent] {
        let t = fix.timestamp
        let p = route.origin.local(lat: fix.lat, lon: fix.lon)
        guard let g = globalProjection(p) else { return [] }
        let tOff = max(40, 2 * acc + 15)
        let tOn = 0.6 * tOff

        var events = offTrailStep(g: g, p: p, t: t, tOff: tOff, tOn: tOn)

        // Acquisition.
        if needsReacquire || cursorChain == nil {
            if g.perp <= tOn { reacqCount += 1 } else { reacqCount = 0 }
            if reacqCount >= 3 {
                acquire(g, t: t)
            } else {
                advisory = currentAdvisory()
                return events
            }
        } else if let ci = cursorChain, let cur = cursorArc {
            let chain = route.chains[ci]
            let dt = max(0.5, t - cursorT)
            let v = smoothedSpeed(fix)
            let lo = cur - 40
            let hi = cur + max(150, v * dt * 2 + 3 * acc)
            let w = chain.project(p, window: lo...hi)
            if let w, w.perpendicular <= tOff {
                reacqCount = 0
                let delta = arcDelta(from: cur, to: w.arc, chain: chain)
                let allowed = 6 * dt + max(acc, 8)
                if abs(delta) > allowed {
                    rejectCount += 1
                    if rejectCount >= 5 { needsReacquire = true; reacqCount = 0; rejectCount = 0 }
                } else {
                    rejectCount = 0
                    cursorArc = w.arc
                    cursorT = t
                    unwrapped += delta
                    updateDirection(delta: delta, t: t, acc: acc)
                }
            } else {
                // The window finds nothing near: hold the cursor, and
                // re-acquire only if a different part of the trail is clearly near.
                if g.perp <= tOn { reacqCount += 1 } else { reacqCount = 0 }
                if reacqCount >= 3 { acquire(g, t: t) }
            }
        }

        // Junction cues only while tracked on the trail.
        if let ci = cursorChain, let cur = cursorArc, direction != .unknown {
            let chain = route.chains[ci]
            let onTrail = chain.project(p, window: (cur - 40)...(cur + 150)).map { $0.perpendicular <= tOff } ?? false
            if onTrail {
                events.append(contentsOf: evaluateJunctions(chainIndex: ci, cur: cur, t: t, acc: acc, fix: fix))
            }
        }
        advisory = currentAdvisory()
        return events
    }

    private mutating func acquire(_ g: GlobalProj, t: Double) {
        let chain = route.chains[g.chain]
        if let ci = cursorChain, ci == g.chain, let cur = cursorArc {
            unwrapped += arcDelta(from: cur, to: g.arc, chain: chain)
        } else {
            unwrapped = g.arc
        }
        cursorChain = g.chain
        cursorArc = g.arc
        cursorT = t
        peak = unwrapped
        anchorU = unwrapped
        anchorT = t
        ring.removeAll()
        needsReacquire = false
        reacqCount = 0
        rejectCount = 0

        // Silently retire junctions the hiker is already at or past.
        for j in route.junctions where j.chain == g.chain {
            var st = js[j.id] ?? JState()
            let d = arcDelta(from: g.arc, to: j.arc, chain: chain)
            let retire: Bool
            switch direction {
            case .forward: retire = d < 5
            case .backward: retire = -d < 5
            case .unknown: retire = abs(d) <= 20
            }
            if retire { st.stage = .passed }
            js[j.id] = st
        }
    }

    private func smoothedSpeed(_ fix: TurnCueFix) -> Double {
        let window = ring.filter { fix.timestamp - $0.t <= 10 }
        if let first = window.first, let last = window.last, last.t - first.t >= 3 {
            let v = abs(last.u - first.u) / (last.t - first.t)
            return max(0.8, min(3.0, v))
        }
        if let s = fix.speed, s.isFinite, s > 0 { return max(0.8, min(3.0, s)) }
        return 1.3
    }

    private mutating func updateDirection(delta: Double, t: Double, acc: Double) {
        ring.append((unwrapped, t))
        ring.removeAll { t - $0.t > 40 }
        switch direction {
        case .unknown:
            let net = unwrapped - anchorU
            // Threshold scales with accuracy so position noise alone can never
            // pick a direction.
            if abs(net) >= max(20, 2.5 * acc), ring.count >= 4 {
                direction = net > 0 ? .forward : .backward
                peak = unwrapped
            } else if t - anchorT > 90 {
                anchorU = unwrapped; anchorT = t
            }
        case .forward:
            peak = max(peak, unwrapped)
            if peak - unwrapped >= max(30, 3 * acc) { flip(to: .backward, t: t) }
        case .backward:
            peak = min(peak, unwrapped)
            if unwrapped - peak >= max(30, 3 * acc) { flip(to: .forward, t: t) }
        }
    }

    private mutating func flip(to newDir: Direction, t: Double) {
        direction = newDir
        peak = unwrapped
        for (id, st) in js {
            var s = st
            s.stage = .idle
            s.nearCount = 0
            s.cued = false
            if let l = s.lastCueT, t - l < 60 { s.blockedUntil = l + 60 }
            js[id] = s
        }
    }

    // MARK: - Junction cues

    private func classify(_ j: TurnCueJunction, direction: Direction) -> Cue? {
        let fwd = direction == .forward
        guard let sel = fwd ? j.selectedBearingForward : j.selectedBearingBackward,
              let approach = fwd ? j.selectedBearingBackward : j.selectedBearingForward else { return nil }
        let heading = TurnCueGeometry.normalize360(approach + 180)
        let thSel = TurnCueGeometry.signedAngleDiff(from: heading, to: sel)
        let thB = j.branches.map { TurnCueGeometry.signedAngleDiff(from: heading, to: $0.bearing) }
        if abs(thSel) >= 35 {
            guard thB.contains(where: { abs($0) <= 150 }) else { return nil }
            let sharp = abs(thSel) >= 110
            if thSel > 0 { return Cue(symbol: sharp ? .sharpRight : .turnRight) }
            return Cue(symbol: sharp ? .sharpLeft : .turnLeft)
        }
        let ahead = thB.filter { abs($0) <= 100 }
        guard let b = ahead.min(by: { abs($0 - thSel) < abs($1 - thSel) }) else { return nil }
        let diff = abs(b - thSel)
        guard diff >= 20, diff < 60 else { return nil }
        return Cue(symbol: thSel < b ? .keepLeft : .keepRight)
    }

    private func aheadDistance(_ j: TurnCueJunction, cur: Double, chain: TurnCueRoute.Chain) -> Double {
        var a = direction == .forward ? j.arc - cur : cur - j.arc
        if chain.isLoop {
            let l = chain.length
            a = a.truncatingRemainder(dividingBy: l)
            if a < 0 { a += l }
            if a > l - 20 { a -= l }
        }
        return a
    }

    private mutating func evaluateJunctions(
        chainIndex: Int, cur: Double, t: Double, acc: Double, fix: TurnCueFix
    ) -> [TurnCueEvent] {
        let chain = route.chains[chainIndex]
        let v = smoothedSpeed(fix)
        let dFar = max(50, min(Self.maxFarMeters, v * 40 + min(acc, 30)))
        let dNear = max(15, min(30, v * 8 + acc))

        var items: [(j: TurnCueJunction, cue: Cue, ahead: Double)] = []
        for j in route.junctions where j.chain == chainIndex {
            guard let cue = classify(j, direction: direction) else { continue }
            items.append((j, cue, aheadDistance(j, cur: cur, chain: chain)))
        }
        items.sort { $0.ahead < $1.ahead }

        var events: [TurnCueEvent] = []
        for item in items {
            var st = js[item.j.id] ?? JState()
            defer { js[item.j.id] = st }
            let ahead = item.ahead
            if ahead < -20 { st.stage = .passed; continue }
            if st.stage == .passed { continue }
            if t < st.blockedUntil { continue }

            if st.stage == .idle, ahead <= dFar {
                let closeAfterCued = items.contains {
                    $0.j.id != item.j.id
                        && (js[$0.j.id]?.cued ?? false)
                        && ($0.ahead > -20 && $0.ahead < ahead && ahead - $0.ahead < 60)
                }
                let spacingOK = lastSpokenT.map { t - $0 >= config.minSpokenSpacing } ?? true
                if ahead > dNear + 10, !closeAfterCued {
                    if spacingOK {
                        events.append(TurnCueEvent(
                            kind: .approach, junctionId: item.j.id, symbol: item.cue.symbol,
                            spoken: TurnCuePhrasing.approach(
                                symbol: item.cue.symbol, meters: ahead,
                                trailName: route.trailName, units: config.units
                            ),
                            shortText: TurnCuePhrasing.near(symbol: item.cue.symbol),
                            distanceMeters: ahead
                        ))
                        st.stage = .farFired
                        st.cued = true
                        st.lastCueT = t
                        lastSpokenT = t
                    }
                } else {
                    st.stage = .farFired
                }
            }
            if st.stage == .farFired {
                // Re-arm only when clearly turned around: use the largest possible
                // far distance, not the speed-dependent one, so a noisy speed
                // estimate can't flip the stage back and refire.
                if ahead > Self.maxFarMeters + 30 {
                    st.stage = .idle
                    st.nearCount = 0
                } else if ahead <= dNear {
                    st.nearCount += 1
                    if st.nearCount >= 2 || ahead <= 10 {
                        events.append(TurnCueEvent(
                            kind: .atJunction, junctionId: item.j.id, symbol: item.cue.symbol,
                            spoken: TurnCuePhrasing.near(symbol: item.cue.symbol),
                            shortText: TurnCuePhrasing.near(symbol: item.cue.symbol),
                            distanceMeters: max(0, ahead)
                        ))
                        st.stage = .nearFired
                        st.cued = true
                        st.lastCueT = t
                        lastSpokenT = t
                    }
                } else {
                    st.nearCount = 0
                }
            }
        }
        return events
    }

    private func currentAdvisory() -> TurnCueAdvisory? {
        if degraded { return nil }
        if offAnnounced {
            return TurnCueAdvisory(
                symbol: .offTrail, headline: TurnCuePhrasing.offTrail(trailName: route.trailName),
                distanceMeters: nil, junctionId: nil
            )
        }
        guard let ci = cursorChain, let cur = cursorArc, direction != .unknown else { return nil }
        let chain = route.chains[ci]
        var best: (j: TurnCueJunction, cue: Cue, ahead: Double)?
        for j in route.junctions where j.chain == ci {
            guard let st = js[j.id], st.cued, st.stage == .farFired || st.stage == .nearFired,
                  let cue = classify(j, direction: direction) else { continue }
            let a = aheadDistance(j, cur: cur, chain: chain)
            if a >= -20, best == nil || a < best!.ahead { best = (j, cue, a) }
        }
        guard let b = best else { return nil }
        return TurnCueAdvisory(
            symbol: b.cue.symbol, headline: TurnCuePhrasing.near(symbol: b.cue.symbol),
            distanceMeters: max(0, b.ahead), junctionId: b.j.id
        )
    }

    // MARK: - Off trail

    private mutating func offTrailStep(
        g: GlobalProj, p: LocalPt, t: Double, tOff: Double, tOn: Double
    ) -> [TurnCueEvent] {
        var events: [TurnCueEvent] = []
        if g.perp <= tOn {
            if onSince == nil { onSince = t }
            if !armed, let s = onSince, t - s >= 10 { armed = true }
            offSince = nil
            if offAnnounced {
                if backSince == nil { backSince = t }
                if let s = backSince, t - s >= 6 {
                    events.append(TurnCueEvent(
                        kind: .backOnTrail, junctionId: nil, symbol: .backOnTrail,
                        spoken: TurnCuePhrasing.backOnTrail(trailName: route.trailName),
                        shortText: TurnCuePhrasing.backOnTrail(trailName: route.trailName),
                        distanceMeters: nil
                    ))
                    offAnnounced = false
                    backSince = nil
                }
            }
        } else {
            onSince = nil
            backSince = nil
            if g.perp > tOff {
                if offSince == nil {
                    offSince = t; offFixes = 0; offStart = p; offMaxMove = 0
                }
                offFixes += 1
                offMaxMove = max(offMaxMove, TurnCueGeometry.distance(p, offStart))
                let chain = route.chains[g.chain]
                let atEnd = !chain.isLoop && (g.arc <= 0.5 || g.arc >= chain.length - 0.5)
                if armed, let s = offSince, t - s >= 20, offFixes >= 3, offMaxMove >= 10, !atEnd {
                    var allowed = true
                    if let lt = lastOffT {
                        allowed = t - lt >= 120 && g.perp - lastOffPerp >= 50
                    }
                    if allowed {
                        events.append(TurnCueEvent(
                            kind: .offTrail, junctionId: nil, symbol: .offTrail,
                            spoken: TurnCuePhrasing.offTrail(trailName: route.trailName),
                            shortText: TurnCuePhrasing.offTrail(trailName: route.trailName),
                            distanceMeters: nil
                        ))
                        offAnnounced = true
                        lastOffT = t
                        lastOffPerp = g.perp
                    }
                }
            } else {
                offSince = nil
            }
        }
        return events
    }
}
