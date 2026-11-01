import Foundation

// SPIKE (debug-only): USGS Topo offline base map. Remove by deleting the
// Utilities/UsgsTopo folder, Views/Settings/UsgsTopoSettingsView.swift, the
// three `UsgsTopo` hooks in MapKitMapView.swift and SettingsView.swift.

enum UsgsTopoMode: String, CaseIterable, Identifiable {
    case off, whenOffline, always
    var id: String { rawValue }
    var title: String {
        switch self {
        case .off: return "Off"
        case .whenOffline: return "Topo when offline"
        case .always: return "Topo always"
        }
    }
}

struct UsgsTopoTile: Hashable {
    let z: Int, x: Int, y: Int
}

enum UsgsTopoMath {
    /// South Mountain Park and Preserve bbox (W,S,E,N).
    static let southMountainBBox = (west: -112.163, south: 33.296, east: -111.976, north: 33.374)
    static let minZoom = 8
    static let maxZoom = 16
    static let attribution = "Basemap: USGS The National Map · Trails © OpenStreetMap contributors"
    static let tileURLBase = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile"

    static func tileX(lon: Double, z: Int) -> Int {
        let n = Double(1 << z)
        return min(max(Int(floor((lon + 180.0) / 360.0 * n)), 0), (1 << z) - 1)
    }

    static func tileY(lat: Double, z: Int) -> Int {
        let n = Double(1 << z)
        let r = lat * .pi / 180
        let v = (1 - log(tan(r) + 1 / cos(r)) / .pi) / 2 * n
        return min(max(Int(floor(v)), 0), (1 << z) - 1)
    }

    /// Inclusive slippy tile range covering the bbox at zoom z.
    static func tileRange(west: Double, south: Double, east: Double, north: Double, z: Int)
        -> (x: ClosedRange<Int>, y: ClosedRange<Int>) {
        let x0 = tileX(lon: west, z: z), x1 = tileX(lon: east, z: z)
        let y0 = tileY(lat: north, z: z), y1 = tileY(lat: south, z: z)
        return (min(x0, x1)...max(x0, x1), min(y0, y1)...max(y0, y1))
    }

    static func tiles(west: Double, south: Double, east: Double, north: Double,
                      zooms: ClosedRange<Int>) -> [UsgsTopoTile] {
        var out: [UsgsTopoTile] = []
        for z in zooms {
            let r = tileRange(west: west, south: south, east: east, north: north, z: z)
            for x in r.x { for y in r.y { out.append(UsgsTopoTile(z: z, x: x, y: y)) } }
        }
        return out
    }

    static func southMountainTiles() -> [UsgsTopoTile] {
        let b = southMountainBBox
        return tiles(west: b.west, south: b.south, east: b.east, north: b.north, zooms: minZoom...maxZoom)
    }

    /// Relative path of a stored tile: "z/x/y.tile".
    static func relativePath(_ t: UsgsTopoTile) -> String { "\(t.z)/\(t.x)/\(t.y).tile" }

    /// USGS (ArcGIS cache) URL order is z/y/x.
    static func remoteURL(_ t: UsgsTopoTile) -> URL {
        URL(string: "\(tileURLBase)/\(t.z)/\(t.y)/\(t.x)")!
    }

    /// Whether the topo overlay should be on the map.
    static func shouldShow(mode: UsgsTopoMode, pathSatisfied: Bool, hasTiles: Bool) -> Bool {
        guard hasTiles else { return false }
        switch mode {
        case .off: return false
        case .always: return true
        case .whenOffline: return !pathSatisfied
        }
    }
}
