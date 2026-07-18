import Foundation

/// A trailhead parking lot for an area, from OSM `amenity=parking`
/// (see `scripts/add-parking.py` + `docs/parking.md`). Decoded from the
/// area geom's `parking` array; absent for areas not yet enriched.
struct ParkingLot: Codable, Sendable, Hashable {
    let lat: Double
    let lon: Double
    let name: String?
    /// true = paid, false = free, nil = unknown (OSM had no `fee` tag).
    let fee: Bool?
    /// true when a nearby OSM `highway=trailhead` corroborated this lot —
    /// higher confidence that it actually serves the trail network.
    let trailhead: Bool?
    /// Provenance of this pin, written by `scripts/add-parking.py`:
    /// nil/"osm" = OpenStreetMap parking (default); "blm"/"usfs" = a federal
    /// agency TRAILHEAD point (drawn as a trailhead marker, not a parking "P");
    /// "nps" = an NPS public parking lot. Drives the map glyph + attribution.
    let source: String?
}

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
    /// Trailhead parking lots (OSM `amenity=parking`), when the area geom has
    /// been enriched. `var … = nil` so absent JSON decodes to nil and existing
    /// memberwise-init call sites compile unchanged (same trick as
    /// `rawTrails`), but it IS in `CodingKeys` so it decodes from the geom.
    var parking: [ParkingLot]? = nil
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
        case id, name, subtitle, zoom, bbox, trails, parking
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
    /// Trailhead parking lots, when the geom has been enriched (nil otherwise).
    /// Must be `var`, not `let`: a `let` with a default value is treated as a
    /// fixed constant and EXCLUDED from synthesized Codable (it would never
    /// decode). `var … = nil` both decodes AND stays out of the memberwise
    /// init, so the manual `AreaRow(...)` stubs compile unchanged.
    var parking: [ParkingLot]? = nil

    enum CodingKeys: String, CodingKey {
        case id, name, state, zoom, bbox, trails, parking
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
            cachedAt: cachedAt.flatMap { formatter.date(from: $0) } ?? Date(),
            parking: parking
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
            parking: parking,
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
                segments: newSegments,
                gainFt: trail.gainFt
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
            cachedAt: cachedAt,
            parking: parking
        )
    }

    /// Build an AreaSilhouette from this area's actual trails. Used at
    /// runtime to override the R2-hosted silhouette — the silhouette
    /// file is generated by a separate Python script that's only re-run
    /// periodically, so its per-line difficulty colors drift relative
    /// to whatever Overpass returns when the iOS app fetches the area.
    /// Reusing the live trail data keeps the AreaCard art and
    /// difficulty mix in sync with what the user sees inside AreaView.
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

extension Area {
    /// The ≤`max` parking lots nearest the given trail's ENDPOINTS, within
    /// `thresholdMeters`. Endpoints (each segment's start/end) are where you
    /// actually access a trail, so a lot near one answers "where do I park for
    /// this trail." Returns empty when the area has no parking or nothing is
    /// close enough — the map then draws nothing, which is the intended
    /// declutter (parking shows ONLY for the selected trail, never while
    /// browsing). One source of truth for both the map pins and the zoom frame.
    func nearestParking(for trail: Trail, max: Int = 3,
                        thresholdMeters: Double = 805) -> [ParkingLot] {
        guard let lots = parking, !lots.isEmpty else { return [] }
        var ends: [(Double, Double)] = []
        for seg in trail.segments {
            if let f = seg.first, f.count >= 2 { ends.append((f[0], f[1])) }
            if let l = seg.last, l.count >= 2 { ends.append((l[0], l[1])) }
        }
        guard !ends.isEmpty else { return [] }
        func meters(_ aLat: Double, _ aLon: Double, _ bLat: Double, _ bLon: Double) -> Double {
            let R = 6_371_000.0
            let dLat = (bLat - aLat) * .pi / 180, dLon = (bLon - aLon) * .pi / 180
            let la1 = aLat * .pi / 180, la2 = bLat * .pi / 180
            let h = sin(dLat / 2) * sin(dLat / 2)
                + cos(la1) * cos(la2) * sin(dLon / 2) * sin(dLon / 2)
            return 2 * R * asin(min(1, sqrt(h)))
        }
        return lots.compactMap { lot -> (ParkingLot, Double)? in
            let d = ends.map { meters(lot.lat, lot.lon, $0.0, $0.1) }.min() ?? .infinity
            return d <= thresholdMeters ? (lot, d) : nil
        }
        .sorted { $0.1 < $1.1 }
        .prefix(max)
        .map { $0.0 }
    }
}
