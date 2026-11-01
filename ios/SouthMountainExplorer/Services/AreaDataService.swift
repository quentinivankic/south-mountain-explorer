import Foundation
import OSLog

/// Signpost log for measuring area-load timing. Inspect in Instruments
/// (Logging template) to see where slow opens spend their time:
/// `fetchFromCdn` is network + JSON parse; the `area(id:)` interval
/// wraps the whole disk-cache-or-fetch path.
private let areaLoadLog = OSLog(subsystem: "com.trekdex.app", category: "areaLoad")

/// CDN for the precomputed per-area trail geometry. Custom
/// domain bound to the Cloudflare R2 `trekdex-areas` bucket so
/// requests get Cloudflare's edge cache + no `.r2.dev` rate
/// limit. The previous `pub-...r2.dev` host hit Cloudflare's
/// rate limiter at TestFlight scale and started returning
/// 403 host_not_allowed for every fetch — moving to the custom
/// domain takes the rate-limit class out of the picture
/// entirely.
///
/// Auto-synced by `.github/workflows/sync-geom-to-r2.yml` —
/// fires on every commit that touches `public/areas/geom/**`
/// and on every successful `build-trail-index` run via the
/// explicit dispatch step.
private let cdnBaseURL = "https://cdn.trekdex.app"

// Caches the bundled area index and per-area full data fetched from Overpass.
// The bundled index.json lives at Resources/areas-index.json.
@MainActor
@Observable
final class AreaDataService {
    static let shared = AreaDataService()

    private(set) var summaries: [AreaSummary] = [] {
        didSet {
            // Keep the O(1) lookup index in step with the array. Built once per
            // index load instead of callers linear-scanning ~29,850 rows.
            summariesById = Dictionary(
                summaries.map { ($0.id, $0) },
                uniquingKeysWith: { first, _ in first }
            )
        }
    }
    private(set) var isLoadingIndex = false

    private var areaCache: [String: Area] = [:]
    private var loadingTasks: [String: Task<Area?, Never>] = [:]
    /// Round-robin starting index for Overpass endpoints. Bumped per fetch so
    /// successive retries of the same area hit different mirrors instead of
    /// hammering a single one that just rate-limited us.
    private var endpointCursor = 0

    private let cacheDir: URL
    private let cacheStore: AreaCacheStore

    private init() {
        let directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("areas", isDirectory: true)
        cacheDir = directory
        cacheStore = AreaCacheStore(cacheDirectory: directory)
        Task { await loadIndex() }
    }

    // MARK: - Duplicate / nested area aliases (see docs/adr/0002)

    /// `id -> canonical id` for areas hidden from Browse: identical twins whose
    /// trails the canonical also has, and nested re-listings a canonical
    /// subsumes. Loaded once from the bundled `area-aliases.json`. Hiding is
    /// provably lossless — the canonical is always a SUPERSET, so no trail and
    /// no completion is dropped (completion already crosses by geometry
    /// fingerprint, so a checkmark recorded under a hidden twin still shows on
    /// its canonical).
    @ObservationIgnored
    private lazy var aliasMap: [String: String] = AreaDataService.loadAliasMap()

    private struct AliasEntry: Decodable { let canonical: String; let kind: String }

    private static func loadAliasMap() -> [String: String] {
        guard let url = Bundle.main.url(forResource: "area-aliases", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let decoded = try? JSONDecoder().decode([String: AliasEntry].self, from: data)
        else { return [:] }
        return decoded.mapValues(\.canonical)
    }

    /// Canonical area id for `id` — returns `id` unchanged when it isn't hidden.
    /// Resolve a stored or linked area id through this so a hidden twin's data
    /// lands on the area the user can actually open.
    func canonicalId(for id: String) -> String { aliasMap[id] ?? id }

    /// True when `id` is hidden from Browse in favour of its canonical.
    func isHiddenAlias(_ id: String) -> Bool { aliasMap[id] != nil }

    /// Reverse of `aliasMap`: canonical id -> the hidden twin ids that resolve
    /// to it. Built once alongside `aliasMap`.
    @ObservationIgnored
    private lazy var aliasReverse: [String: [String]] = AreaDataService.reverseAliases(aliasMap)

    nonisolated static func reverseAliases(_ forward: [String: String]) -> [String: [String]] {
        var reverse: [String: [String]] = [:]
        for (id, canonical) in forward { reverse[canonical, default: []].append(id) }
        return reverse
    }

    /// `areaId` plus any hidden twins that resolve to it. Scope a hike filter
    /// through this so coverage and the walked-here trace recorded under a
    /// now-hidden twin surface on the canonical the user actually opens. Just
    /// `[areaId]` when the area has no twins.
    func areaAndTwins(_ areaId: String) -> Set<String> {
        AreaDataService.expandTwins(areaId, reverse: aliasReverse)
    }

    nonisolated static func expandTwins(_ areaId: String, reverse: [String: [String]]) -> Set<String> {
        var ids: Set<String> = [areaId]
        if let twins = reverse[areaId] { ids.formUnion(twins) }
        return ids
    }

    // MARK: - Area Index

    func loadIndex() async {
        guard summaries.isEmpty else { return }
        isLoadingIndex = true
        defer { isLoadingIndex = false }

        // 1. Read the freshest bytes we have on disk —
        //    AreaIndexService prefers an R2-cached copy from a
        //    prior session over the bundled snapshot. Both decode
        //    through the same tuple shape, so callers downstream
        //    don't care which source was used.
        if let data = AreaIndexService.shared.currentIndexData(),
           let parsed = await decodeIndexOffMain(from: data) {
            summaries = parsed
            clearLegacyIndexCache()
        } else if let cached = loadSummariesFromDisk() {
            summaries = cached
        } else if let cached = loadIndexFromDisk() {
            summaries = cached
        }

        // 2. Kick a background R2 revalidation. ETag / If-None-Match
        //    makes this a 304 in steady state. When the body
        //    changes, swap in the new summaries — the Browse list
        //    re-renders automatically via @Observable.
        //
        //    Fire-and-forget: step 1 already populated `summaries`,
        //    so the UI is responsive from launch; only a real
        //    body change triggers a refresh.
        Task { [weak self] in
            let updated = await AreaIndexService.shared.revalidate()
            guard updated, let self else { return }
            if let data = AreaIndexService.shared.currentIndexData(),
               let parsed = await self.decodeIndexOffMain(from: data) {
                self.summaries = parsed
            }
        }
    }

    /// Await a real CDN revalidation even when the launch-time index is already
    /// populated. Pull-to-refresh uses this path; `loadIndex()` remains the
    /// guarded offline-first initializer.
    @discardableResult
    func refreshIndex() async -> Bool {
        let updated = await AreaIndexService.shared.revalidate()
        guard updated,
              let data = AreaIndexService.shared.currentIndexData(),
              let parsed = await decodeIndexOffMain(from: data)
        else { return false }
        summaries = parsed
        clearLegacyIndexCache()
        return true
    }

    private func clearLegacyIndexCache() {
        try? FileManager.default.removeItem(at: indexDiskURL)
        try? FileManager.default.removeItem(at: summariesDiskURL)
    }

    private var indexDiskURL: URL { cacheDir.appendingPathComponent("index-v2.json") }
    private var summariesDiskURL: URL { cacheDir.appendingPathComponent("summaries-v2.json") }

    private func loadIndexFromDisk() -> [AreaSummary]? { decodeIndex(from: indexDiskURL) }

    private func loadSummariesFromDisk() -> [AreaSummary]? {
        guard let data = try? Data(contentsOf: summariesDiskURL) else { return nil }
        return try? JSONDecoder().decode([AreaSummary].self, from: data)
    }

    private func decodeIndex(from url: URL) -> [AreaSummary]? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return decodeIndex(from: data)
    }

    /// Decode the index OFF the main actor.
    ///
    /// The published index is ~29,850 rows, and this ran on the main thread
    /// during launch — a JSON decode of every row plus the visible-summary
    /// filter, blocking first paint. `AreaSummary` is Sendable, so the work can
    /// hop off and the result come back.
    private func decodeIndexOffMain(from data: Data) async -> [AreaSummary]? {
        let hidden = Set(aliasMap.keys)
        return await Task.detached(priority: .userInitiated) {
            guard let tuples = try? JSONDecoder().decode([[JSONValue]].self, from: data) else {
                return nil
            }
            return Self.visibleSummaries(from: tuples, hidden: hidden)
        }.value
    }

    private func decodeIndex(from data: Data) -> [AreaSummary]? {
        guard let tuples = try? JSONDecoder().decode([[JSONValue]].self, from: data) else {
            return nil
        }
        // Hide ONLY areas explicitly known to have zero trails (=0).
        // Areas that haven't been hydrated yet (trailCount == nil,
        // 5-tuple seed-only rows) stay visible: they render as cards
        // with no trail-count badge but are tappable, and tapping
        // loads geom via the live-Overpass fallback path. Previously
        // we filtered out nil too, which made every 5-tuple silently
        // vanish — so a half-finished Build Trail Index run (e.g.
        // seeded but not yet hydrated) made entire states disappear
        // from the app. Showing them as "loading-but-tappable" is
        // strictly better than hiding them.
        return Self.visibleSummaries(from: tuples, hidden: Set(aliasMap.keys))
    }

    /// Browse-visible summaries: real areas only. Drops zero-trail rows and
    /// areas hidden in favour of a canonical twin (docs/adr/0002) so each real
    /// place appears once in Browse and search. Lossless — an aliased area's
    /// trails all live on its canonical. Static + injectable so the hide is
    /// unit-testable without the app bundle.
    nonisolated static func visibleSummaries(from tuples: [[JSONValue]], hidden: Set<String>) -> [AreaSummary] {
        tuples
            .compactMap { AreaSummary(tuple: $0) }
            .filter { $0.trailCount != 0 && !hidden.contains($0.id) }
    }

    func search(_ query: String) -> [AreaSummary] {
        guard !query.isEmpty else { return summaries }
        let q = query.lowercased()
        return summaries.filter { $0.search.contains(q) }
    }

    // MARK: - Trail search

    /// A (trail, area) pair for Browse's trail search results.
    struct TrailSearchHit: Identifiable, Sendable {
        var id: String { "\(areaId)/\(trailId)" }
        let trailId: String
        let trailName: String
        /// Pre-lowercased name so per-keystroke filtering doesn't
        /// re-lowercase a few thousand strings.
        let searchKey: String
        let difficulty: Difficulty
        let distanceMi: Double
        let areaId: String
        let areaName: String
    }

    /// Cached trail index for Browse search — see `trailSearchHits()`.
    private var trailHitsCache: [TrailSearchHit] = []
    private var trailHitsCacheKey: Int = -1

    /// All trails from every area available locally (in-memory or on
    /// disk). Trail names only exist in full area payloads — the index
    /// carries area names alone — so trail search covers the areas the
    /// app has already fetched: favorites, prefetched nearby areas, and
    /// anything previously opened. Built lazily and cached; rebuilt
    /// only when the set of locally-available areas grows (new fetch).
    func trailSearchHits() -> [TrailSearchHit] {
        let ids = locallyAvailableAreaIds()
        let key = ids.count
        if key == trailHitsCacheKey { return trailHitsCache }

        // `uniquingKeysWith`, never `uniqueKeysWithValues`: the latter TRAPS on a
        // duplicate key, and `summaries` comes from the remotely-published
        // index — so one duplicated area id in a CDN publish would crash every
        // installed client the moment they typed in Browse search, with no app
        // update able to fix it. Same reasoning as `summariesById`.
        let names = Dictionary(summaries.map { ($0.id, $0.name) },
                               uniquingKeysWith: { first, _ in first })
        var hits: [TrailSearchHit] = []
        for areaId in ids {
            guard let areaName = names[areaId],
                  let area = cachedArea(id: areaId) else { continue }
            for trail in area.trails {
                hits.append(TrailSearchHit(
                    trailId: trail.id,
                    trailName: trail.name,
                    searchKey: trail.name.lowercased(),
                    difficulty: trail.difficulty,
                    distanceMi: trail.distanceMi,
                    areaId: areaId,
                    areaName: areaName
                ))
            }
        }
        trailHitsCache = hits
        trailHitsCacheKey = key
        return hits
    }

    /// Area ids with a full payload available locally: the in-memory
    /// cache plus per-area files in the cache directory. Filtered
    /// against the index so non-area cache files (index-v2.json,
    /// summaries-v2.json) never masquerade as areas.
    private func locallyAvailableAreaIds() -> [String] {
        var ids = Set(areaCache.keys)
        let valid = Set(summaries.map(\.id))
        for entry in cacheStore.entries() where valid.contains(entry.id) {
            ids.insert(entry.id)
        }
        return Array(ids)
    }

    /// O(1) id → summary lookup. The index is ~29,850 rows, and callers used to
    /// do `summaries.first { $0.id == ... }` per lookup — a full linear scan
    /// each time, sometimes once PER FAVORITE per view body. Rebuilt whenever
    /// `summaries` changes (see `didSet`).
    private(set) var summariesById: [String: AreaSummary] = [:]

    /// Summary for an area id, without scanning the whole index.
    func summary(id: String) -> AreaSummary? { summariesById[id] }

    /// Memoized `Area.computedSilhouette`.
    ///
    /// Building it walks every trail x every segment x every point and allocates
    /// a fresh line array. AreaCard read it TWICE per card body (once for the
    /// difficulty mix, once to draw), and every card's body depends on the
    /// user's location — which republishes on each GPS fix — so it was rebuilt
    /// for every visible card, repeatedly, while scrolling Explore. The geometry
    /// is immutable for a loaded area, so build it once per area id.
    private var silhouetteCache: [String: AreaSilhouette] = [:]

    func computedSilhouette(for area: Area) -> AreaSilhouette {
        if let hit = silhouetteCache[area.id] { return hit }
        let built = area.computedSilhouette
        silhouetteCache[area.id] = built
        return built
    }

    func nearby(lat: Double, lon: Double, limit: Int = 20) -> [AreaSummary] {
        // Decorate-sort-undecorate: haversine ONCE per area instead of twice per
        // comparison inside the comparator. On a ~29,850-row index that is
        // ~29,850 calls instead of the ~890,000 the comparator form cost.
        summaries
            .map { (area: $0, d: haversineDistanceMi(lat1: lat, lon1: lon, lat2: $0.centerLat, lon2: $0.centerLon)) }
            .sorted { $0.d < $1.d }
            .prefix(limit)
            .map(\.area)
    }

    /// Verify favorites first, then the user's ten most-recently-opened
    /// areas, preserving stable order and performing at most one fetch at a
    /// time. Progress counts verified outcomes, not attempted downloads.
    func prefetchOffline(
        progress: ((OfflineTrailPrefetchProgress) async -> Void)? = nil
    ) async -> OfflineTrailPrefetchResult {
        let favorites = FavoritesService.shared.favoriteAreas.map(\.id)
        let recents = ActivityService.shared.areaOpenedAt
            .sorted { $0.value > $1.value }
            .prefix(10)
            .map(\.key)
        return await prefetch(
            ids: favorites + recents,
            forceRefresh: false,
            progress: progress
        )
    }

    /// Refresh only the durable area files the user currently has. Existing
    /// valid bytes remain available for every failed target.
    func refreshOfflineTrails(
        progress: ((OfflineTrailPrefetchProgress) async -> Void)? = nil
    ) async -> OfflineTrailPrefetchResult {
        URLCache.shared.removeAllCachedResponses()
        return await prefetch(
            ids: cacheStore.entries().map(\.id).sorted(),
            forceRefresh: true,
            progress: progress
        )
    }

    /// Retry precisely the failed IDs from a prior operation. Refresh retries
    /// bypass the already-current shortcut so stale bytes are retained while a
    /// new replacement is verified.
    func retryOfflineTrails(
        _ result: OfflineTrailPrefetchResult,
        forceRefresh: Bool,
        progress: ((OfflineTrailPrefetchProgress) async -> Void)? = nil
    ) async -> OfflineTrailPrefetchResult {
        await prefetch(
            ids: result.retryIDs,
            forceRefresh: forceRefresh,
            progress: progress
        )
    }

    // MARK: - Nearby-Radius Prefetch

    /// UserDefaults keys for the nearby-prefetch cooldown / movement check.
    private static let lastNearbyLatKey = StorageKeys.prefetchNearbyLastLat
    private static let lastNearbyLonKey = StorageKeys.prefetchNearbyLastLon

    func prefetchNearby(
        centerLat: Double,
        centerLon: Double,
        radiusMi: Double,
        progress: ((OfflineTrailPrefetchProgress) async -> Void)? = nil
    ) async -> OfflineTrailPrefetchResult {
        let alreadyPrioritized = Set(
            FavoritesService.shared.favoriteAreas.map(\.id)
            + ActivityService.shared.areaOpenedAt
                .sorted { $0.value > $1.value }
                .prefix(10)
                .map(\.key)
        )
        let targets = summaries.compactMap { summary -> String? in
            guard !alreadyPrioritized.contains(summary.id) else { return nil }
            let distance = haversineDistanceMi(
                lat1: centerLat,
                lon1: centerLon,
                lat2: summary.centerLat,
                lon2: summary.centerLon
            )
            return distance <= radiusMi ? summary.id : nil
        }
        return await prefetch(ids: targets, forceRefresh: false, progress: progress)
    }

    @discardableResult
    func runNearbyPrefetchIfAppropriate(
        radiusMi: Double = 50,
        movementThresholdMi: Double = 25,
        force: Bool = false,
        progress: ((OfflineTrailPrefetchProgress) async -> Void)? = nil
    ) async -> NearbyOfflineTrailPrefetchResult {
        guard let location = LocationService.shared.userLocation else {
            return .skipped(.noLocation)
        }
        if let reason = NearbyOfflineTrailPrefetchResult.networkSkip(
            isOnUnmeteredNetwork: NetworkService.shared.isOnUnmeteredNetwork,
            isExpensive: NetworkService.shared.isExpensive,
            force: force
        ) {
            return .skipped(reason)
        }

        let defaults = UserDefaults.standard
        let lastLat = defaults.object(forKey: Self.lastNearbyLatKey) as? Double
        let lastLon = defaults.object(forKey: Self.lastNearbyLonKey) as? Double
        if !force, let lastLat, let lastLon {
            let moved = haversineDistanceMi(
                lat1: lastLat,
                lon1: lastLon,
                lat2: location.latitude,
                lon2: location.longitude
            )
            if moved < movementThresholdMi { return .skipped(.movementCooldown) }
        }

        let result = await prefetchNearby(
            centerLat: location.latitude,
            centerLon: location.longitude,
            radiusMi: radiusMi,
            progress: progress
        )
        let runResult = NearbyOfflineTrailPrefetchResult.completed(result)
        if runResult.shouldAdvanceCooldown {
            defaults.set(location.latitude, forKey: Self.lastNearbyLatKey)
            defaults.set(location.longitude, forKey: Self.lastNearbyLonKey)
        }
        return runResult
    }

    private func prefetch(
        ids: [String],
        forceRefresh: Bool,
        progress: ((OfflineTrailPrefetchProgress) async -> Void)?
    ) async -> OfflineTrailPrefetchResult {
        let prefetcher = OfflineTrailPrefetcher(
            isDurablyAvailable: { [cacheStore] id in
                cacheStore.validArea(id: id) != nil
            },
            fetchAndVerify: { [weak self] id in
                guard let self else { return false }
                let fetched = await self.fetchAndCacheAreaWithError(id: id)
                return fetched.durableWriteSucceeded
                    && self.cacheStore.validArea(id: id) != nil
            }
        )
        return await prefetcher.run(
            ids: ids,
            forceRefresh: forceRefresh,
            progress: progress
        )
    }

    // MARK: - Full Area Data

    /// Epsilon (meters) for the load-time polyline simplification. Trails
    /// average ~100 GPS points each from the source data, and at typical
    /// phone zoom levels a 5 m perpendicular tolerance is well below the
    /// pixel size of a polyline stroke — visually indistinguishable from
    /// the raw geometry, but cuts coord count 3-5×, shrinking both
    /// MapKit's per-frame work and SwiftUI's MapContent diff size on
    /// camera change. On-disk cache stays raw so this knob can be tuned
    /// without invalidating any persisted area.
    private static let renderDecimationEpsilonMeters = 5.0

    /// Canonicalize every Trail.id in the supplied Area through
    /// `String.canonicalTrailId`. This is the iOS-side guard against
    /// legacy CDN payloads (and on-disk caches) that still carry
    /// position-counter suffixes like `unnamed-494466239-43`. After
    /// the build-trail-counts.py change these suffixes don't appear
    /// in fresh data, but old persisted Area JSON does until the
    /// next refetch.
    nonisolated static func canonicalizeTrailIds(_ area: Area) -> Area {
        let canon = area.trails.map { t in
            // Carry EVERY field. This runs on the load path for every area and
            // BEFORE withDecimatedSegments, so a field omitted here is stripped
            // no matter what the later rebuild does. `profileFt` was missing in
            // both, which nil'd the elevation profile app-wide while the disk
            // and CDN copies looked perfectly correct. Adding a field to Trail
            // means adding it here AND in Area.withDecimatedSegments.
            Trail(
                id: t.id.canonicalTrailId,
                name: t.name,
                distanceMi: t.distanceMi,
                difficulty: t.difficulty,
                segments: t.segments,
                gainFt: t.gainFt,
                profileFt: t.profileFt,
                profileGaps: t.profileGaps
            )
        }
        return Area(
            id: area.id,
            name: area.name,
            subtitle: area.subtitle,
            centerLat: area.centerLat,
            centerLon: area.centerLon,
            zoom: area.zoom,
            bbox: area.bbox,
            trails: canon,
            trailCount: area.trailCount,
            totalMi: area.totalMi,
            cachedAt: area.cachedAt,
            parking: area.parking
        )
    }

    /// Run an Area through canonicalization + the rendering-side
    /// decimation pass and write the result to the in-memory cache.
    /// The cached Area carries TWO trail sets: `trails` (decimated,
    /// for `MapKit` rendering) and `rawTrails` (the canonicalized
    /// pre-decimation set, for the spatial grid / halo on-trail
    /// clipping / coverage measurement). Wrapped in a signpost so
    /// the existing `areaLoad` Instruments category captures the
    /// decimation cost.
    @discardableResult
    private func cacheAreaForRendering(_ area: Area) -> Area {
        let signpostID = OSSignpostID(log: areaLoadLog)
        os_signpost(.begin, log: areaLoadLog, name: "decimate", signpostID: signpostID, "%{public}s", area.id)
        let canonical = Self.canonicalizeTrailIds(area)
        let decimated = canonical.withDecimatedSegments(epsilonMeters: Self.renderDecimationEpsilonMeters)
        let attached = decimated.with(rawTrails: canonical.trails)
        os_signpost(.end, log: areaLoadLog, name: "decimate", signpostID: signpostID)
        areaCache[attached.id] = attached
        return attached
    }

    func area(id: String) async -> Area? {
        let signpostID = OSSignpostID(log: areaLoadLog)
        os_signpost(.begin, log: areaLoadLog, name: "area(id:)", signpostID: signpostID, "%{public}s", id)
        defer { os_signpost(.end, log: areaLoadLog, name: "area(id:)", signpostID: signpostID) }

        if let cached = areaCache[id], !cached.trails.isEmpty {
            os_signpost(.event, log: areaLoadLog, name: "memCache hit", signpostID: signpostID)
            return cached
        }
        if let onDisk = loadAreaFromDisk(id: id), !onDisk.trails.isEmpty {
            os_signpost(.event, log: areaLoadLog, name: "diskCache hit", signpostID: signpostID)
            let cached = cacheAreaForRendering(onDisk)
            let staleness = Date().timeIntervalSince(onDisk.cachedAt ?? .distantPast)
            // Stale-while-revalidate: return the cached copy now, but kick a
            // background re-fetch on any copy older than a few minutes so a
            // shipped correction lands by the next open — not up to 24h later.
            // Cheap now that fetchFromCdn revalidates via ETag (304 if unchanged).
            if staleness > 300 { Task { await fetchAndCacheArea(id: id) } }
            return cached
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
            let cached = cacheAreaForRendering(onDisk)
            let staleness = Date().timeIntervalSince(onDisk.cachedAt ?? .distantPast)
            // Stale-while-revalidate: return the cached copy now, but kick a
            // background re-fetch on any copy older than a few minutes so a
            // shipped correction lands by the next open — not up to 24h later.
            // Cheap now that fetchFromCdn revalidates via ETag (304 if unchanged).
            if staleness > 300 { Task { await fetchAndCacheArea(id: id) } }
            return (cached, nil)
        }
        if let existing = loadingTasks[id] {
            let result = await existing.value
            return (result, result == nil ? "Fetch already in progress but returned no data." : nil)
        }
        let fetched = await fetchAndCacheAreaWithError(id: id)
        return (fetched.area, fetched.error)
    }

    private struct AreaFetchResult {
        let area: Area?
        let error: String?
        let durableWriteSucceeded: Bool
    }

    @discardableResult
    private func fetchAndCacheArea(id: String) async -> Area? {
        await fetchAndCacheAreaWithError(id: id).area
    }

    private func fetchAndCacheAreaWithError(id: String) async -> AreaFetchResult {
        guard let summary = summariesById[id] else {
            return AreaFetchResult(
                area: nil,
                error: "Area not found in index.",
                durableWriteSucceeded: false
            )
        }

        // CDN-first: precomputed per-area JSON gives us deterministic trail
        // identities. A replacement becomes the in-memory current copy only
        // after its durable file has passed staged and final verification.
        if let cdnArea = await fetchFromCdn(id: id),
           cdnArea.id == id,
           !cdnArea.trails.isEmpty {
            return persistFetchedArea(cdnArea, expectedID: id)
        }

        let stub = AreaRow(
            id: summary.id, name: summary.name, state: summary.subtitle,
            centerLat: summary.centerLat, centerLon: summary.centerLon,
            zoom: 13, bbox: nil, trails: nil, trailCount: nil, totalMi: nil, cachedAt: nil,
            osmRelationId: summary.osmRelationId
        )
        let maxAttempts = 3
        var attempt = 0
        var lastError: Error?
        while attempt < maxAttempts {
            attempt += 1
            do {
                let area = try await fetchFromOverpass(row: stub).toArea()
                guard area.id == id, !area.trails.isEmpty else {
                    if attempt < maxAttempts {
                        try? await Task.sleep(for: .milliseconds(600 * attempt))
                        continue
                    }
                    return AreaFetchResult(
                        area: existingValidArea(id: id),
                        error: "Downloaded trail data was empty or did not match this area.",
                        durableWriteSucceeded: false
                    )
                }
                return persistFetchedArea(area, expectedID: id)
            } catch {
                lastError = error
                if attempt < maxAttempts {
                    try? await Task.sleep(for: .milliseconds(600 * attempt))
                }
            }
        }
        return AreaFetchResult(
            area: existingValidArea(id: id),
            error: lastError?.localizedDescription ?? "Could not load trail data.",
            durableWriteSucceeded: false
        )
    }

    private func persistFetchedArea(_ area: Area, expectedID: String) -> AreaFetchResult {
        let receipt = cacheStore.store(area, expectedID: expectedID)
        guard receipt.succeeded,
              let durableArea = cacheStore.validArea(id: expectedID)
        else {
            return AreaFetchResult(
                area: existingValidArea(id: expectedID) ?? area,
                error: "Trail data loaded, but couldn't be verified for offline use.",
                durableWriteSucceeded: false
            )
        }
        return AreaFetchResult(
            area: cacheAreaForRendering(durableArea),
            error: nil,
            durableWriteSucceeded: true
        )
    }

    private func existingValidArea(id: String) -> Area? {
        if let area = areaCache[id], !area.trails.isEmpty { return area }
        guard let area = cacheStore.validArea(id: id) else { return nil }
        return cacheAreaForRendering(area)
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
        let signpostID = OSSignpostID(log: areaLoadLog)
        os_signpost(.begin, log: areaLoadLog, name: "fetchFromCdn", signpostID: signpostID, "%{public}s", id)
        defer { os_signpost(.end, log: areaLoadLog, name: "fetchFromCdn", signpostID: signpostID) }

        guard let url = URL(string: "\(cdnBaseURL)/\(id).json") else { return nil }
        var req = URLRequest(url: url, timeoutInterval: 20)
        // The CDN serves geom with `max-age=86400`, so the default
        // `.useProtocolCachePolicy` makes URLSession return a day-old cached
        // response without hitting the network — a corrected pin (e.g. a bad
        // parking lot we removed) would keep drawing for up to 24h even after
        // the user clears the app cache. Revalidate against the origin ETag
        // instead: a changed area re-downloads, an unchanged one is a cheap 304.
        req.cachePolicy = .reloadRevalidatingCacheData
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            os_signpost(.event, log: areaLoadLog, name: "cdn bytes", signpostID: signpostID, "%d", data.count)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                return nil
            }
            os_signpost(.begin, log: areaLoadLog, name: "cdn decode", signpostID: signpostID)
            let row = try JSONDecoder().decode(AreaRow.self, from: data)
            os_signpost(.end, log: areaLoadLog, name: "cdn decode", signpostID: signpostID)
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
                segments: info.segments,
                gainFt: nil   // live-Overpass fallback has no DEM gain
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
        cacheStore.validArea(id: id)
    }

    func cachedArea(id: String) -> Area? {
        areaCache[id] ?? loadAreaFromDisk(id: id)
    }

    func clearAreaCache() {
        areaCache.removeAll()
        // The HTTP-response cache is separate from durable Offline Trails.
        // Explicit Manage Offline Trails deletion clears both so a later open
        // cannot immediately repopulate from an old URLCache response.
        URLCache.shared.removeAllCachedResponses()
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

    /// Enumerate only decoded, non-empty, identity-matching durable area files.
    /// File sizes are the verified JSON byte counts, not directory estimates.
    func downloadedAreas() -> [DownloadedArea] {
        let names = summariesById.mapValues(\.name)
        return cacheStore.entries()
            .map { entry in
                DownloadedArea(
                    id: entry.id,
                    name: names[entry.id] ?? entry.area.name,
                    sizeBytes: entry.sizeBytes
                )
            }
            .sorted {
                $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
            }
    }

    /// Remove a single area from the on-disk + in-memory cache. The
    /// next `area(id:)` call will re-fetch from the CDN. Used by the
    /// Manage Downloads list's swipe-to-delete.
    func removeDownloadedArea(id: String) {
        areaCache.removeValue(forKey: id)
        try? FileManager.default.removeItem(at: areaDiskURL(id: id))
    }
}
