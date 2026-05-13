import Foundation

struct Area: Codable, Identifiable, Sendable {
    let id: String
    let name: String
    let subtitle: String
    let centerLat: Double
    let centerLon: Double
    let zoom: Double
    let bbox: [Double]?        // [minLon, minLat, maxLon, maxLat]
    let trails: [Trail]
    let trailCount: Int?
    let totalMi: Double?
    let cachedAt: Date?
    /// In-memory-only carrier for the pre-decimation trail geometry.
    /// `trails` is the decimated render-side data; `rawTrails`, when
    /// set, holds the dense node set used for spatial-grid build,
    /// halo on-trail clipping, and coverage measurement so those
    /// algorithms keep working at full fidelity. Default `nil` keeps
    /// it absent from JSON (not in `CodingKeys`); Codable's
    /// synthesized init/encode skip stored properties with defaults
    /// that aren't listed in `CodingKeys`. `var` rather than `let`
    /// so the memberwise init exposes `rawTrails:` as a parameter
    /// (Swift omits `let`-with-default from the synthesized init).
    var rawTrails: [Trail]? = nil

    var resolvedTrailCount: Int { trailCount ?? trails.count }
    var resolvedTotalMi: Double { totalMi ?? trails.reduce(0) { $0 + $1.distanceMi } }

    enum CodingKeys: String, CodingKey {
        case id, name, subtitle, zoom, bbox, trails
        case centerLat = "center_lat"
        case centerLon = "center_lon"
        case trailCount = "trail_count"
        case totalMi = "total_mi"
        case cachedAt = "cached_at"
    }
}

// Supabase row shape for full area fetch
struct AreaRow: Codable, Sendable {
    let id: String
    let name: String
    let state: String
    let centerLat: Double
    let centerLon: Double
    let zoom: Double
    let bbox: [Double]?
    let trails: [Trail]?
    let trailCount: Int?
    let totalMi: Double?
    let cachedAt: String?
    /// Pre-resolved OSM relation id from the bundled index. When set,
    /// fetchFromOverpass uses it directly instead of round-tripping
    /// through Nominatim, which keeps Python (build-time) and iOS
    /// (runtime) querying the same polygon.
    let osmRelationId: Int?

    enum CodingKeys: String, CodingKey {
        case id, name, state, zoom, bbox, trails
        case centerLat = "center_lat"
        case centerLon = "center_lon"
        case trailCount = "trail_count"
        case totalMi = "total_mi"
        case cachedAt = "cached_at"
        case osmRelationId = "osm_relation_id"
    }

    func toArea() -> Area {
        let formatter = ISO8601DateFormatter()
        let resolvedTrails = trails ?? []
        return Area(
            id: id,
            name: name,
            subtitle: state,
            centerLat: centerLat,
            centerLon: centerLon,
            zoom: zoom,
            bbox: bbox,
            trails: resolvedTrails,
            trailCount: trailCount ?? resolvedTrails.count,
            totalMi: totalMi ?? resolvedTrails.reduce(0) { $0 + $1.distanceMi },
            // Stamp `now` when the row didn't carry a date so freshly-fetched
            // areas have a real cache timestamp. Without this, every disk-
            // cached Area read back as cachedAt=nil → staleness=∞ → a silent
            // background refresh on every open, which non-deterministically
            // re-shuffled trail IDs and double-counted completions.
            cachedAt: cachedAt.flatMap { formatter.date(from: $0) } ?? Date()
        )
    }
}

extension Area {
    /// Returns a copy of this Area carrying `newRawTrails` as its
    /// in-memory raw geometry. Used by `AreaDataService` right after
    /// decimation so the cached Area has both the decimated render
    /// set (`trails`) and the dense pre-decimation set (`rawTrails`)
    /// for coverage / grid / halo consumers.
    func with(rawTrails newRawTrails: [Trail]) -> Area {
        Area(
            id: id,
            name: name,
            subtitle: subtitle,
            centerLat: centerLat,
            centerLon: centerLon,
            zoom: zoom,
            bbox: bbox,
            trails: trails,
            trailCount: trailCount,
            totalMi: totalMi,
            cachedAt: cachedAt,
            rawTrails: newRawTrails
        )
    }

    /// Returns a copy of this Area with each Trail's polyline segments
    /// run through Douglas-Peucker simplification. Used by
    /// `AreaDataService` before storing in the in-memory cache to cut
    /// per-frame MapKit cost on dense areas. On-disk and CDN data are
    /// kept raw — decimation epsilon can be tuned without invalidating
    /// any persisted cache.
    func withDecimatedSegments(epsilonMeters: Double) -> Area {
        let decimatedTrails = trails.map { trail -> Trail in
            let newSegments = trail.segments.map {
                PolylineDecimator.decimate($0, epsilonMeters: epsilonMeters)
            }
            return Trail(
                id: trail.id,
                name: trail.name,
                distanceMi: trail.distanceMi,
                difficulty: trail.difficulty,
                segments: newSegments
            )
        }
        return Area(
            id: id,
            name: name,
            subtitle: subtitle,
            centerLat: centerLat,
            centerLon: centerLon,
            zoom: zoom,
            bbox: bbox,
            trails: decimatedTrails,
            trailCount: trailCount,
            totalMi: totalMi,
            cachedAt: cachedAt
        )
    }

    /// Build an AreaSilhouette from this area's actual trails. Used at
    /// runtime to override the bundled silhouettes.json — the bundle is
    /// generated by a separate Python script that's only re-run periodically,
    /// so its per-line difficulty colors drift relative to whatever Overpass
    /// returns when the iOS app fetches the area. Reusing the live trail
    /// data keeps the AreaCard art and difficulty mix in sync with what
    /// the user sees inside AreaView.
    var computedSilhouette: AreaSilhouette {
        var lines: [SilhouetteLine] = []
        var minLat = Double.infinity, minLon = Double.infinity
        var maxLat = -Double.infinity, maxLon = -Double.infinity
        for trail in trails {
            let d: String
            switch trail.difficulty {
            case .easy:     d = "e"
            case .moderate: d = "m"
            case .hard:     d = "h"
            }
            for segment in trail.segments where segment.count >= 2 {
                lines.append(SilhouetteLine(d: d, p: segment))
                for pt in segment where pt.count >= 2 {
                    if pt[0] < minLat { minLat = pt[0] }
                    if pt[0] > maxLat { maxLat = pt[0] }
                    if pt[1] < minLon { minLon = pt[1] }
                    if pt[1] > maxLon { maxLon = pt[1] }
                }
            }
        }
        guard minLat.isFinite, minLon.isFinite else {
            return AreaSilhouette(b: [], l: [])
        }
        return AreaSilhouette(b: [minLon, minLat, maxLon, maxLat], l: lines)
    }
}
