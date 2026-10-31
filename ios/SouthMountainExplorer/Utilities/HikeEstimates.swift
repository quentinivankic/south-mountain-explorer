import Foundation

// Mid-hike completion estimates: "Remaining" (finish the selected trail) and
// "Back" (return to where the hike started). Everything here is a pure,
// deterministic, Sendable value type: callers inject `now` and the last-fix
// time, nothing reads a clock, location service or UI.

/// Codable/Hashable/Sendable mirror of `HikeEstimate`, safe to put in an
/// ActivityKit ContentState.
struct HikeEstimateSnapshot: Codable, Hashable, Sendable {
    var distanceMeters: Double?
    var durationSeconds: Double?
    var etaDate: Date?
    /// "estimating", "ok", "approximate", "stale", "offTrail", "arrived", "unavailable".
    var statusKey: String
    var targetName: String?
}

struct HikeEstimate: Equatable, Sendable {
    enum Status: Equatable, Sendable {
        case estimating
        case ok
        /// Number includes a straight chord across a GPS gap.
        case approximate
        /// Last good value kept while GPS is degraded.
        case stale
        case offTrail(rejoinMeters: Double)
        /// Remaining: at the end of the trail. Back: at the start.
        case arrived
        case unavailable

        var key: String {
            switch self {
            case .estimating: return "estimating"
            case .ok: return "ok"
            case .approximate: return "approximate"
            case .stale: return "stale"
            case .offTrail: return "offTrail"
            case .arrived: return "arrived"
            case .unavailable: return "unavailable"
            }
        }
    }

    var distanceMeters: Double?
    var durationSeconds: TimeInterval?
    var status: Status
    /// Remaining: the trail name. Back: nil.
    var targetName: String?
    var computedAt: Date

    /// Clock time of arrival if the hiker keeps moving, as of `computedAt`.
    var etaDate: Date? { durationSeconds.map { computedAt.addingTimeInterval($0) } }

    var snapshot: HikeEstimateSnapshot {
        HikeEstimateSnapshot(distanceMeters: distanceMeters, durationSeconds: durationSeconds,
                             etaDate: etaDate, statusKey: status.key, targetName: targetName)
    }

    /// True when the numbers carry a usable value.
    var hasValue: Bool { durationSeconds != nil && distanceMeters != nil }
}

struct HikeEstimates: Equatable, Sendable {
    var remaining: HikeEstimate?
    var back: HikeEstimate?
    var computedAt: Date
}

// MARK: - Estimator

/// Stateful (value-type) estimator. Feed it the recording path each tick.
struct HikeEstimator: Equatable, Sendable {
    static let backRecomputeSeconds: TimeInterval = 5
    static let minWalkedMeters = 100.0
    static let minMovingSecondsForRemaining = 90.0
    static let arrivedMeters = 15.0

    private var progress = TrailProgress()
    private var lastGood: HikeEstimates? = nil
    private var backCache: HikeEstimate? = nil
    private var backCachedAt: Date? = nil

    init() {}

    /// - Parameters:
    ///   - geometry: the selected trail, nil for roam recordings (no Remaining).
    ///   - lastFixAt: time of the last fresh GPS fix; older than
    ///     `GpsIngest.staleFixSeconds` returns the last good value marked stale.
    mutating func update(path: [GpsPoint], geometry: TrailGeometry?, now: Date,
                         lastFixAt: Date?) -> HikeEstimates {
        if let lastFixAt, now.timeIntervalSince(lastFixAt) > GpsIngest.staleFixSeconds {
            return staleVersion(now: now, hasTrail: geometry != nil)
        }
        let pace = PaceModel.evaluate(path: path)
        let walked = Self.walkedMeters(path)

        var remaining: HikeEstimate? = nil
        if let g = geometry {
            progress.ingest(path: path, geometry: g)
            remaining = Self.remaining(progress: progress, geometry: g, pace: pace,
                                       walked: walked, now: now)
        }

        if backCache == nil || backCachedAt == nil
            || now.timeIntervalSince(backCachedAt!) >= Self.backRecomputeSeconds
            || now < backCachedAt! {
            backCache = Self.back(path: path, geometry: geometry, progress: progress,
                                  pace: pace, now: now)
            backCachedAt = now
        }
        let result = HikeEstimates(remaining: remaining, back: backCache, computedAt: now)
        lastGood = result
        return result
    }

    private func staleVersion(now: Date, hasTrail: Bool) -> HikeEstimates {
        func stale(_ e: HikeEstimate?) -> HikeEstimate? {
            guard var e else { return nil }
            if e.status == .ok || e.status == .approximate { e.status = .stale }
            return e
        }
        if let g = lastGood {
            return HikeEstimates(remaining: stale(g.remaining), back: stale(g.back), computedAt: g.computedAt)
        }
        let est = HikeEstimate(distanceMeters: nil, durationSeconds: nil, status: .estimating,
                               targetName: nil, computedAt: now)
        return HikeEstimates(remaining: hasTrail ? est : nil, back: est, computedAt: now)
    }

    // MARK: Remaining

    private static func remaining(progress: TrailProgress, geometry g: TrailGeometry,
                                  pace: PaceModel.Result, walked: Double, now: Date) -> HikeEstimate {
        func make(_ status: HikeEstimate.Status, _ d: Double? = nil, _ t: Double? = nil) -> HikeEstimate {
            HikeEstimate(distanceMeters: d, durationSeconds: t, status: status,
                         targetName: g.name, computedAt: now)
        }
        if progress.isOff { return make(.offTrail(rejoinMeters: progress.rejoinDistance)) }
        guard let s = progress.s, progress.direction != 0,
              walked >= minWalkedMeters, pace.movingSeconds >= minMovingSecondsForRemaining,
              let left = progress.remainingMeters(in: g)
        else { return make(.estimating) }
        if left <= arrivedMeters { return make(.arrived, 0, 0) }
        let secs = g.seconds(from: s, direction: progress.direction, length: left,
                             factor: pace.effectiveFactor)
        return make(.ok, left, secs)
    }

    // MARK: Back

    private static func back(path: [GpsPoint], geometry g: TrailGeometry?, progress: TrailProgress,
                             pace: PaceModel.Result, now: Date) -> HikeEstimate {
        func make(_ status: HikeEstimate.Status, _ d: Double? = nil, _ t: Double? = nil) -> HikeEstimate {
            HikeEstimate(distanceMeters: d, durationSeconds: t, status: status,
                         targetName: nil, computedAt: now)
        }
        let route = BackRoute.build(path: path)
        guard route.walked >= minWalkedMeters, route.xs.count >= 2 else { return make(.estimating) }
        let retrace = route.length
        // Loop erasure needs 60 m of path to close, so judge "back at the start"
        // by where we are, not by the erased length.
        let dx = route.xs[route.xs.count - 1] - route.xs[0]
        let dy = route.ys[route.ys.count - 1] - route.ys[0]
        if (dx * dx + dy * dy).squareRoot() <= 20 || retrace <= arrivedMeters {
            return make(.arrived, 0, 0)
        }
        var distance = retrace
        var secs = route.seconds(factor: pace.effectiveFactor)
        var approximate = route.hasGapLegs
        // Loop shortcut: on the selected loop trail the whole way, carrying on
        // to the start can beat retracing.
        if let g, g.isLoop, progress.startedOnTrail, !progress.everOff, progress.direction != 0,
           let s = progress.s, let ring = progress.remainingMeters(in: g), ring < retrace {
            distance = ring
            secs = g.seconds(from: s, direction: progress.direction, length: ring,
                             factor: pace.effectiveFactor)
            approximate = false
        }
        return make(approximate ? .approximate : .ok, distance, secs)
    }

    /// Sum of continuous (non-gap) step lengths of the path.
    static func walkedMeters(_ path: [GpsPoint]) -> Double {
        var total = 0.0
        var prev: GpsPoint? = nil
        for p in path {
            guard p.count >= 3 else { continue }
            if let q = prev, !GpsIngest.isGap(prev: q, p: p) {
                total += MapMath.haversineMeters(lat1: q[0], lon1: q[1], lat2: p[0], lon2: p[1])
            }
            prev = p
        }
        return total
    }
}

/// The way back: the walked track with spurs and loops erased, from the start
/// to the current position. Never a straight line except across a GPS gap
/// (then flagged `hasGapLegs`).
struct BackRoute: Equatable, Sendable {
    static let resampleMeters = 10.0
    static let closeMeters = 15.0
    static let minLoopMeters = 60.0

    /// Local meters, start -> current position, loop-erased.
    private(set) var xs: [Double] = []
    private(set) var ys: [Double] = []
    private(set) var alts: [Double?] = []
    private(set) var cum: [Double] = []
    private(set) var hasGapLegs = false
    /// Metres actually walked (continuous steps, before erasing).
    private(set) var walked = 0.0

    var length: Double { cum.last ?? 0 }

    static func build(path: [GpsPoint]) -> BackRoute {
        var r = BackRoute()
        guard let first = path.first(where: { $0.count >= 3 }) else { return r }
        let lat0 = first[0], lon0 = first[1]
        let k = TrailGeometry.metersPerDegree
        let cosLat = cos(lat0 * .pi / 180)
        func xy(_ p: GpsPoint) -> (Double, Double) { ((p[1] - lon0) * k * cosLat, (p[0] - lat0) * k) }

        // 1. Resample to ~10 m, remembering which points start a new run.
        var rx: [Double] = [], ry: [Double] = [], ra: [Double?] = [], chord: [Bool] = []
        var prev: GpsPoint? = nil
        var lastEmit: (Double, Double)? = nil
        var pendingChord = false
        let valid = path.filter { $0.count >= 3 }
        for (idx, p) in valid.enumerated() {
            let (x, y) = xy(p)
            var startsRun = false
            if let q = prev, GpsIngest.isGap(prev: q, p: p) { startsRun = true; pendingChord = true }
            else if let q = prev {
                let (qx, qy) = xy(q)
                r.walked += ((x - qx) * (x - qx) + (y - qy) * (y - qy)).squareRoot()
            }
            prev = p
            let isLast = idx == valid.count - 1
            var emit = rx.isEmpty || startsRun || isLast
            if !emit, let l = lastEmit {
                let d = ((x - l.0) * (x - l.0) + (y - l.1) * (y - l.1)).squareRoot()
                emit = d >= resampleMeters
            }
            if emit {
                if let l = lastEmit, !startsRun, !rx.isEmpty,
                   ((x - l.0) * (x - l.0) + (y - l.1) * (y - l.1)).squareRoot() < 0.5 { continue }
                rx.append(x); ry.append(y); ra.append(p.altitudeMeters)
                chord.append(pendingChord && !rx.isEmpty && rx.count > 1)
                pendingChord = false
                lastEmit = (x, y)
            }
        }
        guard rx.count >= 2 else { return r }

        // 2. Loop-erase with a spatial hash.
        let cell = closeMeters
        var grid: [Int64: [Int]] = [:]
        func key(_ x: Double, _ y: Double) -> Int64 {
            let cx = Int64((x / cell).rounded(.down)), cy = Int64((y / cell).rounded(.down))
            return (cx << 32) ^ (cy & 0xFFFF_FFFF)
        }
        func keyAt(_ cx: Int64, _ cy: Int64) -> Int64 { (cx << 32) ^ (cy & 0xFFFF_FFFF) }
        var ox: [Double] = [], oy: [Double] = [], oa: [Double?] = [], oc: [Double] = [], och: [Bool] = []
        for i in 0..<rx.count {
            let x = rx[i], y = ry[i]
            var match: Int? = nil
            if let endCum = oc.last {
                let cx = Int64((x / cell).rounded(.down)), cy = Int64((y / cell).rounded(.down))
                for dx in -1...1 {
                    for dy in -1...1 {
                        guard let list = grid[keyAt(cx + Int64(dx), cy + Int64(dy))] else { continue }
                        for j in list where endCum - oc[j] >= minLoopMeters {
                            let d = ((x - ox[j]) * (x - ox[j]) + (y - oy[j]) * (y - oy[j])).squareRoot()
                            if d <= closeMeters, match == nil || j < match! { match = j }
                        }
                    }
                }
            }
            var isChord = chord[i]
            if let m = match {
                while ox.count - 1 > m {
                    let j = ox.count - 1
                    grid[key(ox[j], oy[j])]?.removeLast()
                    ox.removeLast(); oy.removeLast(); oa.removeLast(); oc.removeLast(); och.removeLast()
                }
                isChord = false
            }
            var c = 0.0
            if let lx = ox.last, let ly = oy.last, let lc = oc.last {
                c = lc + ((x - lx) * (x - lx) + (y - ly) * (y - ly)).squareRoot()
            }
            grid[key(x, y), default: []].append(ox.count)
            ox.append(x); oy.append(y); oa.append(ra[i]); oc.append(c); och.append(isChord)
        }
        r.xs = ox; r.ys = oy; r.alts = oa; r.cum = oc
        r.hasGapLegs = och.dropFirst().contains(true)
        return r
    }

    /// Seconds to walk the route back (reversed grade), 50 m chunks.
    func seconds(factor: Double) -> Double {
        guard xs.count >= 2 else { return 0 }
        var t = 0.0
        var j = xs.count - 1
        while j > 0 {
            var i = j
            while i > 0 && cum[j] - cum[i] < PaceModel.chunkMeters { i -= 1 }
            let len = cum[j] - cum[i]
            var grade = 0.0
            if len >= PaceModel.chunkMeters / 2, let aj = alts[j], let ai = alts[i] {
                grade = (ai - aj) / len      // walking from j back to i
            }
            t += PaceModel.seconds(length: len, grade: grade, factor: factor)
            j = i
        }
        return t
    }
}

// MARK: - Text

/// User-facing strings for an estimate, pure so they can be unit tested.
enum HikeEstimateText {
    enum Kind { case remaining, back }

    static func label(_ kind: Kind) -> String { kind == .remaining ? "Remaining" : "Back" }

    /// "to finish National Trail" / "to return to start".
    static func target(_ kind: Kind, trailName: String?) -> String {
        switch kind {
        case .remaining: return trailName.map { "to finish \($0)" } ?? "to finish the trail"
        case .back: return "to return to start"
        }
    }

    /// First line: "Remaining 24 min", "Back · estimating…".
    static func primary(_ e: HikeEstimate?, kind: Kind) -> String {
        let name = label(kind)
        guard let e else { return "\(name) · estimating…" }
        switch e.status {
        case .estimating, .unavailable: return "\(name) · estimating…"
        case .offTrail: return "\(name) · off trail"
        case .arrived: return "\(name) · " + (kind == .remaining ? "at the end" : "at the start")
        case .ok, .stale:
            return "\(name) \(UnitFormatter.duration(seconds: e.durationSeconds ?? 0))"
        case .approximate:
            return "\(name) ~\(UnitFormatter.duration(seconds: e.durationSeconds ?? 0))"
        }
    }

    /// Second line: "1.40 mi · 3:42 PM", "120 m to rejoin", nil when nothing to add.
    /// `reference` is "now": the clock time is `reference + duration` while the
    /// value is live, and fixed at `computedAt + duration` once stale.
    static func secondary(_ e: HikeEstimate?, units: UnitsPreference, reference: Date,
                          timeZone: TimeZone = .current, locale: Locale = .current) -> String? {
        guard let e else { return nil }
        switch e.status {
        case .estimating, .unavailable, .arrived: return nil
        case .offTrail(let m):
            return "\(UnitFormatter.elevation(meters: m, units: units)) to rejoin"
        case .ok, .approximate, .stale:
            guard let d = e.distanceMeters, let t = e.durationSeconds else { return nil }
            let base = e.status == .stale ? e.computedAt : reference
            let clock = base.addingTimeInterval(t).formatted(
                Date.FormatStyle(date: .omitted, time: .shortened, locale: locale,
                                 calendar: Calendar(identifier: .gregorian), timeZone: timeZone))
            let tail = e.status == .stale ? " · last known" : ""
            return "\(UnitFormatter.distance(meters: d, units: units)) · \(clock)\(tail)"
        }
    }

    /// Full VoiceOver sentence.
    static func accessibility(_ e: HikeEstimate?, kind: Kind, units: UnitsPreference,
                              reference: Date, timeZone: TimeZone = .current,
                              locale: Locale = .current) -> String {
        let name = label(kind)
        let tgt = target(kind, trailName: e?.targetName)
        guard let e else { return "\(name), still estimating." }
        switch e.status {
        case .estimating, .unavailable: return "\(name), still estimating."
        case .offTrail(let m):
            return "\(name), off trail, \(spokenDistance(m, units)) to rejoin the trail."
        case .arrived:
            return kind == .remaining ? "\(name), you are at the end of \(e.targetName ?? "the trail")."
                                      : "\(name), you are at the start."
        case .ok, .approximate, .stale:
            guard let d = e.distanceMeters, let t = e.durationSeconds else { return "\(name), still estimating." }
            let prefix = e.status == .stale ? "Last known, " : ""
            let rough = e.status == .approximate ? "roughly " : "about "
            let base = e.status == .stale ? e.computedAt : reference
            let clock = base.addingTimeInterval(t).formatted(
                Date.FormatStyle(date: .omitted, time: .shortened, locale: locale,
                                 calendar: Calendar(identifier: .gregorian), timeZone: timeZone))
            return "\(prefix)\(name), \(rough)\(spokenDuration(t)), \(spokenDistance(d, units)), \(tgt), "
                + (kind == .remaining ? "done around \(clock)." : "back around \(clock).")
        }
    }

    static func explanation(hasTrail: Bool, trailName: String?) -> String {
        let back = "Back: time and distance to return to where you started, retracing your steps."
        guard hasTrail else { return back }
        return "Remaining: time and distance left \(target(.remaining, trailName: trailName)), measured along the trail.\n" + back
    }

    private static func spokenDuration(_ seconds: TimeInterval) -> String {
        let m = Int((seconds / 60).rounded())
        if m < 1 { return "less than a minute" }
        if m < 60 { return m == 1 ? "1 minute" : "\(m) minutes" }
        let h = m / 60, mm = m % 60
        let hs = h == 1 ? "1 hour" : "\(h) hours"
        return mm == 0 ? hs : "\(hs) \(mm) minutes"
    }

    private static func spokenDistance(_ meters: Double, _ units: UnitsPreference) -> String {
        switch units {
        case .imperial:
            let mi = meters / 1609.344
            if mi < 0.1 { return "\(Int((meters * 3.28084 / 10).rounded()) * 10) feet" }
            return String(format: "%.1f miles", mi)
        case .metric:
            if meters < 1000 { return "\(Int((meters / 10).rounded()) * 10) meters" }
            return String(format: "%.1f kilometers", meters / 1000)
        }
    }
}
