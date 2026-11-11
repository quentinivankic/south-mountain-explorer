import Foundation

// "Plan a Hike", Release B task 1: the planned walk as one line in walking
// order, with a marker where each numbered step starts. Pure geometry, no
// MapKit. The forward-only tracker (task 2) and the guide (task 3) read it.

/// The selected route flattened into one polyline in walking order, measured
/// in metres along the walk. A stretch walked twice (out and back, a shared
/// stem) appears twice, the second pass at a later distance, so a position on
/// the line says which pass you are on.
struct PlanGuideRoute: Equatable, Sendable {
    /// Where one numbered step (`PlanStep`) sits on the line.
    struct Marker: Equatable, Sendable {
        /// 0-based index into the route's steps.
        let index: Int
        /// The step's 1-based number.
        let number: Int
        let action: PlanStep.Action
        let kind: PlannedRoute.Kind
        /// Metres along the line where the step starts (its decision point).
        let startMeters: Double
        /// Metres along the line where the step ends (the next one starts).
        let endMeters: Double
        let isLast: Bool

        var meters: Double { endMeters - startMeters }
        var isTurnAround: Bool { action == .turnAround }
    }

    /// Consecutive vertices closer than this are one point (a step's first
    /// vertex is the last vertex of the step before).
    static let joinMeters = 0.5

    /// Lat/lon to the flat frame the line is stored in (origin: the start).
    let projector: TurnCueProjector
    /// The line in walking order, local metres. At least two points.
    let points: [LocalPt]
    /// Metres along the line at each point (`cumulative[0] == 0`).
    let cumulative: [Double]
    /// One per step, in walking order. `markers[0].startMeters == 0` and the
    /// last ends at `length`.
    let markers: [Marker]

    var length: Double { cumulative.last ?? 0 }

    /// The line for the selected route. nil when the route has no numbered
    /// steps or nothing to walk.
    init?(route: PlannedRoute) {
        self.init(steps: route.steps)
    }

    /// The line for `steps` in walking order. nil when there is nothing to walk.
    init?(steps: [PlanStep]) {
        guard let origin = steps.first(where: { !$0.coords.isEmpty })?.coords.first else { return nil }
        let projector = TurnCueProjector(originLat: origin.lat, originLon: origin.lon)
        var pts: [LocalPt] = []
        var starts: [Int] = []
        for step in steps {
            // The step starts at the line's current end, which is its first point.
            starts.append(max(0, pts.count - 1))
            for c in step.coords {
                let p = projector.local(lat: c.lat, lon: c.lon)
                if let last = pts.last, TurnCueGeometry.distance(last, p) < Self.joinMeters { continue }
                pts.append(p)
            }
        }
        guard pts.count >= 2 else { return nil }
        let cum = TurnCueGeometry.cumulative(pts)
        var markers: [Marker] = []
        for (i, step) in steps.enumerated() {
            let start = cum[min(starts[i], cum.count - 1)]
            let end = i + 1 < steps.count ? cum[min(starts[i + 1], cum.count - 1)] : cum[cum.count - 1]
            markers.append(Marker(index: i, number: step.number, action: step.action, kind: step.kind,
                                  startMeters: start, endMeters: max(start, end), isLast: i == steps.count - 1))
        }
        self.projector = projector
        self.points = pts
        self.cumulative = cum
        self.markers = markers
    }

    func local(lat: Double, lon: Double) -> LocalPt {
        projector.local(lat: lat, lon: lon)
    }

    /// The step being walked at `meters` along the line: the last one that
    /// starts at or before it. Clamped into the route.
    func markerIndex(at meters: Double) -> Int {
        guard !markers.isEmpty else { return 0 }
        var lo = 0, hi = markers.count - 1
        while lo < hi {
            let mid = (lo + hi + 1) / 2
            if markers[mid].startMeters <= meters { lo = mid } else { hi = mid - 1 }
        }
        return lo
    }

    /// The point `meters` along the line, clamped to its ends.
    func point(at meters: Double) -> LocalPt {
        if meters <= 0 { return points[0] }
        if meters >= length { return points[points.count - 1] }
        var lo = 0, hi = cumulative.count - 1
        while hi - lo > 1 {
            let mid = (lo + hi) / 2
            if cumulative[mid] <= meters { lo = mid } else { hi = mid }
        }
        let span = cumulative[hi] - cumulative[lo]
        let t = span > 0 ? (meters - cumulative[lo]) / span : 0
        let a = points[lo], b = points[hi]
        return LocalPt(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t)
    }
}
