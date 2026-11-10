import Foundation

// "Plan a Hike", Task 9: the pure side of the Plan a Hike sheet. Inputs,
// validation, spot naming, and the mapping from a `PlanOutcome` to display
// strings. No SwiftUI, no services: everything here is unit-testable.

// MARK: - Inputs

enum PlanPaceChoice: Equatable, Sendable {
    case standard
    case mine
}

/// A start or end the user can pick from a list.
struct PlanSpot: Identifiable, Equatable, Sendable {
    let id: String
    let point: StartPoint
    let title: String
}

/// What the user has chosen so far.
struct PlanHikeInputs: Equatable, Sendable {
    /// 15 to 120 min in 15 min steps, then 2.5 h to 8 h in 30 min steps.
    static let baseTimeOptionsMinutes: [Int] =
        Array(stride(from: 15, through: 120, by: 15)) + Array(stride(from: 150, through: 480, by: 30))
    static let defaultTimeMinutes = 120
    /// The quick time chips: 1 h, 2 h, 3 h, 4 h. "Custom" covers everything else.
    static let timeChipMinutes = [60, 120, 180, 240]
    static let maxTimeMinutes = 24 * 60
    /// An end closer than this to the start is the same place.
    static let minStartEndMeters = 30.0

    var start: StartPoint?
    var end: StartPoint?
    var timeLimitMinutes = PlanHikeInputs.defaultTimeMinutes
    var limitClimb = false
    /// Display units (ft for imperial, m for metric).
    var climbValue = PlanHikeInputs.defaultClimb(units: .imperial)
    var pace = PlanPaceChoice.standard
    var shape = RoutePlanRequest.Shape.loop

    // MARK: Climb

    static func climbOptions(units: UnitsPreference) -> [Int] {
        units == .imperial ? [250, 500, 750, 1000, 1500, 2000, 3000] : [100, 200, 300, 500, 750, 1000]
    }

    static func defaultClimb(units: UnitsPreference) -> Int { units == .imperial ? 1000 : 300 }

    /// Keeps `climbValue` on a real option for the unit in use.
    mutating func normalizeClimb(units: UnitsPreference) {
        if !Self.climbOptions(units: units).contains(climbValue) { climbValue = Self.defaultClimb(units: units) }
    }

    /// Climb cap for the planner, in feet. nil when the cap is off.
    func maxGainFt(units: UnitsPreference) -> Double? {
        guard limitClimb, climbValue > 0 else { return nil }
        return units == .imperial ? Double(climbValue) : Double(climbValue) * 3.28084
    }

    // MARK: Time

    var timeLimitSeconds: Double { Double(timeLimitMinutes) * 60 }

    /// true when the time is one of the quick chips.
    var timeIsChip: Bool { Self.timeChipMinutes.contains(timeLimitMinutes) }

    /// The picker list. Keeps the current value (a re-plan can land off the grid).
    func timeOptions() -> [Int] {
        var all = Self.baseTimeOptionsMinutes
        if !all.contains(timeLimitMinutes) { all.append(timeLimitMinutes) }
        return all.sorted()
    }

    /// One-tap "Plan for 1 h 35 m": sets the limit to `seconds`, rounded up to a whole minute.
    mutating func applyReplan(seconds: Double) {
        timeLimitMinutes = min(Self.maxTimeMinutes, max(1, Int((seconds / 60 - 1e-6).rounded(.up))))
    }

    // MARK: Pace

    /// "My pace" is offered only when it was measured.
    static func offersMyPace(_ personal: PersonalPaceResult?) -> Bool { personal != nil }

    mutating func reconcilePace(personal: PersonalPaceResult?) {
        if pace == .mine, personal == nil { pace = .standard }
    }

    func paceFactor(personal: PersonalPaceResult?) -> Double {
        pace == .mine ? (personal?.factor ?? 1.0) : 1.0
    }

    // MARK: Validation

    enum Issue: Equatable, Sendable {
        case noStart
        case noEnd
        case endSameAsStart
        case timeOutOfRange
        case climbInvalid

        var message: String {
            switch self {
            case .noStart: return "Choose where to start."
            case .noEnd: return "Choose where the one-way hike ends."
            case .endSameAsStart: return "Pick an end that is different from the start, or choose Loop."
            case .timeOutOfRange: return "Pick a time limit between 1 min and 24 h."
            case .climbInvalid: return "Pick a climb limit, or turn the limit off."
            }
        }
    }

    var issues: [Issue] {
        var out: [Issue] = []
        guard let start else {
            out.append(.noStart)
            return out + shapeAndLimitIssues(start: nil)
        }
        return shapeAndLimitIssues(start: start)
    }

    private func shapeAndLimitIssues(start: StartPoint?) -> [Issue] {
        var out: [Issue] = []
        if shape == .oneWay {
            if let end {
                if let start,
                   Area.meters(start.lat, start.lon, end.lat, end.lon) < Self.minStartEndMeters {
                    out.append(.endSameAsStart)
                }
            } else {
                out.append(.noEnd)
            }
        }
        if timeLimitMinutes < 1 || timeLimitMinutes > Self.maxTimeMinutes { out.append(.timeOutOfRange) }
        if limitClimb, climbValue <= 0 { out.append(.climbInvalid) }
        return out
    }

    var isValid: Bool { issues.isEmpty }
}

// MARK: - Start / end list

enum PlanSpotBuilder {
    /// Lots to offer as a start or end: named first (A to Z), then "Parking
    /// near <trail>" by name. Duplicate titles get " (2)", " (3)".
    static func spots(lots: [ParkingLot], trails: [Trail], limit: Int = 40) -> [PlanSpot] {
        let sorted = lots.sorted { ($0.lat, $0.lon) < ($1.lat, $1.lon) }
        var built: [(spot: PlanSpot, named: Bool)] = []
        for lot in sorted {
            let name = lot.name?.trimmingCharacters(in: .whitespacesAndNewlines)
            let named = !(name ?? "").isEmpty
            let isTrailhead = lot.source == "blm" || lot.source == "usfs"
            let title: String
            if let name, named {
                title = name
            } else {
                let kind = isTrailhead ? "Trailhead" : "Parking"
                if let near = nearestTrailName(lat: lot.lat, lon: lot.lon, trails: trails) {
                    title = "\(kind) near \(near)"
                } else {
                    title = kind
                }
            }
            let point: StartPoint = isTrailhead ? .trailhead(name: title, lat: lot.lat, lon: lot.lon) : .parking(lot)
            let id = String(format: "lot:%.5f,%.5f", lot.lat, lot.lon)
            built.append((PlanSpot(id: id, point: point, title: title), named))
        }
        built.sort {
            if $0.named != $1.named { return $0.named }
            return ($0.spot.title, $0.spot.id) < ($1.spot.title, $1.spot.id)
        }
        var count: [String: Int] = [:]
        var out: [PlanSpot] = []
        for item in built.prefix(limit) {
            let n = (count[item.spot.title] ?? 0) + 1
            count[item.spot.title] = n
            if n == 1 {
                out.append(item.spot)
            } else {
                let title = "\(item.spot.title) (\(n))"
                out.append(PlanSpot(id: item.spot.id, point: Self.retitled(item.spot.point, title), title: title))
            }
        }
        return out
    }

    private static func retitled(_ p: StartPoint, _ title: String) -> StartPoint {
        if case .trailhead(_, let lat, let lon) = p { return .trailhead(name: title, lat: lat, lon: lon) }
        return p
    }

    /// Name of the trail with a vertex nearest the point (flat-earth distance;
    /// only the ranking matters).
    static func nearestTrailName(lat: Double, lon: Double, trails: [Trail]) -> String? {
        let cosLat = cos(lat * .pi / 180)
        var best: (d: Double, name: String)?
        for t in trails {
            for seg in t.segments {
                for p in seg where p.count >= 2 {
                    let dy = p[0] - lat, dx = (p[1] - lon) * cosLat
                    let d = dx * dx + dy * dy
                    if best == nil || d < best!.d || (d == best!.d && t.name < best!.name) { best = (d, t.name) }
                }
            }
        }
        return best?.name
    }

    /// A dropped pin. Never shows raw coordinates.
    static func pinSpot(lat: Double, lon: Double) -> PlanSpot {
        PlanSpot(id: "pin", point: .pin(lat: lat, lon: lon), title: "Dropped pin")
    }

    static let hereTitle = "Your location"

    /// The user's own position as a start option.
    static func hereSpot(lat: Double, lon: Double) -> PlanSpot {
        PlanSpot(id: "here", point: .pin(lat: lat, lon: lon, name: hereTitle), title: hereTitle)
    }

    /// Where a new plan starts: the user's location when it is close enough to
    /// a trail to snap to, else the trailhead (or parking spot) nearest to the
    /// user, else nearest to the area's centre. nil when there is nothing to offer.
    static func defaultStart(
        user: (lat: Double, lon: Double)?, spots: [PlanSpot], trails: [Trail],
        areaCenter: (lat: Double, lon: Double), maxTrailMeters: Double
    ) -> PlanSpot? {
        if let user, let d = nearestTrailMeters(lat: user.lat, lon: user.lon, trails: trails), d <= maxTrailMeters {
            return hereSpot(lat: user.lat, lon: user.lon)
        }
        let ref = user ?? areaCenter
        func dist(_ s: PlanSpot) -> Double { Area.meters(ref.lat, ref.lon, s.point.lat, s.point.lon) }
        func isTrailhead(_ s: PlanSpot) -> Bool { if case .trailhead = s.point { return true } else { return false } }
        let pool = spots.contains(where: isTrailhead) ? spots.filter(isTrailhead) : spots
        return pool.min { (dist($0), $0.id) < (dist($1), $1.id) }
    }

    /// Straight-line metres from a point to the nearest trail vertex.
    static func nearestTrailMeters(lat: Double, lon: Double, trails: [Trail]) -> Double? {
        var best: Double?
        for t in trails {
            for seg in t.segments {
                for p in seg where p.count >= 2 {
                    let d = Area.meters(lat, lon, p[0], p[1])
                    if best == nil || d < best! { best = d }
                }
            }
        }
        return best
    }
}

// MARK: - Result display

/// What the planner learned about where the start sits, for the notes.
struct PlanResultContext: Equatable, Sendable {
    struct Part: Equatable, Sendable {
        let isLargest: Bool
        let lengthMeters: Double
        let largestLengthMeters: Double
    }
    var notDownloaded = 0
    var failedToLoad = 0
    var startPart: Part?
    var startOffTrailMeters = 0.0
    var endOffTrailMeters = 0.0

    /// A start joins a "small component" when it is not the largest piece and
    /// holds under a quarter of the largest piece's trail.
    var startIsInSmallPart: Bool {
        guard let p = startPart, !p.isLargest else { return false }
        return p.lengthMeters < 0.25 * p.largestLengthMeters
    }
}

struct PlanResultDisplay: Equatable {
    enum Kind: Equatable { case planned, nothingFits, noCandidates }

    struct Stat: Equatable {
        let label: String
        let value: String
    }

    struct Route: Equatable {
        let title: String
        /// Trails it finishes, in walking order.
        let trailNames: [String]
        let stats: [Stat]
        /// "1 connector (0.05 mi off-trail)", "2 unverified connections (0.03 mi)".
        let flags: [String]
        /// Connector + unverified connection count.
        let flagCount: Int
        /// One sentence for VoiceOver.
        let spoken: String
        /// "Kachina Access · 7.4 mi · 2 h 57 m · 1.0 new mi".
        let summary: String
        /// "1963 ft climb".
        let climbLine: String
        /// Set when most of the route is repeat ground (new miles under 30%).
        let lowValueNote: String?
        /// Where the distance comes from, adding up to it:
        /// "3.48 new + 0.87 repeat + 0.06 off-trail = 4.41 mi".
        var breakdown: String = ""
    }

    struct Alternative: Equatable {
        let title: String
        let why: String
        let route: Route
    }

    struct Replan: Equatable {
        let seconds: Double
        let buttonTitle: String
    }

    let kind: Kind
    let headline: String
    /// Planner's honest text (nothing-fits and no-candidates).
    let message: String?
    let route: Route?
    let alternatives: [Alternative]
    let replan: Replan?
    /// Nothing-fits: the "closest you can get" walk.
    let fallback: Route?
    let notes: [String]

    static func make(outcome: PlanOutcome, context: PlanResultContext, units: UnitsPreference) -> PlanResultDisplay {
        let notes = Self.notes(context, units: units)
        switch outcome {
        case .planned(let best, let alts):
            let hasShorter = alts.contains { $0.label == .shortest }
            let route = Self.route(best, title: "Your hike", units: units, isBest: true, offersShorter: hasShorter)
            let alternatives = alts.map {
                Alternative(title: $0.label.title, why: $0.why,
                            route: Self.route($0.plan, title: $0.label.title, units: units, isBest: false))
            }
            let n = best.finished.count
            return PlanResultDisplay(
                kind: .planned,
                headline: n == 0
                    ? "\(PlanText.miles(best.metrics.newMeters)) of new trail in \(PlanText.duration(best.metrics.seconds))"
                    : "Finishes \(PlanText.trailCount(n)) in \(PlanText.duration(best.metrics.seconds))",
                message: nil, route: route, alternatives: alternatives, replan: nil, fallback: nil, notes: notes)
        case .nothingFits(let n):
            let replan = n.replanSeconds.map {
                Replan(seconds: $0, buttonTitle: "Plan for \(PlanText.duration($0))")
            }
            return PlanResultDisplay(
                kind: .nothingFits,
                headline: "Nothing fits in \(PlanText.duration(n.limitSeconds))",
                message: n.message, route: nil, alternatives: [], replan: replan,
                fallback: n.fallback.map { Self.route($0, title: "Closest you can get", units: units) },
                notes: notes)
        case .noCandidates(let c):
            return PlanResultDisplay(
                kind: .noCandidates, headline: "Nothing to plan from here",
                message: c.message, route: nil, alternatives: [], replan: nil, fallback: nil, notes: notes)
        }
    }

    // MARK: Pieces

    static func route(_ plan: RoutePlan, title: String, units: UnitsPreference,
                      isBest: Bool = false, offersShorter: Bool = false) -> Route {
        let m = plan.metrics
        let names = plan.finished.map(\.name)
        let newLabel = units == .imperial ? "New miles" : "New km"
        let stats = [
            Stat(label: "Distance", value: UnitFormatter.distance(meters: m.meters, units: units)),
            Stat(label: "Time", value: PlanText.duration(m.seconds)),
            Stat(label: "Climb", value: UnitFormatter.elevation(feet: m.gainFt, units: units)),
            Stat(label: newLabel, value: UnitFormatter.distance(meters: m.newMeters, units: units)),
        ]
        let (flags, flagCount) = flagLines(plan, units: units)
        var spoken = "\(title). \(stats.map { "\($0.label) \($0.value)" }.joined(separator: ", "))."
        spoken += names.isEmpty ? " Finishes no whole trail." : " Finishes \(PlanText.trailCount(names.count)): \(names.joined(separator: ", "))."
        if !flags.isEmpty { spoken += " Includes \(flags.joined(separator: " and "))." }
        let summary = summaryLine(plan, units: units)
        let climb = "\(UnitFormatter.elevation(feet: m.gainFt, units: units)) climb"
        var low: String?
        if plan.isLowValue {
            let newText = PlanText.distance(m.newMeters, units: units, decimals: 2)
            let total = PlanText.distance(m.meters, units: units)
            let unit = UnitFormatter.distanceSuffix(units: units)
            low = isBest
                ? "Most trails near here are done. Best is \(newText.dropLast(unit.count + 1)) new \(unit) in \(total)."
                    + (offersShorter ? " Try Shorter for a closer option." : "")
                : "Mostly trails you have done: \(newText.dropLast(unit.count + 1)) new \(unit) in \(total)."
        }
        if let low { spoken += " " + low }
        let parts = breakdown(m, units: units)
        spoken += " " + parts.spoken
        return Route(title: title, trailNames: names, stats: stats, flags: flags, flagCount: flagCount,
                     spoken: spoken, summary: summary, climbLine: climb, lowValueNote: low,
                     breakdown: parts.text)
    }

    /// One part of the distance breakdown, in hundredths of the display unit.
    struct BreakdownPart: Equatable {
        let label: String
        let hundredths: Int
    }

    /// Splits the route distance into new trail, repeat trail (walked before or
    /// earlier this hike), off-trail connectors and unverified connections.
    /// Rounded by largest remainder so the shown parts add up exactly to the
    /// shown total. Parts that round to zero are left out.
    static func breakdownParts(_ m: RouteMetrics, units: UnitsPreference) -> (parts: [BreakdownPart], totalHundredths: Int) {
        let unit = units == .imperial ? 1609.344 : 1000.0
        let raw: [(String, Double)] = [
            ("new", m.newMeters),
            ("repeat", max(0, m.trailMeters - m.newMeters)),
            ("off-trail", m.connectorMeters),
            ("unverified", m.bridgeMeters),
        ]
        let scaled = raw.map { max(0, $0.1) / unit * 100 }
        let total = Int((m.meters / unit * 100).rounded())
        var shares = scaled.map { Int($0.rounded(.down)) }
        var left = total - shares.reduce(0, +)
        // Hand the leftover hundredths to the largest remainders (ties: list order).
        let order = scaled.indices.sorted {
            let a = scaled[$0] - Double(shares[$0]), b = scaled[$1] - Double(shares[$1])
            return a != b ? a > b : $0 < $1
        }
        var i = 0
        while left > 0, !order.isEmpty {
            shares[order[i % order.count]] += 1
            left -= 1
            i += 1
        }
        while left < 0, let j = shares.indices.filter({ shares[$0] > 0 }).min(by: {
            (scaled[$0] - Double(shares[$0])) < (scaled[$1] - Double(shares[$1]))
        }) {
            shares[j] -= 1
            left += 1
        }
        let parts = raw.indices.compactMap { shares[$0] > 0 ? BreakdownPart(label: raw[$0].0, hundredths: shares[$0]) : nil }
        return (parts, total)
    }

    /// "3.48 new + 0.87 repeat + 0.06 off-trail = 4.41 mi", plus a spoken form.
    static func breakdown(_ m: RouteMetrics, units: UnitsPreference) -> (text: String, spoken: String) {
        let (parts, total) = breakdownParts(m, units: units)
        let unit = UnitFormatter.distanceSuffix(units: units)
        func v(_ h: Int) -> String { String(format: "%d.%02d", h / 100, h % 100) }
        let text = parts.map { "\(v($0.hundredths)) \($0.label)" }.joined(separator: " + ") + " = \(v(total)) \(unit)"
        let spoken = "Distance is " + parts.map { "\(v($0.hundredths)) \(unit) \($0.label)" }.joined(separator: ", ") + "."
        return (text, spoken)
    }

    /// "Kachina Access · 7.4 mi · 2 h 57 m · 1.0 new mi". With several trails:
    /// "Kachina Access +2 more".
    static func summaryLine(_ plan: RoutePlan, units: UnitsPreference) -> String {
        let names = plan.finished.map(\.name)
        let lead: String
        switch names.count {
        case 0: lead = "Your hike"
        case 1: lead = names[0]
        default: lead = "\(names[0]) +\(names.count - 1) more"
        }
        let unit = UnitFormatter.distanceSuffix(units: units)
        let newValue = PlanText.distance(plan.metrics.newMeters, units: units).dropLast(unit.count + 1)
        return [lead, PlanText.distance(plan.metrics.meters, units: units),
                PlanText.duration(plan.metrics.seconds), "\(newValue) new \(unit)"].joined(separator: " · ")
    }

    /// Distinct connector / unverified-bridge edges the walk uses.
    static func flagLines(_ plan: RoutePlan, units: UnitsPreference) -> (lines: [String], count: Int) {
        var connectors = Set<Int>(), bridges = Set<Int>()
        for (step, info) in zip(plan.steps, plan.stepInfo) {
            switch info.kind {
            case .connector: connectors.insert(step.edgeId)
            case .bridge: bridges.insert(step.edgeId)
            case .trail: break
            }
        }
        var lines: [String] = []
        if !connectors.isEmpty {
            let n = connectors.count
            lines.append("\(n) connector\(n == 1 ? "" : "s") (\(UnitFormatter.distance(meters: plan.metrics.connectorMeters, units: units)) off-trail)")
        }
        if !bridges.isEmpty {
            let n = bridges.count
            lines.append("\(n) unverified connection\(n == 1 ? "" : "s") (\(UnitFormatter.distance(meters: plan.metrics.bridgeMeters, units: units)))")
        }
        return (lines, connectors.count + bridges.count)
    }

    static func notes(_ c: PlanResultContext, units: UnitsPreference) -> [String] {
        var out: [String] = []
        if c.startIsInSmallPart {
            out.append("Your start connects to a small part of the trail network, so options are limited.")
        }
        if c.startOffTrailMeters > 10 {
            out.append("Your start is \(UnitFormatter.distance(meters: c.startOffTrailMeters, units: units)) from the nearest trail. The route walks there in a straight line.")
        }
        if c.endOffTrailMeters > 10 {
            out.append("Your end is \(UnitFormatter.distance(meters: c.endOffTrailMeters, units: units)) from the nearest trail. The route finishes with a straight line.")
        }
        if c.notDownloaded > 0 {
            out.append(c.notDownloaded == 1
                ? "1 nearby area isn't downloaded and you're offline, so its trails weren't considered."
                : "\(c.notDownloaded) nearby areas aren't downloaded and you're offline, so their trails weren't considered.")
        }
        if c.failedToLoad > 0 {
            out.append(c.failedToLoad == 1
                ? "1 nearby area couldn't be loaded, so its trails weren't considered."
                : "\(c.failedToLoad) nearby areas couldn't be loaded, so their trails weren't considered.")
        }
        return out
    }
}

// MARK: - Messages

enum PlanHikeMessages {
    static func startError(_ error: TrailNetworkStartError, isEnd: Bool, units: UnitsPreference) -> String {
        let which = isEnd ? "end" : "start"
        switch error {
        case .tooFarFromTrails(let meters):
            let max = UnitFormatter.distance(meters: TrailNetworkSnapper.Options().maxConnectorMeters, units: units)
            return "Your \(which) is \(UnitFormatter.distance(meters: meters, units: units)) from the nearest trail. Pick a spot within \(max) of a trail."
        case .noTrails:
            return "This area has no trails to plan on."
        case .invalidCoordinate:
            return "That \(which) location isn't valid. Pick another."
        }
    }
}
