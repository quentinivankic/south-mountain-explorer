import Foundation

// Caches the bundled area index and per-area full data fetched from Supabase.
// The bundled index.json lives at Resources/areas-index.json (copy from public/areas/index.json).
@MainActor
@Observable
final class AreaDataService {
    static let shared = AreaDataService()

    private(set) var summaries: [AreaSummary] = []
    private(set) var isLoadingIndex = false

    // In-memory cache of full Area objects keyed by id
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

        // Supabase-format cache (filtered, has trail counts)
        if let cached = loadSummariesFromDisk() {
            summaries = cached
            Task { await fetchAndCacheIndex() }
            return
        }

        // Tuple-format disk cache (legacy)
        if let cached = loadIndexFromDisk() {
            summaries = cached
            Task { await fetchAndCacheIndex() }
            return
        }

        // Bundled fallback — then immediately refresh from Supabase
        if let bundled = loadIndexFromBundle() {
            summaries = bundled
            Task { await fetchAndCacheIndex() }
            return
        }

        await fetchAndCacheIndex()
    }

    private func loadIndexFromBundle() -> [AreaSummary]? {
        guard let url = Bundle.main.url(forResource: "areas-index", withExtension: "json") else {
            return nil
        }
        return decodeIndex(from: url)
    }

    private var indexDiskURL: URL {
        cacheDir.appendingPathComponent("index-v2.json")
    }

    private var summariesDiskURL: URL {
        cacheDir.appendingPathComponent("summaries-v2.json")
    }

    private func loadIndexFromDisk() -> [AreaSummary]? {
        decodeIndex(from: indexDiskURL)
    }

    private func loadSummariesFromDisk() -> [AreaSummary]? {
        guard let data = try? Data(contentsOf: summariesDiskURL) else { return nil }
        return try? JSONDecoder().decode([AreaSummary].self, from: data)
    }

    private func saveSummariesToDisk(_ s: [AreaSummary]) {
        guard let data = try? JSONEncoder().encode(s) else { return }
        try? data.write(to: summariesDiskURL)
    }

    private func decodeIndex(from url: URL) -> [AreaSummary]? {
        guard let data = try? Data(contentsOf: url),
              let tuples = try? JSONDecoder().decode([[JSONValue]].self, from: data)
        else { return nil }
        return tuples.compactMap { AreaSummary(tuple: $0) }
    }

    private func fetchAndCacheIndex() async {
        struct IndexRow: Codable {
            let id: String
            let trailCount: Int?
            let totalMi: Double?
            enum CodingKeys: String, CodingKey {
                case id
                case trailCount = "trail_count"
                case totalMi = "total_mi"
            }
        }
        do {
            let rows: [IndexRow] = try await supabase
                .from("areas")
                .select("id, trail_count, total_mi")
                .not("trails", operator: .init(rawValue: "is")!, value: "null")
                .execute()
                .value

            // Build a lookup of Supabase-known trail counts
            let lookup = Dictionary(uniqueKeysWithValues: rows.map { ($0.id, $0) })

            // Enrich existing summaries and filter out areas confirmed to have no trails.
            // Areas not in Supabase at all are kept (data may not be loaded yet).
            let enriched = summaries.compactMap { summary -> AreaSummary? in
                if let row = lookup[summary.id] {
                    var s = summary
                    s.trailCount = row.trailCount
                    s.totalMi = row.totalMi
                    return s
                }
                return summary  // not in Supabase yet — keep it
            }
            summaries = enriched
            saveSummariesToDisk(enriched)
        } catch {
            // Network unavailable — keep using bundled/cached index
        }
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
            .prefix(limit)
            .map { $0 }
    }

    // MARK: - Full Area Data

    func area(id: String) async -> Area? {
        if let cached = areaCache[id] { return cached }

        // Check disk cache
        if let onDisk = loadAreaFromDisk(id: id) {
            areaCache[id] = onDisk
            // Refresh in background if stale
            let staleness = Date().timeIntervalSince(onDisk.cachedAt ?? .distantPast)
            if staleness > 30 * 24 * 3600 {
                Task { await fetchAndCacheArea(id: id) }
            }
            return onDisk
        }

        // Deduplicate concurrent fetches
        if let existing = loadingTasks[id] {
            return await existing.value
        }
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
            if staleness > 30 * 24 * 3600 { Task { await fetchAndCacheArea(id: id) } }
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
        do {
            let row: AreaRow = try await supabase
                .from("areas")
                .select("id, name, state, center_lat, center_lon, zoom, bbox, trails, trail_count, total_mi, cached_at")
                .eq("id", value: id)
                .single()
                .execute()
                .value
            let area = row.toArea()
            areaCache[id] = area
            saveAreaToDisk(area)
            return (area, nil)
        } catch {
            return (nil, error.localizedDescription)
        }
    }

    // MARK: - Disk persistence for full area data

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
}
