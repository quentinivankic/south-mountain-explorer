import Foundation

// "Plan a Hike", Release B task 2: where you are along the planned walk, and
// which numbered step you are on. Forward only: a cursor in metres along the
// `PlanGuideRoute` that never follows you backward, so the way back of an out
// and back (same ground, later distance) can never be mistaken for the way
// out. Pure, no CoreLocation. The guide (task 3) decides which fixes are
// trusted and owns the off-route warning; this only tracks progress.

/// Forward-only progress along a planned walk.
///
/// Each fix is matched only inside a window from a little behind the cursor
/// to a little ahead of where you could have walked since the last fix. Inside
/// that window the earliest good match at or ahead of the cursor wins, never
/// the global nearest point. A turn-around step closes the line past it until
/// the cursor has reached it. Off the expected stretch, a later part of the
/// plan is accepted only after steady forward progress along it (a shortcut).
struct PlanProgressMatcher: Sendable, Equatable {
    struct Config: Sendable, Equatable {
        /// The window starts this far behind the cursor. Backward drift
        /// inside it is ignored.
        var behindMeters = 15.0
        /// The window reaches at least this far ahead of the cursor.
        var minAheadMeters = 150.0
        /// Speed used for the window when the fix has none (m/s).
        var walkingSpeed = 1.5
        /// A match this much farther than the closest one still counts, so
        /// the earlier of two passes over the same ground wins.
        var tieMeters = 5.0
        /// One fix can move the cursor at most this speed times the time
        /// since the last fix, plus the fix's accuracy (as the trail engine).
        var maxSpeed = 6.0
        /// The line past a turn-around step opens once the cursor is this
        /// close to it.
        var turnAroundGateMeters = 25.0
        /// The step changes once the cursor is this far past its start.
        var stepSwitchMeters = 10.0
        /// A fix farther than this from the line (or 2x accuracy plus 15 m
        /// if larger) is off the expected stretch. Same numbers as off route.
        var matchMeters = GpsTrustPolicy.offLineMeters
        /// Fixes worse than this are not used (the trail engine's limit).
        var maxAcceptableAccuracy = GpsTrustPolicy.maxAcceptableAccuracy
        /// Skipping ahead needs this many fixes near a later part of the plan...
        var skipFixes = 3
        /// ...and this much forward progress along it.
        var skipProgressMeters = 30.0

        init() {}
    }

    /// What one fix did.
    struct Update: Sendable, Equatable {
        enum Kind: Sendable, Equatable {
            /// Not used: invalid or poor accuracy, or older than the last fix.
            case ignored
            /// On the expected stretch of the plan.
            case matched
            /// Too far from the expected stretch (a wrong turn, walking a leg
            /// backward, or a shortcut not yet confirmed).
            case offExpected
            /// Jumped forward to a later part of the plan, skipping steps.
            case skippedAhead
        }
        let kind: Kind
        /// The current step changed with this fix.
        let stepChanged: Bool
    }

    private struct SkipRun: Sendable, Equatable {
        var startMeters: Double
        var lastMeters: Double
        var count: Int
    }

    let route: PlanGuideRoute
    let config: Config
    /// Metres along the route. Only moves forward, except through `move(toStep:)`.
    private(set) var cursorMeters: Double = 0
    /// 0-based index into `route.markers`.
    private(set) var stepIndex = 0
    private var lastFixTime: TimeInterval?
    private var skip: SkipRun?

    init(route: PlanGuideRoute, config: Config = Config()) {
        self.route = route
        self.config = config
    }

    var currentMarker: PlanGuideRoute.Marker { route.markers[stepIndex] }

    /// Back and Next: put the guide at the start of step `index`. Auto
    /// tracking continues from there; if you are still behind it, it waits.
    mutating func move(toStep index: Int) {
        let i = max(0, min(index, route.markers.count - 1))
        stepIndex = i
        cursorMeters = route.markers[i].startMeters
        skip = nil
    }

    mutating func ingest(_ fix: TurnCueFix) -> Update {
        let acc = fix.horizontalAccuracy
        guard acc >= 0, acc <= config.maxAcceptableAccuracy else { return Update(kind: .ignored, stepChanged: false) }
        if let last = lastFixTime, fix.timestamp < last { return Update(kind: .ignored, stepChanged: false) }
        let dt = lastFixTime.map { fix.timestamp - $0 }
        lastFixTime = fix.timestamp
        let p = route.local(lat: fix.lat, lon: fix.lon)
        let before = stepIndex
        let gate = gateMeters
        let limit = max(config.matchMeters, 2 * acc + 15)
        let cap = dt.map { config.maxSpeed * $0 + acc } ?? .infinity
        let speed = max(fix.speed ?? 0, config.walkingSpeed)
        let ahead = max(config.minAheadMeters, 2 * speed * (dt ?? 0) + 3 * acc)
        let lo = max(0, cursorMeters - config.behindMeters)
        let hi = min(gate, cursorMeters + ahead)

        if let m = match(p, from: lo, to: hi), m.dist <= limit {
            skip = nil
            if m.arc > cursorMeters { cursorMeters += min(m.arc - cursorMeters, cap) }
            updateStep()
            return Update(kind: .matched, stepChanged: stepIndex != before)
        }

        // Off the expected stretch: is this a later part of the plan (before
        // the next closed turn-around)?
        guard hi < gate, let m = match(p, from: hi, to: gate), m.dist <= limit else {
            return Update(kind: .offExpected, stepChanged: false)
        }
        if var run = skip, m.arc >= run.lastMeters - config.behindMeters, m.arc - run.lastMeters <= cap {
            run.count += 1
            run.lastMeters = max(run.lastMeters, m.arc)
            skip = run
        } else {
            skip = SkipRun(startMeters: m.arc, lastMeters: m.arc, count: 1)
        }
        guard let run = skip, run.count >= config.skipFixes,
              run.lastMeters - run.startMeters >= config.skipProgressMeters else {
            return Update(kind: .offExpected, stepChanged: false)
        }
        skip = nil
        cursorMeters = run.lastMeters
        updateStep()
        return Update(kind: .skippedAhead, stepChanged: stepIndex != before)
    }

    // MARK: - Internals

    /// The line is closed past the first turn-around step the cursor has not
    /// yet come within `turnAroundGateMeters` of.
    private var gateMeters: Double {
        route.markers.first {
            $0.isTurnAround && $0.startMeters > cursorMeters + config.turnAroundGateMeters
        }?.startMeters ?? route.length
    }

    private mutating func updateStep() {
        let s = route.markerIndex(at: cursorMeters - config.stepSwitchMeters)
        if s > stepIndex { stepIndex = s }
    }

    /// The match for `p` on the line between `lo` and `hi` metres: the
    /// earliest local closest point at or ahead of the cursor within
    /// `tieMeters` of the closest one, else the closest one.
    private func match(_ p: LocalPt, from lo: Double, to hi: Double) -> (arc: Double, dist: Double)? {
        let c = projections(p, from: lo, to: hi)
        guard let best = c.min(by: { $0.dist < $1.dist }) else { return nil }
        for k in c.indices {
            let d = c[k].dist
            let isLocalMin = (k == 0 || d <= c[k - 1].dist) && (k == c.count - 1 || d <= c[k + 1].dist)
            if isLocalMin, c[k].arc >= cursorMeters, d <= best.dist + config.tieMeters { return c[k] }
        }
        return best
    }

    /// `p` projected onto each piece of the line between `lo` and `hi`, in
    /// walking order. The piece holding the cursor is split there.
    private func projections(_ p: LocalPt, from lo: Double, to hi: Double) -> [(arc: Double, dist: Double)] {
        let pts = route.points, cum = route.cumulative
        guard hi >= lo, pts.count >= 2 else { return [] }
        // Last segment starting at or before `lo`.
        var a = 0, b = cum.count - 2
        while a < b {
            let mid = (a + b + 1) / 2
            if cum[mid] <= lo { a = mid } else { b = mid - 1 }
        }
        var out: [(arc: Double, dist: Double)] = []
        var i = a
        while i < pts.count - 1, cum[i] <= hi {
            let s0 = max(lo, cum[i]), s1 = min(hi, cum[i + 1])
            if s1 >= s0 {
                let cuts = cursorMeters > s0 && cursorMeters < s1 ? [s0, cursorMeters, s1] : [s0, s1]
                for (u, v) in zip(cuts, cuts.dropFirst()) {
                    let pu = Self.lerp(pts[i], pts[i + 1], cum[i], cum[i + 1], u)
                    let pv = Self.lerp(pts[i], pts[i + 1], cum[i], cum[i + 1], v)
                    let (t, d) = TurnCueGeometry.projectOnSegment(p, pu, pv)
                    out.append((u + (v - u) * t, d))
                }
            }
            i += 1
        }
        return out
    }

    private static func lerp(_ a: LocalPt, _ b: LocalPt, _ ca: Double, _ cb: Double, _ s: Double) -> LocalPt {
        let t = cb > ca ? (s - ca) / (cb - ca) : 0
        return LocalPt(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t)
    }
}
