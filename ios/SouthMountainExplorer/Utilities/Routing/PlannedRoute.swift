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

    /// Consecutive steps of one kind, joined into one line.
    struct Segment: Equatable, Sendable {
        let kind: Kind
        let coords: [Coord]
        let lengthMeters: Double
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
    static func build(plan: RoutePlan, edges: [TrailNetwork.Edge]) -> PlannedRoute {
        var byId: [Int: TrailNetwork.Edge] = [:]
        for e in edges { byId[e.id] = e }
        var kinds: [PlannedRoute.Kind] = []
        var coords: [[PlannedRoute.Coord]] = []
        var lengths: [Double] = []
        for step in plan.steps {
            guard let edge = byId[step.edgeId] else { continue }
            let pts = edge.coordinates.map { PlannedRoute.Coord(lat: $0.lat, lon: $0.lon) }
            let walked = step.direction == .forward ? pts : Array(pts.reversed())
            guard walked.count >= 2 else { continue }
            let k = kind(edge.kind)
            if let last = kinds.last, last == k {
                var merged = coords[coords.count - 1]
                // The first vertex of a step is the last vertex of the previous one.
                merged.append(contentsOf: merged.last == walked.first ? Array(walked.dropFirst()) : walked)
                coords[coords.count - 1] = merged
                lengths[lengths.count - 1] += edge.lengthMeters
            } else {
                kinds.append(k)
                coords.append(walked)
                lengths.append(edge.lengthMeters)
            }
        }
        let segments = kinds.indices.map {
            PlannedRoute.Segment(kind: kinds[$0], coords: coords[$0], lengthMeters: lengths[$0])
        }
        guard let first = segments.first?.coords.first, let last = segments.last?.coords.last else { return .empty }
        return PlannedRoute(
            segments: segments, tags: tags(for: segments), start: first,
            end: plan.shape == .oneWay ? last : nil)
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
