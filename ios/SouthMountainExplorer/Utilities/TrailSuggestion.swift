import Foundation
import CoreLocation

/// A single "you could finish this trail without much extra
/// effort" suggestion for the recording-panel banner. Includes
/// enough context to render the banner copy ("0.2 mi detour,
/// ~6 min") and act on a tap (`trail.id` feeds
/// `RecordingService.retargetTrail`).
struct TrailSuggestion: Equatable {
    let trail: Trail
    /// Meters from the user's current location to the nearest
    /// point on the trail polyline (perpendicular distance).
    let detourMeters: Double
    /// Approximate meters of trail still uncovered. Computed as
    /// `trail.distanceMi × 1609.344 × (1 - coverage)` — coarse but
    /// fine for ranking; a v2 could project the user onto the
    /// trail to refine which direction "uncovered" lies in.
    let remainingMeters: Double
    /// Time the user would add to their hike if they detoured to
    /// this trail and walked it to completion at their current
    /// smoothed pace. Drives the banner's "~X min" label via
    /// `TrailETA.formatLabel`.
    let extraSeconds: TimeInterval
}

/// Ranks nearby incomplete trails by "how easy would this be to
/// finish from where the user is right now," and returns the top
/// N as banner-ready `TrailSuggestion` values. Pure static so the
/// scoring rules are unit-testable without booting any UI.
///
/// Caller (AreaView) feeds in the current location, the
/// recording's current trail id (or nil for roam mode), the
/// area's trails, per-trail coverage from CoverageService, and a
/// smoothed pace from RecordingService. Output is empty when no
/// trail clears all filters — banner unmounts in that case.
enum TrailSuggestionEngine {

    /// Defaults tuned for hiking-app scale: 200 m detour is
    /// roughly "two minutes off your line," 1.0 mi remaining is
    /// "you could knock it out in well under an hour." Tweakable
    /// per-call so a future Settings toggle or an A/B test can
    /// loosen / tighten without editing the engine.
    /// Defaults tightened after the build-12 device test surfaced
    /// that the original 200 m / 1.0 mi caps excluded every
    /// surrounding trail in South Mountain (most are 1-2 mi and
    /// users hike multiple at a time). 300 m / 1.5 mi qualifies
    /// realistic detour candidates without crossing into "this
    /// isn't really a detour anymore" territory.
    static let defaultMaxDetourMeters: Double = 300
    static let defaultMaxRemainingMiles: Double = 1.5
    /// Tracks the completion threshold from RecordingService —
    /// bumped from 0.9 → 0.95 in build 13. The engine's "skip
    /// already-completed trails" filter uses the same cutoff so a
    /// trail the user just completed doesn't keep getting
    /// suggested.
    static let defaultCompleteThreshold: Double = 0.95

    static func candidates(
        userLocation: CLLocationCoordinate2D,
        currentTrailId: String?,
        trails: [Trail],
        coverageByTrailId: [String: Double],
        paceMetersPerSec: Double?,
        completeThreshold: Double = defaultCompleteThreshold,
        maxDetourMeters: Double = defaultMaxDetourMeters,
        maxRemainingMiles: Double = defaultMaxRemainingMiles,
        maxResults: Int = 1
    ) -> [TrailSuggestion] {
        // Pace gate. Without pace, "extra time" is meaningless —
        // same rationale TrailETA uses for showing "—" instead of
        // a number on the recording panel's ETA pill.
        guard let pace = paceMetersPerSec, pace > 0.1 else { return [] }
        let maxRemainingMeters = maxRemainingMiles * 1609.344

        var scored: [TrailSuggestion] = []
        scored.reserveCapacity(trails.count)
        for trail in trails {
            // Filter 1: don't suggest the trail the recording is
            // already targeted at.
            if let currentId = currentTrailId, trail.id == currentId { continue }

            // Filter 2: skip already-completed trails. The whole
            // feature is about progressing completion.
            let coverage = coverageByTrailId[trail.id] ?? 0
            if coverage >= completeThreshold { continue }

            // Filter 3: skip far-away trails. Project user onto
            // the trail polyline; the perpendicular distance is
            // the detour. Bail early on degenerate trails.
            let coords = trail.flattenedCoords
            guard coords.count >= 2 else { continue }
            guard let projection = PolylineMath.project(userLocation, onto: coords) else { continue }
            let detour = projection.perpendicularDistance
            if detour > maxDetourMeters { continue }

            // Filter 4: skip trails with too much uncovered length
            // to qualify as "easily complete." Approximation —
            // assumes the user would have to walk all uncovered
            // distance. A v2 could refine this by direction.
            let totalMeters = trail.distanceMi * 1609.344
            let remaining = max(0, totalMeters * (1 - coverage))
            if remaining > maxRemainingMeters { continue }

            let extraSeconds = (detour + remaining) / pace
            scored.append(TrailSuggestion(
                trail: trail,
                detourMeters: detour,
                remainingMeters: remaining,
                extraSeconds: extraSeconds
            ))
        }

        scored.sort { $0.extraSeconds < $1.extraSeconds }
        return Array(scored.prefix(maxResults))
    }
}

extension TrailSuggestion {
    /// User-facing distance label for the banner copy. Rounded to
    /// the nearest 0.1 mi — finer precision is noise at hiking
    /// scale and reads worse on a compact pill.
    var detourMilesLabel: String {
        let miles = detourMeters / 1609.344
        return String(format: "%.1f mi", miles)
    }
}
