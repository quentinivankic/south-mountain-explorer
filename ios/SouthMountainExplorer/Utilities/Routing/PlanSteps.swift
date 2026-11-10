import Foundation

// "Plan a Hike", round 6: the numbered "where do I go at each point" list.
// Pure: built from the plan's edge walk and the network geometry, no MapKit.

/// One numbered leg of a planned walk: what to do at its start point, then
/// how far to go before the next decision.
struct PlanStep: Equatable, Sendable {
    enum Action: Equatable, Sendable {
        case start
        case straight
        case keepLeft
        case keepRight
        case left
        case right
        case sharpLeft
        case sharpRight
        case turnAround
    }

    /// 1-based, in walking order.
    let number: Int
    let action: Action
    let kind: PlannedRoute.Kind
    /// Trail name for a trail leg ("an unnamed trail" when it has none); nil
    /// for off-trail connectors and unverified connections.
    let trailName: String?
    /// The leg stays on the trail of the leg before it (a turn at a junction,
    /// or turning around at a dead end).
    let staysOnTrail: Bool
    let meters: Double
    /// Where the decision is made (the leg's first point).
    let at: PlannedRoute.Coord
    /// The leg itself, in walking order, for highlighting and zooming.
    let coords: [PlannedRoute.Coord]
    /// The walk ends after this leg.
    let isLast: Bool
    /// The plan steps (indices into `RoutePlan.steps`) this leg walks, so the
    /// map can draw just this leg in its own lanes (round 7).
    var walkRange: Range<Int> = 0..<0

    /// "Start on Fugleruten", "Left onto Fugleruten", "Right to stay on Fugleruten".
    var instruction: String {
        let name = trailName ?? "an unnamed trail"
        switch kind {
        case .connector:
            let tail = isLast ? " to the finish" : ""
            return action == .start ? "Start off-trail\(tail)" : "\(verb) off-trail\(tail)"
        case .bridge:
            return action == .start ? "Start across an unverified connection" : "\(verb) across an unverified connection"
        case .trail:
            if action == .start { return "Start on \(name)" }
            if action == .turnAround { return staysOnTrail ? "Turn around on \(name)" : "Turn around onto \(name)" }
            return staysOnTrail ? "\(verb) to stay on \(name)" : "\(verb) onto \(name)"
        }
    }

    private var verb: String {
        switch action {
        case .start: return "Start"
        case .straight: return "Straight"
        case .keepLeft: return "Keep left"
        case .keepRight: return "Keep right"
        case .left: return "Left"
        case .right: return "Right"
        case .sharpLeft: return "Sharp left"
        case .sharpRight: return "Sharp right"
        case .turnAround: return "Turn around"
        }
    }

    /// "2. Left onto Fugleruten, 0.48 mi".
    func line(units: UnitsPreference) -> String {
        "\(number). \(instruction), \(UnitFormatter.distance(meters: meters, units: units))"
    }
}

enum PlanStepBuilder {
    /// Bearings are measured this far along each line from the junction, so
    /// the small wiggles right at a node do not decide left or right.
    static let bearingSpanMeters = 20.0
    /// Same thresholds as the turn cues (`TurnCueEngine.classify`).
    static let turnDegrees = 35.0
    static let straightDegrees = 30.0
    static let sharpDegrees = 120.0
    static let turnAroundDegrees = 165.0
    /// Another way within this angle of straight ahead makes a near-straight
    /// pick a keep left / keep right.
    static let keepBranchDegrees = 60.0
    /// A trail leg shorter than this between two legs on one trail is folded
    /// into them (two junctions a few metres apart read as one crossing).
    static let minLegMeters = 20.0

    private enum Label: Equatable {
        case trail(TrailNetwork.TrailKey)
        case connector
        case bridge
    }

    private struct Leg {
        var label: Label
        var action: PlanStep.Action
        var staysOnTrail: Bool
        var meters: Double
        var coords: [PlannedRoute.Coord]
        /// Indices into the plan's steps.
        var walk: Range<Int>
    }

    /// The numbered steps for `plan` on `network` (the view the plan was made on).
    /// A new step starts where the walk changes trail or kind, turns around,
    /// or turns at a junction where a straighter way leads somewhere else.
    /// Walking straight through a junction on the same trail is not a step.
    static func steps(plan: RoutePlan, network: TrailNetwork,
                      names: [TrailNetwork.TrailKey: String]) -> [PlanStep] {
        let edges = network.edges
        let walk: [(edge: TrailNetwork.Edge, dir: RouteDirection, info: RoutePlan.StepInfo?, index: Int)] = plan.steps.enumerated().compactMap { i, s in
            guard s.edgeId >= 0, s.edgeId < edges.count else { return nil }
            return (edges[s.edgeId], s.direction, i < plan.stepInfo.count ? plan.stepInfo[i] : nil, i)
        }
        guard !walk.isEmpty else { return [] }

        func oriented(_ e: TrailNetwork.Edge, _ d: RouteDirection) -> (pts: [LocalPt], coords: [PlannedRoute.Coord]) {
            let c = e.coordinates.map { PlannedRoute.Coord(lat: $0.lat, lon: $0.lon) }
            return d == .forward ? (e.polyline, c) : (e.polyline.reversed(), c.reversed())
        }
        // Trail label for walk index `i`, keeping `current` while the edge carries it.
        func label(at i: Int, current: Label?) -> Label {
            let e = walk[i].edge
            switch e.kind {
            case .connector: return .connector
            case .bridge: return .bridge
            case .trail: break
            }
            if case .trail(let k)? = current, e.trailKeys.contains(k) { return .trail(k) }
            guard !e.trailKeys.isEmpty else { return .connector }
            // The key that runs furthest ahead, so a shared stretch is named
            // after the trail the walk stays on.
            func run(_ k: TrailNetwork.TrailKey) -> Int {
                var n = 0
                for j in i..<walk.count {
                    guard walk[j].edge.kind == .trail, walk[j].edge.trailKeys.contains(k) else { break }
                    n += 1
                }
                return n
            }
            let preferred = walk[i].info?.trailKey
            let best = e.trailKeys.max { a, b in
                let ra = run(a), rb = run(b)
                if ra != rb { return ra < rb }
                if (a == preferred) != (b == preferred) { return b == preferred }
                return a > b
            }!
            return .trail(best)
        }

        var legs: [Leg] = []
        var current: Label? = nil
        for i in walk.indices {
            let (edge, dir, _, planIndex) = walk[i]
            let o = oriented(edge, dir)
            let lab = label(at: i, current: current)
            var begin: (action: PlanStep.Action, stays: Bool)? = nil
            if i == 0 {
                begin = (.start, false)
            } else {
                let prev = walk[i - 1]
                let po = oriented(prev.edge, prev.dir)
                let node = dir == .forward ? edge.from : edge.to
                let reversed = prev.edge.id == edge.id && prev.dir != dir
                let heading = bearing(along: po.pts.reversed()).map { TurnCueGeometry.normalize360($0 + 180) }
                let out = bearing(along: o.pts)
                let theta = (heading != nil && out != nil) ? TurnCueGeometry.signedAngleDiff(from: heading!, to: out!) : 0
                let junction = node >= 0 && node < network.nodes.count && network.nodes[node].id == node
                    ? network.nodes[node] : network.nodes.first { $0.id == node }
                let others: [Double] = junction != nil
                    ? junction!.edgeIds.compactMap { id in
                        guard id != edge.id, id != prev.edge.id, id < edges.count, edges[id].kind == .trail else { return nil }
                        let other = edges[id]
                        let pts = other.from == node ? other.polyline : other.polyline.reversed()
                        guard let b = bearing(along: pts), let h = heading else { return nil }
                        return TurnCueGeometry.signedAngleDiff(from: h, to: b)
                    }
                    : []
                let action = classify(theta: theta, others: others, reversed: reversed)
                if lab != current {
                    begin = (action, false)
                } else if action == .turnAround {
                    begin = (.turnAround, true)
                } else if abs(theta) >= turnDegrees, others.contains(where: { abs($0) < abs(theta) }) {
                    begin = (action, true)
                }
            }
            if let begin {
                legs.append(Leg(label: lab, action: begin.action, staysOnTrail: begin.stays,
                                meters: edge.lengthMeters, coords: o.coords,
                                walk: planIndex..<(planIndex + 1)))
            } else {
                legs[legs.count - 1].meters += edge.lengthMeters
                var c = legs[legs.count - 1].coords
                c.append(contentsOf: c.last == o.coords.first ? Array(o.coords.dropFirst()) : o.coords)
                legs[legs.count - 1].coords = c
                legs[legs.count - 1].walk = legs[legs.count - 1].walk.lowerBound..<(planIndex + 1)
            }
            current = lab
        }
        legs = foldShortCrossings(legs)

        return legs.enumerated().map { i, leg in
            let name: String?
            let kind: PlannedRoute.Kind
            switch leg.label {
            case .trail(let k):
                kind = .trail
                let n = names[k]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                name = n.isEmpty ? nil : n
            case .connector: kind = .connector; name = nil
            case .bridge: kind = .bridge; name = nil
            }
            return PlanStep(number: i + 1, action: leg.action, kind: kind, trailName: name,
                            staysOnTrail: leg.staysOnTrail, meters: leg.meters,
                            at: leg.coords.first ?? .init(lat: 0, lon: 0), coords: leg.coords,
                            isLast: i == legs.count - 1, walkRange: leg.walk)
        }
    }

    /// A short trail leg between two legs on the same trail, with no turn
    /// around, is a crossing of two nearby junctions: join the three into one.
    private static func foldShortCrossings(_ legs: [Leg]) -> [Leg] {
        var out: [Leg] = []
        var i = 0
        while i < legs.count {
            if let prev = out.last, i + 1 < legs.count,
               case .trail = legs[i].label, legs[i].meters < minLegMeters,
               legs[i + 1].label == prev.label,
               legs[i].action != .turnAround, legs[i + 1].action != .turnAround {
                var merged = prev
                for leg in [legs[i], legs[i + 1]] {
                    merged.meters += leg.meters
                    merged.walk = merged.walk.lowerBound..<leg.walk.upperBound
                    merged.coords.append(contentsOf: merged.coords.last == leg.coords.first ? Array(leg.coords.dropFirst()) : leg.coords)
                }
                out[out.count - 1] = merged
                i += 2
                continue
            }
            out.append(legs[i])
            i += 1
        }
        return out
    }

    /// Left / right / straight from the signed turn angle (positive = right),
    /// given the other ways out of the junction (signed, same frame).
    static func classify(theta: Double, others: [Double], reversed: Bool = false) -> PlanStep.Action {
        let a = abs(theta)
        if reversed || a >= turnAroundDegrees { return .turnAround }
        if a >= sharpDegrees { return theta > 0 ? .sharpRight : .sharpLeft }
        if a >= straightDegrees { return theta > 0 ? .right : .left }
        if let near = others.filter({ abs($0) < keepBranchDegrees }).min(by: { abs($0 - theta) < abs($1 - theta) }) {
            return theta < near ? .keepLeft : .keepRight
        }
        return .straight
    }

    /// Bearing from the first point to the point `bearingSpanMeters` along the line.
    static func bearing(along pts: [LocalPt]) -> Double? {
        guard let first = pts.first, pts.count >= 2 else { return nil }
        var walked = 0.0
        for i in 1..<pts.count {
            let d = TurnCueGeometry.distance(pts[i - 1], pts[i])
            if walked + d >= bearingSpanMeters {
                let t = d > 0 ? (bearingSpanMeters - walked) / d : 0
                let p = LocalPt(x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t,
                                y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t)
                return TurnCueGeometry.distance(first, p) > 0.5 ? TurnCueGeometry.bearing(from: first, to: p) : nil
            }
            walked += d
        }
        let last = pts[pts.count - 1]
        return TurnCueGeometry.distance(first, last) > 0.5 ? TurnCueGeometry.bearing(from: first, to: last) : nil
    }
}

/// "Walk through" (round 7): step through the route one numbered leg at a
/// time. `index` nil is the whole-route view.
struct PlanWalkThrough: Equatable, Sendable {
    private(set) var count: Int
    private(set) var index: Int?

    init(count: Int = 0) {
        self.count = max(0, count)
        index = nil
    }

    var isActive: Bool { index != nil }
    var canGoBack: Bool { (index ?? 0) > 0 }
    var canGoNext: Bool { index.map { $0 + 1 < count } ?? false }
    /// "Step 3 of 9".
    var counter: String? { index.map { "Step \($0 + 1) of \(count)" } }

    /// Starts at `step` (0-based), clamped into the route. false when the
    /// route has no steps.
    @discardableResult
    mutating func enter(at step: Int = 0) -> Bool {
        guard count > 0 else { index = nil; return false }
        index = min(max(0, step), count - 1)
        return true
    }

    @discardableResult
    mutating func next() -> Bool {
        guard let i = index, i + 1 < count else { return false }
        index = i + 1
        return true
    }

    @discardableResult
    mutating func back() -> Bool {
        guard let i = index, i > 0 else { return false }
        index = i - 1
        return true
    }

    /// Back to the whole route.
    mutating func exit() { index = nil }

    /// What VoiceOver reads on each step: "Step 3 of 9. 3. Left onto Lake Loop, 0.8 mi".
    static func announcement(counter: String, line: String) -> String { "\(counter). \(line)" }
}
