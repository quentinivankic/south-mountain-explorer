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

    enum CodingKeys: String, CodingKey {
        case id, name, state, zoom, bbox, trails
        case centerLat = "center_lat"
        case centerLon = "center_lon"
        case trailCount = "trail_count"
        case totalMi = "total_mi"
        case cachedAt = "cached_at"
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
            cachedAt: cachedAt.flatMap { formatter.date(from: $0) }
        )
    }
}
