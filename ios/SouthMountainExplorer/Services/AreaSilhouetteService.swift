import Foundation
import OSLog

/// Signpost log for silhouette fetch timing — same shape as
/// `AreaDataService.areaLoadLog` so the Send Diagnostics bundle
/// shows both lookup paths interleaved.
private let silhouetteLoadLog = OSLog(subsystem: "com.trekdex.app", category: "silhouetteLoad")

/// Mirror of `AreaDataService` for per-area silhouettes. The
/// previous implementation bulk-loaded a 45 MB bundled
/// `silhouettes.json` synchronously at app launch. With area
/// coverage growing past Arizona+California, that bundle would
/// have ballooned past Apple's 200 MB cellular-download warning,
/// so silhouettes now live on R2 alongside the geom files —
/// one JSON per area, fetched on demand, cached on disk for
/// instant subsequent reads.
///
/// Two-tier API:
///
/// - `cachedSilhouette(for:)` — synchronous; returns whatever's
///   in memory right now or `nil`. The view layer calls this
///   on every render pass; rendering nil → gradient fallback,
///   non-nil → the trail-line canvas.
/// - `silhouette(for:) async` — kicks the load if needed (memory
///   → disk cache → R2), updates the in-memory store on
///   success, and returns the result. Views call this from a
///   `.task` so SwiftUI re-renders once the silhouette lands.
///
/// In-flight de-dupe via `loadingTasks` prevents concurrent
/// fetches for the same area from multiplying network work
/// (e.g. when `HomeView.prefetchVisibleAreas` and the per-card
/// `.task` both ask for the same id within the same frame).
@MainActor
@Observable
final class AreaSilhouetteService {
    static let shared = AreaSilhouetteService()

    private(set) var byId: [String: AreaSilhouette] = [:]
    private var loadingTasks: [String: Task<AreaSilhouette?, Never>] = [:]

    /// Same custom domain + bucket as the geom files; per-area
    /// silhouettes live under the `silhouettes/` prefix on the
    /// same `trekdex-areas` bucket so a single API token covers
    /// both datasets and there's only one hostname to swap if
    /// the CDN ever changes.
    private let cdnBaseURL = "https://cdn.trekdex.app/silhouettes"

    private let cacheDir: URL = {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("silhouettes", isDirectory: true)
    }()

    private init() {
        try? FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
    }

    /// Synchronous read for the SwiftUI render pass — never
    /// blocks. View code pairs this with a `.task` that calls
    /// the async variant, so the next render returns the loaded
    /// silhouette automatically.
    func cachedSilhouette(for areaId: String) -> AreaSilhouette? {
        byId[areaId]
    }

    /// Async load: memory → on-disk cache → R2. Returns `nil`
    /// only if every layer fails (network error, parse error,
    /// area genuinely missing from R2). Idempotent and safe to
    /// call repeatedly; in-flight requests for the same id are
    /// deduplicated.
    @discardableResult
    func silhouette(for areaId: String) async -> AreaSilhouette? {
        if let hit = byId[areaId] { return hit }
        if let inflight = loadingTasks[areaId] { return await inflight.value }

        let task = Task<AreaSilhouette?, Never> { [weak self] in
            guard let self else { return nil }
            // Disk cache first — survives across cold launches
            // until iOS evicts the caches directory under
            // low-disk pressure.
            if let fromDisk = self.readDiskCache(areaId: areaId) {
                self.byId[areaId] = fromDisk
                return fromDisk
            }
            // R2 fetch.
            guard let fromCdn = await self.fetchFromCdn(areaId: areaId) else {
                return nil
            }
            self.byId[areaId] = fromCdn
            self.writeDiskCache(areaId: areaId, silhouette: fromCdn)
            return fromCdn
        }
        loadingTasks[areaId] = task
        let result = await task.value
        loadingTasks[areaId] = nil
        return result
    }

    // MARK: - Disk cache

    private func diskURL(areaId: String) -> URL {
        cacheDir.appendingPathComponent("\(areaId).json")
    }

    private func readDiskCache(areaId: String) -> AreaSilhouette? {
        let url = diskURL(areaId: areaId)
        guard let data = try? Data(contentsOf: url),
              let decoded = try? JSONDecoder().decode(AreaSilhouette.self, from: data)
        else { return nil }
        return decoded
    }

    private func writeDiskCache(areaId: String, silhouette: AreaSilhouette) {
        guard let data = try? JSONEncoder().encode(silhouette) else { return }
        try? data.write(to: diskURL(areaId: areaId), options: .atomic)
    }

    // MARK: - CDN fetch

    private func fetchFromCdn(areaId: String) async -> AreaSilhouette? {
        let signpostID = OSSignpostID(log: silhouetteLoadLog)
        os_signpost(.begin, log: silhouetteLoadLog, name: "fetchFromCdn", signpostID: signpostID, "%{public}s", areaId)
        defer { os_signpost(.end, log: silhouetteLoadLog, name: "fetchFromCdn", signpostID: signpostID) }

        guard let url = URL(string: "\(cdnBaseURL)/\(areaId).json") else { return nil }
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return nil
            }
            return try JSONDecoder().decode(AreaSilhouette.self, from: data)
        } catch {
            return nil
        }
    }
}
