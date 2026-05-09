import Foundation

// Caches the bundled area index and per-area full data fetched from Overpass.
// The bundled index.json lives at Resources/areas-index.json.
@MainActor
@Observable
final class AreaDataService {
    static let shared = AreaDataService()

    private(set) var summaries: [AreaSummary] = []
    private(set) var isLoadingIndex = false

    private var areaCache: [String: Area] = [:]
    private var loadingTasks: [String: Task<Area?, Never>] = [:]

    private let cacheDir: URL = {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("areas", isDirectory: true)
    }()

    private init() {
        try? FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
        Task { await loadIndex() }
    }

    // MARK: - Area Index

    func loadIndex() async {
        guard summaries.isEmpty else { return }
        isLoadingIndex = true
        defer { isLoadingIndex = false }

        // Bundle is the source of truth (no remote backend). Prefer it over any
        // legacy disk cache, which can be stale if the bundle has changed (e.g.
        // areas removed by deduplication).
        if let bundled = loadIndexFromBundle() {
            summaries = bundled
            clearLegacyIndexCache()
            return
        }

        if let cached = loadSummariesFromDisk() {
            summaries = cached
            return
        }

        if let cached = loadIndexFromDisk() {
            summaries = cached
            return
        }
    }

    private func clearLegacyIndexCache() {
        try? FileManager.default.removeItem(at: indexDiskURL)
        try? FileManager.default.removeItem(at: summariesDiskURL)
    }

    private func loadIndexFromBundle() -> [AreaSummary]? {
        guard let url = Bundle.main.url(forResource: "areas-index", withExtension: "json") else { return nil }
        return decodeIndex(from: url)
    }

    private var indexDiskURL: URL { cacheDir.appendingPathComponent("index-v2.json") }
    private var summariesDiskURL: URL { cacheDir.appendingPathComponent("summaries-v2.json") }

    private func loadIndexFromDisk() -> [AreaSummary]? { decodeIndex(from: indexDiskURL) }

    private func loadSummariesFromDisk() -> [AreaSummary]? {
        guard let data = try? Data(contentsOf: summariesDiskURL) else { return nil }
        return try? JSONDecoder().decode([AreaSummary].self, from: data)
    }

    private func decodeIndex(from url: URL) -> [AreaSummary]? {
        guard let data = try? Data(contentsOf: url),
              let tuples = try? JSONDecoder().decode([[JSONValue]].self, from: data)
        else { return nil }
        // Hide areas with no trail data — they show as broken "0/0 trails"
        // cards with no silhouette. They re-appear automatically once a
        // future Build Trail Index run finds trails for them.
        return tuples
            .compactMap { AreaSummary(tuple: $0) }
            .filter { ($0.trailCount ?? 0) > 0 }
    }

    func search(_ query: String) -> [AreaSummary] {
        guard !query.isEmpty else { return summaries }
        let q = query.lowercased()
        return summaries.filter { $0.search.contains(q) }
    }

    func nearby(lat: Double, lon: Double, limit: Int = 20) -> [AreaSummary] {
        summaries
            .sorted { a, b in
                haversineDistanceMi(lat1: lat, lon1: lon, lat2: a.centerLat, lon2: a.centerLon)
                < haversineDistanceMi(lat1: lat, lon1: lon, lat2: b.centerLat, lon2: b.centerLon)
            }
            .prefix(limit).map { $0 }
    }

    // MARK: - Full Area Data

    func area(id: String) async -> Area? {
        if let cached = areaCache[id] { return cached }
        if let onDisk = loadAreaFromDisk(id: id) {
            areaCache[id] = onDisk
            let staleness = Date().timeIntervalSince(onDisk.cachedAt ?? .distantPast)
            if staleness > 24 * 3600 { Task { await fetchAndCacheArea(id: id) } }
            return onDisk
        }
        if let existing = loadingTasks[id] { return await existing.value }
        let task = Task<Area?, Never> { await fetchAndCacheArea(id: id) }
        loadingTasks[id] = task
        let result = await task.value
        loadingTasks.removeValue(forKey: id)
        return result
    }

    func areaWithError(id: String) async -> (area: Area?, error: String?) {
        if let cached = areaCache[id] { return (cached, nil) }
        if let onDisk = loadAreaFromDisk(id: id) {
            areaCache[id] = onDisk
            let staleness = Date().timeIntervalSince(onDisk.cachedAt ?? .distantPast)
            if staleness > 24 * 3600 { Task { await fetchAndCacheArea(id: id) } }
            return (onDisk, nil)
        }
        if let existing = loadingTasks[id] {
            let result = await existing.value
            return (result, result == nil ? "Fetch already in progress but returned no data." : nil)
        }
        return await fetchAndCacheAreaWithError(id: id)
    }

    @discardableResult
    private func fetchAndCacheArea(id: String) async -> Area? {
        await fetchAndCacheAreaWithError(id: id).area
    }

    private func fetchAndCacheAreaWithError(id: String) async -> (area: Area?, error: String?) {
        guard let summary = summaries.first(where: { $0.id == id }) else {
            return (nil, "Area not found in index.")
        }
        let stub = AreaRow(
            id: summary.id, name: summary.name, state: summary.subtitle,
            centerLat: summary.centerLat, centerLon: summary.centerLon,
            zoom: 13, bbox: nil, trails: nil, trailCount: nil, totalMi: nil, cachedAt: nil
        )
        do {
            let row = try await fetchFromOverpass(row: stub)
            let area = row.toArea()
            areaCache[id] = area
            saveAreaToDisk(area)
            return (area, nil)
        } catch {
            return (nil, error.localizedDescription)
        }
    }

    // Returns (relationId, bbox [w,s,e,n]) or nil
    private func nominatimLookup(name: String, state: String) async -> (relationId: Int, bbox: [Double])? {
        let place = state == "Denmark" ? "\(name), Denmark" : "\(name), \(state), USA"
        guard let encoded = place.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
              let url = URL(string: "https://nominatim.openstreetmap.org/search?q=\(encoded)&format=json&limit=1&featuretype=relation")
        else { return nil }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.setValue("SouthMountainExplorer/1.0", forHTTPHeaderField: "User-Agent")
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let results = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]],
              let first = results.first,
              first["osm_type"] as? String == "relation",
              let idStr = first["osm_id"] as? String,
              let id = Int(idStr)
        else { return nil }
        // Nominatim boundingbox: [s, n, w, e] as strings — convert to [w, s, e, n]
        var bbox: [Double] = []
        if let bb = first["boundingbox"] as? [String], bb.count == 4,
           let s = Double(bb[0]), let n = Double(bb[1]),
           let w = Double(bb[2]), let e = Double(bb[3]) {
            bbox = [w, s, e, n]
        }
        return (id, bbox)
    }

    private func fetchFromOverpass(row: AreaRow) async throws -> AreaRow {
        let query: String
        var parkBbox: [Double] = []
        if let bbox = row.bbox, bbox.count == 4 {
            let s = bbox[1], w = bbox[0], n = bbox[3], e = bbox[2]
            query = "[out:json][timeout:90];(way[\"highway\"~\"^(path|footway|track|bridleway)$\"](\(s),\(w),\(n),\(e)););out tags geom;"
            parkBbox = bbox
        } else if let result = await nominatimLookup(name: row.name, state: row.state) {
            let areaId = result.relationId + 3_600_000_000
            query = "[out:json][timeout:90];area(\(areaId))->.a;(way[\"highway\"~\"^(path|footway|track|bridleway)$\"](area.a););out tags geom;"
            parkBbox = result.bbox
        } else {
            let lat = row.centerLat, lon = row.centerLon, d = 0.10
            query = "[out:json][timeout:90];(way[\"highway\"~\"^(path|footway|track|bridleway)$\"](\(lat-d),\(lon-d),\(lat+d),\(lon+d)););out tags geom;"
        }

        let endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"
        ]
        var lastError: Error?
        for endpoint in endpoints {
            guard let url = URL(string: endpoint) else { continue }
            var req = URLRequest(url: url, timeoutInterval: 100)
            req.httpMethod = "POST"
            req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
            req.httpBody = ("data=" + query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)!).data(using: .utf8)
            do {
                let (data, _) = try await URLSession.shared.data(for: req)
                let trails = buildTrails(from: data, parkBbox: parkBbox)
                return AreaRow(
                    id: row.id, name: row.name, state: row.state,
                    centerLat: row.centerLat, centerLon: row.centerLon,
                    zoom: row.zoom, bbox: row.bbox,
                    trails: trails, trailCount: trails.count,
                    totalMi: trails.reduce(0) { $0 + $1.distanceMi },
                    cachedAt: row.cachedAt
                )
            } catch {
                lastError = error
            }
        }
        throw lastError ?? URLError(.unknown)
    }

    private func buildTrails(from data: Data, parkBbox: [Double] = []) -> [Trail] {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let elements = json["elements"] as? [[String: Any]] else { return [] }

        let ways = elements.filter {
            ($0["type"] as? String) == "way" &&
            (($0["geometry"] as? [[String: Any]])?.count ?? 0) > 1
        }

        var namedNodes = Set<String>()
        for w in ways {
            guard let tags = w["tags"] as? [String: String],
                  let name = tags["name"]?.trimmingCharacters(in: .whitespaces), !name.isEmpty,
                  !isRoadLike(name: name, tags: tags),
                  let geom = w["geometry"] as? [[String: Any]] else { continue }
            for p in geom {
                if let lat = p["lat"] as? Double, let lon = p["lon"] as? Double {
                    namedNodes.insert(nodeKey(lat: lat, lon: lon))
                }
            }
        }

        var byName: [String: (tags: [String: String]?, segments: [[[Double]]])] = [:]
        for w in ways {
            let tags = w["tags"] as? [String: String]
            let rawName = tags?["name"]?.trimmingCharacters(in: .whitespaces) ?? ""

            if isRoadLike(name: rawName, tags: tags) { continue }

            guard let geom = w["geometry"] as? [[String: Any]] else { continue }
            if rawName.isEmpty {
                let endpoints = [geom.first, geom.last].compactMap { $0 }
                let touches = endpoints.contains { p in
                    guard let lat = p["lat"] as? Double, let lon = p["lon"] as? Double else { return false }
                    return neighborKeys(lat: lat, lon: lon).contains { namedNodes.contains($0) }
                }
                if !touches { continue }
            }
            let name = rawName.isEmpty ? "Unnamed \(w["id"] ?? 0)" : rawName
            var coords = geom.compactMap { p -> [Double]? in
                guard let lat = p["lat"] as? Double, let lon = p["lon"] as? Double else { return nil }
                return [lat, lon]
            }
            if parkBbox.count == 4 { coords = clipToBbox(coords, bbox: parkBbox) }
            guard coords.count >= 2 else { continue }
            if byName[name] == nil { byName[name] = (tags, []) }
            byName[name]!.segments.append(coords)
        }

        var trails: [Trail] = []
        for (name, info) in byName {
            let totalMi = info.segments.reduce(0.0) { $0 + segmentMiles($1) }
            if totalMi < 0.59 { continue }
            let id = slugify(name) + "-\(trails.count)"
            trails.append(Trail(
                id: id, name: name,
                distanceMi: Double(String(format: "%.2f", totalMi))!,
                difficulty: difficulty(tags: info.tags, mi: totalMi),
                segments: info.segments
            ))
        }
        return trails.sorted { $0.distanceMi > $1.distanceMi }
    }

    private func nodeKey(lat: Double, lon: Double) -> String {
        let cell = 0.0001
        return "\(Int((lat / cell).rounded())):\(Int((lon / cell).rounded()))"
    }

    private func neighborKeys(lat: Double, lon: Double) -> [String] {
        let cell = 0.0001
        let r = Int((lat / cell).rounded())
        let c = Int((lon / cell).rounded())
        return (-1...1).flatMap { dr in (-1...1).map { dc in "\(r+dr):\(c+dc)" } }
    }

    private func segmentMiles(_ coords: [[Double]]) -> Double {
        var meters = 0.0
        for i in 1..<coords.count {
            let (la1, lo1) = (coords[i-1][0], coords[i-1][1])
            let (la2, lo2) = (coords[i][0], coords[i][1])
            let R = 6_371_000.0
            let dLat = (la2 - la1) * .pi / 180
            let dLon = (lo2 - lo1) * .pi / 180
            let a = sin(dLat/2)*sin(dLat/2) + cos(la1 * .pi/180)*cos(la2 * .pi/180)*sin(dLon/2)*sin(dLon/2)
            meters += R * 2 * atan2(sqrt(a), sqrt(1-a))
        }
        return meters / 1609.344
    }

    private func isRoadLike(name: String, tags: [String: String]?) -> Bool {
        guard tags?["highway"] == "track" else { return false }
        let roadWords = ["road", "drive", "avenue", "canal", "drain", "ditch", "boulevard", "highway", "freeway"]
        let lower = name.lowercased()
        if roadWords.contains(where: { lower.contains($0) }) { return true }
        if tags?["motor_vehicle"] == "yes" || tags?["motorcar"] == "yes" { return true }
        if tags?["access"] == "private" { return true }
        return false
    }

    private func clipToBbox(_ coords: [[Double]], bbox: [Double]) -> [[Double]] {
        let buf = 0.02
        let w = bbox[0] - buf, s = bbox[1] - buf, e = bbox[2] + buf, n = bbox[3] + buf
        return coords.filter { $0[0] >= s && $0[0] <= n && $0[1] >= w && $0[1] <= e }
    }

    private func difficulty(tags: [String: String]?, mi: Double) -> Difficulty {
        if let sac = tags?["sac_scale"], sac != "hiking" { return .hard }
        if mi > 4 { return .hard }
        if mi > 2 || tags?["trail_visibility"] == "intermediate" { return .moderate }
        return .easy
    }

    private func slugify(_ s: String) -> String {
        s.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { !$0.isEmpty }
            .joined(separator: "-")
            .prefix(60).description
    }

    // MARK: - Disk persistence

    private func areaDiskURL(id: String) -> URL {
        cacheDir.appendingPathComponent("\(id).json")
    }

    private func loadAreaFromDisk(id: String) -> Area? {
        let url = areaDiskURL(id: id)
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(Area.self, from: data)
    }

    private func saveAreaToDisk(_ area: Area) {
        guard let data = try? JSONEncoder().encode(area) else { return }
        try? data.write(to: areaDiskURL(id: area.id))
    }

    func cachedArea(id: String) -> Area? {
        areaCache[id] ?? loadAreaFromDisk(id: id)
    }

    func clearAreaCache() {
        areaCache.removeAll()
        if let files = try? FileManager.default.contentsOfDirectory(at: cacheDir, includingPropertiesForKeys: nil) {
            for file in files where file.pathExtension == "json" && file.lastPathComponent != "index-v2.json" && file.lastPathComponent != "summaries-v2.json" {
                try? FileManager.default.removeItem(at: file)
            }
        }
    }
}
