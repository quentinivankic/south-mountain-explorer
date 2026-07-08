import Foundation
import SwiftUI

// MARK: - On-device confidence score (spec §4.3)
//
// Swift port of the canonical reference `data-pipeline/build/
// scoring_reference.py`. This is a dev/authoring POLICY, not shipped
// data: the tiles carry raw signals (Bucket A) + a few precomputed flags
// (Bucket B), and this computes a confidence score LIVE from a tunable
// weights config — so a region can be re-curated with no tile rebuild.
//
//   score = clamp(base + Σ(weightᵢ × firedᵢ), 0, 100)
//   band  = high (≥ bandHigh) | medium (≥ bandMedium) | low
//
// The score NEVER removes a trail from the tiles — only the licensing
// gate does that (§2). It only informs which trails the *developer*
// curates into the shipped set. The shipped user build carries no
// confidence UI; this engine drives the DEBUG-only authoring lab.
//
// Kept in lockstep with the Python reference by `TrailScoringTests`,
// which pins the same conformance numbers. Change weights in ONE place
// (`ScoringWeights.default`) and update both suites together.

/// The twelve weighted signals from the §4.3 mapping. Each fires 0/1;
/// its weight is added iff it fires.
enum ScoreSignal: String, CaseIterable, Identifiable, Sendable {
    // positives
    case authoritativeMatch = "authoritative_match"
    case inRouteRelation = "in_route_relation"
    case networkNational = "network_national"
    case hasKnownOperator = "has_known_operator"
    case hasName = "has_name"
    case inOfficialWhitelist = "in_official_whitelist"
    case regionTrustHigh = "region_trust_high"
    // negatives
    case vehicleOrUtilityRoad = "vehicle_or_utility_road"
    case tooShort = "too_short"
    case accessRestricted = "access_restricted"
    case informal = "informal"
    case lifecycleAbandonedOrDisused = "lifecycle_abandoned_or_disused"
    case trailVisibilityPoor = "trail_visibility_poor"
    case sacScaleT4Plus = "sac_scale_t4_plus"
    case tigerUnreviewed = "tiger_unreviewed"
    case recentlyEditedOrLowTrust = "recently_edited_or_low_trust"

    var id: String { rawValue }

    /// Positive signals lift the score; negatives cut it. Drives slider
    /// range + grouping in the authoring lab.
    var isPositive: Bool {
        switch self {
        case .authoritativeMatch, .inRouteRelation, .networkNational,
             .hasKnownOperator, .hasName, .inOfficialWhitelist, .regionTrustHigh:
            return true
        default:
            return false
        }
    }

    var label: String {
        switch self {
        case .authoritativeMatch: return "Authoritative match"
        case .inRouteRelation: return "In hiking route"
        case .networkNational: return "National/intl network"
        case .hasKnownOperator: return "Known operator"
        case .hasName: return "Has name"
        case .inOfficialWhitelist: return "Official whitelist"
        case .regionTrustHigh: return "High-trust region"
        case .vehicleOrUtilityRoad: return "Road / utility track"
        case .tooShort: return "Too short"
        case .accessRestricted: return "Access restricted"
        case .informal: return "Informal"
        case .lifecycleAbandonedOrDisused: return "Abandoned / disused"
        case .trailVisibilityPoor: return "Poor visibility"
        case .sacScaleT4Plus: return "SAC ≥ demanding"
        case .tigerUnreviewed: return "TIGER unreviewed"
        case .recentlyEditedOrLowTrust: return "Recent / low-trust edit"
        }
    }
}

/// The tunable config: base score, per-signal weights, and band cutoffs.
/// Mirrors `weights.default.json`. ILLUSTRATIVE starting values — tune
/// against the NZ pilot.
struct ScoringWeights: Equatable, Sendable {
    var base: Double
    var weights: [ScoreSignal: Double]
    var bandHigh: Double
    var bandMedium: Double

    func weight(_ s: ScoreSignal) -> Double { weights[s] ?? 0 }

    /// Policy: "named OR officially recognized (DOC-matched / inside
    /// conservation land) → keep (high); anonymous urban footways,
    /// private, abandoned, informal → drop (low)". Tuned against the real
    /// NZ pilot (~7% high, ~93% low). Base is LOW (30) so an unknown
    /// trail isn't trusted by default. Mirrors weights.default.json;
    /// TrailScoringTests pins these numbers.
    static let `default` = ScoringWeights(
        base: 30,
        weights: [
            .authoritativeMatch: 40,
            .inRouteRelation: 40,
            .networkNational: 15,
            .hasKnownOperator: 20,
            .hasName: 40,
            .inOfficialWhitelist: 10,
            .regionTrustHigh: 5,
            .vehicleOrUtilityRoad: -50,
            .tooShort: -50,
            .accessRestricted: -45,
            .informal: -40,
            .lifecycleAbandonedOrDisused: -60,
            .trailVisibilityPoor: -20,
            .sacScaleT4Plus: 0,
            .tigerUnreviewed: -15,
            .recentlyEditedOrLowTrust: -10,
        ],
        bandHigh: 70,
        bandMedium: 45
    )
}

enum ScoreBand: String, Sendable {
    case high, medium, low

    var label: String { rawValue.capitalized }

    var color: Color {
        switch self {
        case .high: return .green
        case .medium: return .orange
        case .low: return .red
        }
    }
}

/// A trail's shipped attributes: Bucket A raw signals (§4.1) + Bucket B
/// precomputed flags (§4.2). Named to mirror the `scoring_reference.py`
/// keys so the two stay comparable. On real tiles these come off the
/// pmtiles feature properties; in the authoring lab they come from the
/// bundled sample set.
struct TrailScoringProps: Identifiable, Sendable {
    var id = UUID()
    var name: String

    var authoritativeMatch = false
    var inRouteRelation = false     // member of an OSM hiking route relation
    var network = ""                // iwn / nwn / rwn / lwn (walking network)
    var hasKnownOperator = false
    var hasName = false
    var inOfficialWhitelist = false
    var regionTrust = ""            // "high" fires region_trust_high
    var access = ""                 // no / private / discouraged → restricted
    var informal = false
    var lifecycle = ""              // abandoned / disused → dead
    var trailVisibility = ""        // bad / horrible / no → poor
    var sacScale = ""               // hiking … difficult_alpine_hiking, or "t3"
    var tigerUnreviewed = false
    var lowTrustEditor = false
    var vehicleOrUtilityRoad = false // track that's really a road/utility corridor
    var lengthMi: Double? = nil     // whole-trail length; < MIN_TRAIL_MI → too short
    /// Days since the last OSM edit. `< 30` fires the recency signal;
    /// nil = old/unknown. Stand-in for the reference's timestamp math.
    var editedDaysAgo: Int? = nil

    /// How many real trails in the region share this exact signal
    /// signature. Set for the sampled NZ data so the lab can weight the
    /// band tally by prevalence (one "unnamed footway" sample stands for
    /// ~300k trails); nil for hand-authored fixtures.
    var count: Int? = nil
}

enum TrailScoring {
    static let recentEditDays = 30
    static let minTrailMi = 0.59

    private static let sacRank: [String: Int] = [
        "hiking": 1,
        "mountain_hiking": 2,
        "demanding_mountain_hiking": 3,
        "alpine_hiking": 4,
        "demanding_alpine_hiking": 5,
        "difficult_alpine_hiking": 6,
    ]
    /// `sac_scale_t4_plus` fires at demanding_mountain_hiking (rank 3) or
    /// harder — matches the reference's documented trigger despite the
    /// legacy "t4" flag name.
    private static let sacT4PlusThreshold = 3

    private static let accessRestricted: Set<String> = ["no", "private", "discouraged"]
    private static let visibilityPoor: Set<String> = ["bad", "horrible", "no"]
    private static let lifecycleDead: Set<String> = ["abandoned", "disused"]
    private static let networkNational: Set<String> = ["iwn", "nwn"]

    private static func norm(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// OSM `sac_scale` → ordinal rank (0 = absent/unknown). Tolerates
    /// "t3"/"T4" numeric-style encodings, like the reference.
    static func sacRankOf(_ value: String) -> Int {
        let s = norm(value)
        if let r = sacRank[s] { return r }
        if s.hasPrefix("t"), let n = Int(s.dropFirst()), n >= 0 { return n }
        return 0
    }

    /// The set of signals that fire for a trail. Pure — same input, same
    /// output — so it's directly testable against the Python conformance
    /// suite.
    static func firedSignals(_ p: TrailScoringProps) -> Set<ScoreSignal> {
        var fired: Set<ScoreSignal> = []
        func set(_ s: ScoreSignal, _ cond: Bool) { if cond { fired.insert(s) } }

        set(.authoritativeMatch, p.authoritativeMatch)
        set(.inRouteRelation, p.inRouteRelation)
        set(.networkNational, networkNational.contains(norm(p.network)))
        set(.hasKnownOperator, p.hasKnownOperator)
        set(.hasName, p.hasName)
        set(.inOfficialWhitelist, p.inOfficialWhitelist)
        set(.regionTrustHigh, norm(p.regionTrust) == "high")

        set(.vehicleOrUtilityRoad, p.vehicleOrUtilityRoad)
        set(.tooShort, (p.lengthMi ?? .greatestFiniteMagnitude) < minTrailMi)
        set(.accessRestricted, accessRestricted.contains(norm(p.access)))
        set(.informal, p.informal)
        set(.lifecycleAbandonedOrDisused, lifecycleDead.contains(norm(p.lifecycle)))
        set(.trailVisibilityPoor, visibilityPoor.contains(norm(p.trailVisibility)))
        set(.sacScaleT4Plus, sacRankOf(p.sacScale) >= sacT4PlusThreshold)
        set(.tigerUnreviewed, p.tigerUnreviewed)
        let recentlyEdited = (p.editedDaysAgo ?? Int.max) < recentEditDays
        set(.recentlyEditedOrLowTrust, recentlyEdited || p.lowTrustEditor)

        return fired
    }

    /// `clamp(base + Σ weightᵢ·firedᵢ, 0, 100)`.
    static func score(_ p: TrailScoringProps, weights: ScoringWeights) -> Double {
        var total = weights.base
        for signal in firedSignals(p) {
            total += weights.weight(signal)
        }
        return max(0, min(100, total))
    }

    static func band(_ value: Double, weights: ScoringWeights) -> ScoreBand {
        if value >= weights.bandHigh { return .high }
        if value >= weights.bandMedium { return .medium }
        return .low
    }

    static func scoreAndBand(_ p: TrailScoringProps,
                             weights: ScoringWeights) -> (score: Double, band: ScoreBand) {
        let s = score(p, weights: weights)
        return (s, band(s, weights: weights))
    }
}
