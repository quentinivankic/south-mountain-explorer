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
    /// Areas already background-revalidated this session, so repeated
    /// renders of the same card don't stack redundant R2 fetches.
    private var revalidated: Set<String> = []

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
                // Background-revalidate against R2 so a regenerated
                // silhouette (re-curation, lifted trail cap) replaces the
                // stale disk copy. Without this the disk cache is terminal
                // and card art freezes at whatever first landed, even as
                // the trail geom refreshes — AreaDataService already does
                // the equivalent staleness re-fetch for geom. Fire-and-
                // forget: the disk copy is already returned, so a change
                // just swaps in on a later render via @Observable.
                self.revalidate(areaId: areaId)
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

    // MARK: - Revalidation

    /// Background-refresh an already-cached silhouette against R2. Runs at
    /// most once per area per session (`revalidated` guard) so re-rendering
    /// a card doesn't stack fetches. Re-fetches bypassing the local URL
    /// cache — otherwise the 24 h `max-age` on the object would just hand
    /// back the same stale bytes without a network trip — and only rewrites
    /// disk + memory when the art actually changed, so unchanged areas cost
    /// one cheap request and never churn the UI.
    private func revalidate(areaId: String) {
        guard revalidated.insert(areaId).inserted else { return }
        Task { [weak self] in
            guard let self else { return }
            guard let fresh = await self.fetchFromCdn(areaId: areaId, bypassCache: true) else {
                return
            }
            if self.byId[areaId] == fresh { return }   // no change — leave UI alone
            self.writeDiskCache(areaId: areaId, silhouette: fresh)
            self.byId[areaId] = fresh
        }
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

    /// - Parameter bypassCache: when true, ignores the local URL cache and
    ///   forces a network fetch (used by `revalidate`, which needs the live
    ///   object, not the 24 h-cached one). The cold-load path leaves it
    ///   false so a first open still benefits from any warm URL-cache entry.
    private func fetchFromCdn(areaId: String, bypassCache: Bool = false) async -> AreaSilhouette? {
        let signpostID = OSSignpostID(log: silhouetteLoadLog)
        os_signpost(.begin, log: silhouetteLoadLog, name: "fetchFromCdn", signpostID: signpostID, "%{public}s", areaId)
        defer { os_signpost(.end, log: silhouetteLoadLog, name: "fetchFromCdn", signpostID: signpostID) }

        guard let url = URL(string: "\(cdnBaseURL)/\(areaId).json") else { return nil }
        var request = URLRequest(url: url)
        if bypassCache { request.cachePolicy = .reloadIgnoringLocalCacheData }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return nil
            }
            return try JSONDecoder().decode(AreaSilhouette.self, from: data)
        } catch {
            return nil
        }
    }
}
