import Foundation

// "Plan a Hike", Task 10: the pure geometry behind drawing a planned route on
// the map, plus the preview and Start-gating state. No MapKit, no SwiftUI.

// MARK: - Geometry

/// A planned walk as drawable pieces, in walking order.
struct PlannedRoute: Equatable, Sendable {
    enum Kind: Equatable, Sendable {
        case trail
        /// Off-trail straight line to or from the start / end point.
        case connector
        /// Unverified connection across a small gap between trails.
        case bridge
    }

    struct Coord: Equatable, Sendable {
        let lat: Double
        let lon: Double
    }

    /// Consecutive steps of one kind (and, on trails, one of new or done),
    /// joined into one line.
    struct Segment: Equatable, Sendable {
        let kind: Kind
        let coords: [Coord]
        let lengthMeters: Double
        /// Trail ground that counts toward the plan's new miles: not walked
        /// by a saved track, on a trail not marked complete, and the first
        /// time this hike walks it. false for connectors and bridges.
        var isNew = true
    }

    struct Tag: Equatable, Sendable {
        let kind: Kind
        let text: String
        let coord: Coord
    }

    let segments: [Segment]
    let tags: [Tag]
    let start: Coord?
    /// Only for one-way hikes. A loop ends where it started.
    let end: Coord?

    var isEmpty: Bool { segments.isEmpty }

    /// Trail metres drawn as new (equals the plan's new metres).
    var newTrailMeters: Double { segments.filter { $0.kind == .trail && $0.isNew }.reduce(0) { $0 + $1.lengthMeters } }
    /// Trail metres drawn as done: already walked, or walked earlier this hike.
    var doneTrailMeters: Double { segments.filter { $0.kind == .trail && !$0.isNew }.reduce(0) { $0 + $1.lengthMeters } }
    /// Both kinds of trail run are drawn, so the legend is worth showing.
    var mixesNewAndDone: Bool {
        segments.contains { $0.kind == .trail && $0.isNew } && segments.contains { $0.kind == .trail && !$0.isNew }
    }

    /// Every vertex, for fitting the camera.
    var allCoords: [Coord] { segments.flatMap(\.coords) }

    static let empty = PlannedRoute(segments: [], tags: [], start: nil, end: nil)
}

enum PlannedRouteBuilder {
    /// A connector or bridge shorter than this gets no tag (no clutter).
    static let minTagMeters = 10.0
    /// Tags of one kind closer than this are the same place (a loop's start
    /// connector is walked out and back).
    static let tagDedupMeters = 15.0

    static func tagText(_ kind: PlannedRoute.Kind) -> String? {
        switch kind {
        case .trail: return nil
        case .connector: return "Off-trail"
        case .bridge: return "Unverified connection"
        }
    }

    static func kind(_ k: TrailNetwork.EdgeKind) -> PlannedRoute.Kind {
        switch k {
        case .trail: return .trail
        case .connector: return .connector
        case .bridge: return .bridge
        }
    }

    /// Builds the drawable route from the plan's steps, in walk order. Each
    /// edge is read in the direction it is walked. Consecutive steps of the
    /// same kind are merged into one segment.
    ///
    /// With `completion` (round 5), trail ground is also split into new and
    /// done runs by the rule behind the plan's new miles
    /// (`TrailNetwork.newMetersByEdge`, `RouteMetrics.newMeters`): an edge of
    /// a completed trail is done; otherwise only the part no saved track
    /// walked (`WalkedGround.unwalkedRuns`) is new, and only on the first
    /// pass, so new run lengths add up to the plan's new metres. Without it,
    /// every trail step is drawn as new (the old look).
    static func build(plan: RoutePlan, edges: [TrailNetwork.Edge],
                      completion: TrailCompletionLookup? = nil) -> PlannedRoute {
        var byId: [Int: TrailNetwork.Edge] = [:]
        for e in edges { byId[e.id] = e }
        var kinds: [PlannedRoute.Kind] = []
        var news: [Bool] = []
        var coords: [[PlannedRoute.Coord]] = []
        var lengths: [Double] = []
        var seen = Set<Int>()
        for step in plan.steps {
            guard let edge = byId[step.edgeId] else { continue }
            let firstPass = seen.insert(edge.id).inserted
            let k = kind(edge.kind)
            for piece in pieces(of: edge, direction: step.direction, firstPass: firstPass, completion: completion) {
                let isNew = k == .trail && piece.isNew
                if let last = kinds.last, last == k, news.last == isNew {
                    var merged = coords[coords.count - 1]
                    // The first vertex of a piece is the last vertex of the previous one.
                    merged.append(contentsOf: merged.last == piece.coords.first ? Array(piece.coords.dropFirst()) : piece.coords)
                    coords[coords.count - 1] = merged
                    lengths[lengths.count - 1] += piece.meters
                } else {
                    kinds.append(k)
                    news.append(isNew)
                    coords.append(piece.coords)
                    lengths.append(piece.meters)
                }
            }
        }
        let segments = kinds.indices.map {
            PlannedRoute.Segment(kind: kinds[$0], coords: coords[$0], lengthMeters: lengths[$0], isNew: news[$0])
        }
        guard let first = segments.first?.coords.first, let last = segments.last?.coords.last else { return .empty }
        return PlannedRoute(
            segments: segments, tags: tags(for: segments), start: first,
            end: plan.shape == .oneWay ? last : nil)
    }

    /// One walked edge as new / done pieces in walking order. Lengths are
    /// shares of `edge.lengthMeters`; the new ones add up to the edge's new
    /// metres exactly as `TrailNetwork.newMetersByEdge` works them out.
    static func pieces(of edge: TrailNetwork.Edge, direction: RouteDirection, firstPass: Bool,
                       completion: TrailCompletionLookup?) -> [(isNew: Bool, coords: [PlannedRoute.Coord], meters: Double)] {
        let fwd = edge.coordinates.map { PlannedRoute.Coord(lat: $0.lat, lon: $0.lon) }
        guard fwd.count >= 2 else { return [] }
        let whole = direction == .forward ? fwd : Array(fwd.reversed())
        guard let completion else { return [(true, whole, edge.lengthMeters)] }
        let open = edge.kind == .trail && edge.trailKeys.contains { !completion.isComplete($0) }
        guard firstPass, open else { return [(false, whole, edge.lengthMeters)] }
        if completion.walked.isEmpty { return [(true, whole, edge.lengthMeters)] }
        let r = completion.walked.unwalkedRuns(of: edge.coordinates)
        let newMeters = edge.lengthMeters * WalkedGround.fraction(r)
        guard let total = r.totalMeters, total > 0 else { return [(true, whole, edge.lengthMeters)] }
        let runSum = r.runs.reduce(0) { $0 + ($1.to - $1.from) }
        guard runSum > 0 else { return [(false, whole, edge.lengthMeters)] }
        // Run boundaries in walking order, metres from the walked start.
        var spans: [(from: Double, to: Double, isNew: Bool)] = []
        var at = 0.0
        let runs = direction == .forward ? r.runs : r.runs.reversed().map { (total - $0.to, total - $0.from) }
        for run in runs {
            if run.from > at { spans.append((at, run.from, false)) }
            spans.append((run.from, run.to, true))
            at = run.to
        }
        if at < total { spans.append((at, total, false)) }
        // Cumulative metres along the walked line, by the same haversine the runs use.
        var cum = [0.0]
        for i in 1..<whole.count {
            cum.append(cum[i - 1] + haversineDistanceM(
                lat1: whole[i - 1].lat, lon1: whole[i - 1].lon, lat2: whole[i].lat, lon2: whole[i].lon))
        }
        func point(at d: Double) -> PlannedRoute.Coord {
            if d <= 0 { return whole[0] }
            if d >= cum[cum.count - 1] { return whole[whole.count - 1] }
            var i = 1
            while i < cum.count - 1, cum[i] < d { i += 1 }
            let span = cum[i] - cum[i - 1]
            let t = span > 0 ? (d - cum[i - 1]) / span : 0
            return .init(lat: whole[i - 1].lat + (whole[i].lat - whole[i - 1].lat) * t,
                         lon: whole[i - 1].lon + (whole[i].lon - whole[i - 1].lon) * t)
        }
        let doneMeters = edge.lengthMeters - newMeters
        let doneSum = total - runSum
        var out: [(isNew: Bool, coords: [PlannedRoute.Coord], meters: Double)] = []
        for s in spans where s.to > s.from {
            var pts = [point(at: s.from)]
            for i in cum.indices where cum[i] > s.from && cum[i] < s.to { pts.append(whole[i]) }
            pts.append(point(at: s.to))
            let len = s.to - s.from
            let meters = s.isNew ? newMeters * len / runSum : (doneSum > 0 ? doneMeters * len / doneSum : 0)
            out.append((s.isNew, pts, meters))
        }
        return out
    }

    static func tags(for segments: [PlannedRoute.Segment]) -> [PlannedRoute.Tag] {
        var out: [PlannedRoute.Tag] = []
        for s in segments {
            guard let text = tagText(s.kind), s.lengthMeters >= minTagMeters,
                  let mid = midpoint(s.coords) else { continue }
            let duplicate = out.contains {
                $0.kind == s.kind && Area.meters($0.coord.lat, $0.coord.lon, mid.lat, mid.lon) < tagDedupMeters
            }
            if !duplicate { out.append(.init(kind: s.kind, text: text, coord: mid)) }
        }
        return out
    }

    /// The point halfway along the line by length.
    static func midpoint(_ coords: [PlannedRoute.Coord]) -> PlannedRoute.Coord? {
        guard coords.count >= 2 else { return coords.first }
        var cum = [0.0]
        for i in 1..<coords.count {
            cum.append(cum[i - 1] + Area.meters(coords[i - 1].lat, coords[i - 1].lon, coords[i].lat, coords[i].lon))
        }
        let half = (cum.last ?? 0) / 2
        guard half > 0 else { return coords[0] }
        for i in 1..<coords.count where cum[i] >= half {
            let span = cum[i] - cum[i - 1]
            let t = span > 0 ? (half - cum[i - 1]) / span : 0
            return .init(
                lat: coords[i - 1].lat + (coords[i].lat - coords[i - 1].lat) * t,
                lon: coords[i - 1].lon + (coords[i].lon - coords[i - 1].lon) * t)
        }
        return coords.last
    }
}

// MARK: - Preview state

/// Which planned route (best or an alternative) is drawn on the map, if any.
struct PlanPreview: Equatable, Sendable {
    struct Option: Equatable, Sendable {
        let id: String
        let title: String
        let route: PlannedRoute
    }

    private(set) var options: [Option] = []
    private(set) var selectedIndex: Int?

    var isShowing: Bool { selectedIndex != nil }
    var canShow: Bool { !options.isEmpty }
    var selected: Option? { selectedIndex.map { options[$0] } }

    /// Best first, then alternatives. Routes with nothing to draw are left out.
    static func options(best: PlannedRoute, alternatives: [(title: String, route: PlannedRoute)]) -> [Option] {
        var out: [Option] = []
        if !best.isEmpty { out.append(Option(id: "best", title: "Best", route: best)) }
        for a in alternatives where !a.route.isEmpty {
            out.append(Option(id: slug(a.title), title: a.title, route: a.route))
        }
        return out
    }

    static func slug(_ title: String) -> String {
        title.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber }).joined(separator: "-")
    }

    /// New result: nothing is drawn until the user asks.
    mutating func load(_ options: [Option]) {
        self.options = options
        selectedIndex = nil
    }

    /// Draws the best route. No-op without one.
    mutating func show() {
        guard canShow else { return }
        selectedIndex = selectedIndex ?? 0
    }

    /// Switches the drawn route. false when the id is unknown or nothing is shown.
    @discardableResult
    mutating func select(id: String) -> Bool {
        guard isShowing, let i = options.firstIndex(where: { $0.id == id }) else { return false }
        selectedIndex = i
        return true
    }

    /// Removes the overlay. The result stays so it can be shown again.
    mutating func clear() { selectedIndex = nil }
}

// MARK: - Start gating

enum PlanStartGate: Equatable {
    case allowed
    case blocked(String)

    var isAllowed: Bool { self == .allowed }

    static func evaluate(hasActiveRecording: Bool, hasRecoveryIssue: Bool) -> PlanStartGate {
        if hasRecoveryIssue { return .blocked("A previous recording needs recovery before you can start a new hike.") }
        if hasActiveRecording { return .blocked("You're already recording a hike. Stop it before starting a planned one.") }
        return .allowed
    }

    static let guidanceNote = "Route guidance (turn cues, Remaining) for planned routes is coming soon. This records a normal hike."
}
