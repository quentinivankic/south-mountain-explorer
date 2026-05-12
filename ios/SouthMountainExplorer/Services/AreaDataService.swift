import Foundation

/// jsDelivr CDN mirror of the precomputed per-area trail geometry. The
/// workflow writes `public/areas/geom/<id>.json` on every build; jsDelivr
/// serves those files from GitHub with proper edge caching. Branch ref is
/// pinned here so we can promote a build atomically (and roll back by
/// changing one constant). jsDelivr caches branch URLs roughly 12 h, so
/// a workflow push doesn't go live instantly — fine for our cadence.
private let cdnBaseURL = "https://cdn.jsdelivr.net/gh/quentinivankic/south-mountain-explorer@main/public/areas/geom"

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
    /// Round-robin starting index for Overpass endpoints. Bumped per fetch so
    /// successive retries of the same area hit different mirrors instead of
    /// hammering a single one that just rate-limited us.
    private var endpointCursor = 0

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

    /// Pull favorites and the user's 10 most-recently-opened areas down
    /// to disk so they're available offline. Reuses the public
    /// `area(id:)` path, which short-circuits anything fresher than 24 h
    /// — so on warm caches this is essentially free. Progress callback
    /// fires per item with `(completed, total)`; pass `nil` for silent
    /// background runs (the cold-launch path).
    func prefetchOffline(progress: ((Int, Int) async -> Void)? = nil) async {
        let favorites = FavoritesService.shared.favoriteAreas.map(\.id)
        let recents = ActivityService.shared.areaOpenedAt
            .sorted { $0.value > $1.value }
            .prefix(10)
            .map(\.key)
        // Stable de-dupe: favorites first (the user's explicit signal),
        // then any recents that aren't already a favorite. Keeps the
        // Settings progress count climbing predictably and means a user
        // with overlap doesn't double-fetch.
        var seen = Set<String>()
        var targets: [String] = []
        for id in favorites + recents {
            if seen.insert(id).inserted { targets.append(id) }
        }
        let total = targets.count
        await progress?(0, total)
        for (i, id) in targets.enumerated() {
            _ = await area(id: id)
            await progress?(i + 1, total)
            // Yield so SwiftUI can render between cache-warm items;
            // without it the loop completes inside one render tick and
            // the count appears to flash straight to N of N.
            await Task.yield()
        }
    }

    // MARK: - Nearby-Radius Prefetch

    /// UserDefaults keys for the nearby-prefetch cooldown / movement check.
    private static let lastNearbyLatKey = StorageKeys.prefetchNearbyLastLat
    private static let lastNearbyLonKey = StorageKeys.prefetchNearbyLastLon

    /// Pull every area whose center is within `radiusMi` of the given
    /// coordinate down to disk. Skips anything already covered by
    /// `prefetchOffline` (favorites + recents) so the two callers can
    /// safely run back-to-back without double-fetching. Same per-item
    /// loop pattern as `prefetchOffline`.
    func prefetchNearby(
        centerLat: Double,
        centerLon: Double,
        radiusMi: Double,
        progress: ((Int, Int) async -> Void)? = nil
    ) async {
        let already = Set(
            FavoritesService.shared.favoriteAreas.map(\.id)
            + ActivityService.shared.areaOpenedAt
                .sorted { $0.value > $1.value }
                .prefix(10)
                .map(\.key)
        )
        let targets: [String] = summaries.compactMap { s in
            guard !already.contains(s.id) else { return nil }
            let d = haversineDistanceMi(
                lat1: centerLat, lon1: centerLon,
                lat2: s.centerLat, lon2: s.centerLon
            )
            return d <= radiusMi ? s.id : nil
        }
        let total = targets.count
        await progress?(0, total)
        for (i, id) in targets.enumerated() {
            _ = await area(id: id)
            await progress?(i + 1, total)
            await Task.yield()
        }
    }

    /// Orchestrator for the cold-launch / foreground-resume nearby
    /// prefetch. Returns `true` if a prefetch ran (or was already
    /// cache-fresh by the movement check), `false` if it was skipped
    /// because of network policy / no location / not enough movement.
    ///
    /// Movement check: skips if the user hasn't moved more than 25 mi
    /// since the last successful prefetch — keeps us from re-fetching
    /// the same 50-mi disc on every foreground transition.
    ///
    /// Network check: defaults to Wi-Fi only. Pass `force: true` from a
    /// user-initiated Settings button after they've confirmed cellular
    /// is OK.
    @discardableResult
    func runNearbyPrefetchIfAppropriate(
        radiusMi: Double = 50,
        movementThresholdMi: Double = 25,
        force: Bool = false,
        progress: ((Int, Int) async -> Void)? = nil
    ) async -> Bool {
        guard let loc = LocationService.shared.userLocation else { return false }
        if !force && NetworkService.shared.isExpensive { return false }

        let ud = UserDefaults.standard
        let lastLat = ud.object(forKey: Self.lastNearbyLatKey) as? Double
        let lastLon = ud.object(forKey: Self.lastNearbyLonKey) as? Double
        if !force, let lastLat, let lastLon {
            let moved = haversineDistanceMi(
                lat1: lastLat, lon1: lastLon,
                lat2: loc.latitude, lon2: loc.longitude
            )
            if moved < movementThresholdMi { return false }
        }

        await prefetchNearby(
            centerLat: loc.latitude,
            centerLon: loc.longitude,
            radiusMi: radiusMi,
            progress: progress
        )
        ud.set(loc.latitude, forKey: Self.lastNearbyLatKey)
        ud.set(loc.longitude, forKey: Self.lastNearbyLonKey)
        return true
    }

    // MARK: - Full Area Data

    func area(id: String) async -> Area? {
        if let cached = areaCache[id], !cached.trails.isEmpty { return cached }
        if let onDisk = loadAreaFromDisk(id: id), !onDisk.trails.isEmpty {
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
        // Treat 0-trail entries as cache misses so a polluted cache (legacy
        // data from before the empty-overwrite guard, or a one-off bad
        // fetch we managed to persist) doesn't pin the area to the empty
        // state. The next fetch will replace it with good data.
        if let cached = areaCache[id], !cached.trails.isEmpty { return (cached, nil) }
        if let onDisk = loadAreaFromDisk(id: id), !onDisk.trails.isEmpty {
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

        // CDN-first: precomputed per-area JSON gives us deterministic
        // trail ids and the same counts Browse shows. On 404 / network
        // error / empty payload we fall through to the live Overpass
        // path below, which still has its mirror-rotation + retry
        // safety net for areas not yet present in our build.
        if let cdnArea = await fetchFromCdn(id: id), !cdnArea.trails.isEmpty {
            areaCache[id] = cdnArea
            saveAreaToDisk(cdnArea)
            return (cdnArea, nil)
        }

        let stub = AreaRow(
            id: summary.id, name: summary.name, state: summary.subtitle,
            centerLat: summary.centerLat, centerLon: summary.centerLon,
            zoom: 13, bbox: nil, trails: nil, trailCount: nil, totalMi: nil, cachedAt: nil,
            osmRelationId: summary.osmRelationId
        )
        // Inline retry: Overpass occasionally returns an empty body (timeout
        // converted to 200, rate-limit slot, etc.). One trip ≈ a 1–3s "Trail
        // data didn't load" screen for the user; a quick second/third attempt
        // catches the transient case in the same load instead of pushing the
        // recovery onto a manual close-and-reopen.
        let maxAttempts = 3
        var attempt = 0
        var lastError: Error? = nil
        while attempt < maxAttempts {
            attempt += 1
            do {
                let row = try await fetchFromOverpass(row: stub)
                let area = row.toArea()
                if area.trails.isEmpty {
                    // Defensive: a flaky Overpass response can succeed with
                    // zero trails. Don't overwrite a previously-good cache
                    // with empty data.
                    if let existingMemory = areaCache[id], !existingMemory.trails.isEmpty {
                        return (existingMemory, nil)
                    }
                    if let existingDisk = loadAreaFromDisk(id: id), !existingDisk.trails.isEmpty {
                        areaCache[id] = existingDisk
                        return (existingDisk, nil)
                    }
                    if attempt < maxAttempts {
                        // No prior cache. Brief backoff and retry — the
                        // failure mode here is usually a one-shot upstream
                        // hiccup that resolves a second later.
                        try? await Task.sleep(for: .milliseconds(600 * attempt))
                        continue
                    }
                    // Final attempt also empty. Don't write to disk so the
                    // next open retries instead of caching the empty result.
                    areaCache[id] = area
                    return (area, nil)
                }
                areaCache[id] = area
                saveAreaToDisk(area)
                return (area, nil)
            } catch {
                lastError = error
                if attempt < maxAttempts {
                    try? await Task.sleep(for: .milliseconds(600 * attempt))
                }
            }
        }
        return (nil, lastError?.localizedDescription ?? "Could not load trail data.")
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

    /// Fetch the precomputed geometry for `id` from the jsDelivr CDN.
    /// Returns `nil` for any failure mode — 404 (area not in the build
    /// yet), non-2xx, network down, malformed JSON. Caller falls back to
    /// the live Overpass path, which has its own retry / mirror logic.
    private func fetchFromCdn(id: String) async -> Area? {
        guard let url = URL(string: "\(cdnBaseURL)/\(id).json") else { return nil }
        var req = URLRequest(url: url, timeoutInterval: 20)
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                return nil
            }
            let row = try JSONDecoder().decode(AreaRow.self, from: data)
            return row.toArea()
        } catch {
            return nil
        }
    }

    private func fetchFromOverpass(row: AreaRow) async throws -> AreaRow {
        let query: String
        var parkBbox: [Double] = []
        if let bbox = row.bbox, bbox.count == 4 {
            let s = bbox[1], w = bbox[0], n = bbox[3], e = bbox[2]
            query = "[out:json][timeout:90];(way[\"highway\"~\"^(path|footway|track|bridleway)$\"](\(s),\(w),\(n),\(e)););out tags geom;"
            parkBbox = bbox
        } else if let osmId = row.osmRelationId {
            // Fast path: bundled index already pinned the relation id, so
            // we query the same polygon Python used. Skipping Nominatim
            // entirely also drops a network round-trip and a rate-limit
            // sleep from every cold area open.
            let areaId = osmId + 3_600_000_000
            query = "[out:json][timeout:90];area(\(areaId))->.a;(way[\"highway\"~\"^(path|footway|track|bridleway)$\"](area.a););out tags geom;"
        } else if let result = await nominatimLookup(name: row.name, state: row.state) {
            let areaId = result.relationId + 3_600_000_000
            query = "[out:json][timeout:90];area(\(areaId))->.a;(way[\"highway\"~\"^(path|footway|track|bridleway)$\"](area.a););out tags geom;"
            // Intentionally don't set parkBbox here — Overpass's `area(id)`
            // already constrains results to the relation polygon.
        } else {
            let lat = row.centerLat, lon = row.centerLon, d = 0.10
            query = "[out:json][timeout:90];(way[\"highway\"~\"^(path|footway|track|bridleway)$\"](\(lat-d),\(lon-d),\(lat+d),\(lon+d)););out tags geom;"
        }

        let endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"
        ]
        // Rotate which endpoint we try first per fetch so a flapping mirror
        // doesn't poison every retry of the same area open.
        let start = endpointCursor % endpoints.count
        endpointCursor &+= 1
        let ordered = (0..<endpoints.count).map { endpoints[(start + $0) % endpoints.count] }

        var lastError: Error?
        var lastEmptyResult: AreaRow?
        for endpoint in ordered {
            guard let url = URL(string: endpoint) else { continue }
            var req = URLRequest(url: url, timeoutInterval: 100)
            req.httpMethod = "POST"
            req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
            req.httpBody = ("data=" + query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)!).data(using: .utf8)
            do {
                let (data, response) = try await URLSession.shared.data(for: req)
                // Overpass returns 429/504 (rate limit, gateway timeout) with
                // text/HTML bodies that JSON-parse to nothing. Without this
                // check we'd treat that as "successfully fetched, 0 trails"
                // and never try the fallback mirror.
                if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    lastError = URLError(.badServerResponse)
                    continue
                }
                // Overpass error responses sometimes come back as 200 with
                // {"remark": "runtime error: Query timed out ..."} and no
                // elements. Detect and fail through to the next endpoint.
                if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let remark = json["remark"] as? String,
                   remark.lowercased().contains("error") || remark.lowercased().contains("timed out") {
                    lastError = URLError(.timedOut)
                    continue
                }
                let trails = buildTrails(from: data, parkBbox: parkBbox)
                let result = AreaRow(
                    id: row.id, name: row.name, state: row.state,
                    centerLat: row.centerLat, centerLon: row.centerLon,
                    zoom: row.zoom, bbox: row.bbox,
                    trails: trails, trailCount: trails.count,
                    totalMi: trails.reduce(0) { $0 + $1.distanceMi },
                    cachedAt: row.cachedAt,
                    osmRelationId: row.osmRelationId
                )
                if !trails.isEmpty {
                    return result
                }
                // Empty result from a healthy-looking response. Could be a
                // genuinely empty area, but more often it's a quiet upstream
                // hiccup. Stash it and try the other endpoint before giving
                // up — if the second endpoint also returns empty we'll trust
                // that this area really has no trails right now.
                lastEmptyResult = result
            } catch {
                lastError = error
            }
        }
        if let empty = lastEmptyResult {
            return empty
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

        // Sort names before assigning IDs so the count-based suffix is
        // deterministic across fetches. Swift Dictionary iteration order is
        // randomized, so without this sort the same trail could get
        // "name-3" on one fetch and "name-7" on the next, scrambling
        // ProgressService completions when an area got silently re-fetched.
        var trails: [Trail] = []
        for name in byName.keys.sorted() {
            guard let info = byName[name] else { continue }
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
        // Reset the nearby-prefetch movement check so the next launch
        // re-sweeps the radius (otherwise we'd be in an inconsistent
        // state — UserDefaults says we've prefetched here but the
        // files are gone).
        UserDefaults.standard.removeObject(forKey: Self.lastNearbyLatKey)
        UserDefaults.standard.removeObject(forKey: Self.lastNearbyLonKey)
    }

    /// One downloaded area entry as surfaced in the Manage Downloads list.
    /// Pre-resolved name (from the in-memory index when available) and
    /// file size on disk so the UI can render rows without per-row IO.
    struct DownloadedArea: Identifiable, Hashable {
        let id: String
        let name: String
        let sizeBytes: Int
    }

    /// Enumerate every area JSON cached on disk. Names come from the
    /// loaded summaries index (`summaries`) — orphan cache files
    /// (whose area is no longer in the index) fall back to the id.
    /// Sorted by name for stable UI rendering.
    func downloadedAreas() -> [DownloadedArea] {
        let summariesById = Dictionary(uniqueKeysWithValues: summaries.map { ($0.id, $0) })
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: cacheDir,
            includingPropertiesForKeys: [.fileSizeKey]
        ) else { return [] }
        var rows: [DownloadedArea] = []
        for file in files where file.pathExtension == "json" {
            let last = file.lastPathComponent
            if last == "index-v2.json" || last == "summaries-v2.json" { continue }
            let id = String(last.dropLast(".json".count))
            let resourceValues = try? file.resourceValues(forKeys: [.fileSizeKey])
            let size = resourceValues?.fileSize ?? 0
            let name = summariesById[id]?.name ?? id
            rows.append(DownloadedArea(id: id, name: name, sizeBytes: size))
        }
        rows.sort { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        return rows
    }

    /// Remove a single area from the on-disk + in-memory cache. The
    /// next `area(id:)` call will re-fetch from the CDN. Used by the
    /// Manage Downloads list's swipe-to-delete.
    func removeDownloadedArea(id: String) {
        areaCache.removeValue(forKey: id)
        try? FileManager.default.removeItem(at: areaDiskURL(id: id))
    }
}
