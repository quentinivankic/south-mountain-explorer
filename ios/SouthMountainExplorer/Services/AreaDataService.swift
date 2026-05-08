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

        // Try disk cache first
        if let cached = loadIndexFromDisk() {
            summaries = cached
            // Refresh in background
            Task { await fetchAndCacheIndex() }
            return
        }

        // Fall back to bundle
        if let bundled = loadIndexFromBundle() {
            summaries = bundled
            return
        }

        // Last resort: fetch from network index if hosted
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

    private func loadIndexFromDisk() -> [AreaSummary]? {
        decodeIndex(from: indexDiskURL)
    }

    private func decodeIndex(from url: URL) -> [AreaSummary]? {
        guard let data = try? Data(contentsOf: url),
              let tuples = try? JSONDecoder().decode([[JSONValue]].self, from: data)
        else { return nil }
        return tuples.compactMap { AreaSummary(tuple: $0) }
    }

    private func fetchAndCacheIndex() async {
        // The areas index is bundled — no remote URL by default.
        // If you host it externally, set the URL here.
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

    @discardableResult
    private func fetchAndCacheArea(id: String) async -> Area? {
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
            return area
        } catch {
            return nil
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
