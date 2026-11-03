import Foundation

/// How Plan a Hike ranks routes ("Plan a Hike" v2).
///
/// A route is valued by the new ground it covers, not by how many trails it
/// ticks off. In miles:
///
///     score = newMiles + 0.5 * finishedTrails - 0.25 * repeatMiles
///
/// where `repeatMiles` is everything walked that is not new (completed trails,
/// connectors, bridges, and new ground walked a second time).
///
/// - Each whole trail finished is worth a flat half mile on top of its new
///   miles, so finishing a trail still matters.
/// - Each mile of repeat walking costs a quarter mile of value. Extra distance
///   is therefore worth walking only when at least about a fifth of it is
///   new (new / (new + repeat) above 0.2). That is what stops a 7 mile walk
///   over finished trails to reach one short new trail.
/// - Scores within `tolerance` count as equal; then higher efficiency
///   (newMiles / totalMiles) wins, then less time.
///
/// A route whose efficiency is under `lowValueEfficiency` is still returned,
/// but flagged "low value" so the UI can say so and offer a shorter option.
enum RouteScore {
    static let bonusPerFinishedTrailMiles = 0.5
    static let repeatPenalty = 0.25
    /// Below this share of new ground the route is flagged low value.
    static let lowValueEfficiency = 0.30
    /// Scores closer than this (miles, about half a metre) tie.
    static let tolerance = 0.0003
    static let metersPerMile = 1609.344

    /// Score in miles.
    static func value(newMeters: Double, totalMeters: Double, finished: Int) -> Double {
        let newMi = newMeters / metersPerMile
        let repeatMi = max(0, totalMeters - newMeters) / metersPerMile
        return newMi + bonusPerFinishedTrailMiles * Double(finished) - repeatPenalty * repeatMi
    }

    /// New share of the distance walked, 0 to 1.
    static func efficiency(newMeters: Double, totalMeters: Double) -> Double {
        totalMeters > 0 ? min(1, newMeters / totalMeters) : 0
    }

    /// true when `a` ranks above `b`: score, then efficiency, then less time.
    static func isBetter(
        score a: Double, efficiency ea: Double, seconds sa: Double,
        than b: Double, efficiency eb: Double, seconds sb: Double
    ) -> Bool {
        if abs(a - b) > tolerance { return a > b }
        if abs(ea - eb) > 1e-6 { return ea > eb }
        return sa < sb - 0.5
    }
}

extension RoutePlan {
    /// The planner's score for this route, in miles.
    var score: Double {
        RouteScore.value(newMeters: metrics.newMeters, totalMeters: metrics.meters, finished: finished.count)
    }

    /// New share of the distance, 0 to 1.
    var efficiency: Double { RouteScore.efficiency(newMeters: metrics.newMeters, totalMeters: metrics.meters) }

    /// Mostly repeat ground: new miles are under 30% of the distance.
    var isLowValue: Bool { efficiency < RouteScore.lowValueEfficiency }
}
