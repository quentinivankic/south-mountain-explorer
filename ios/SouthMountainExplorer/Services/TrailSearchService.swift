import Foundation
import OSLog

private let log = Logger(subsystem: "com.trekdex.app", category: "trailSearch")

/// Global trail-name search index. The lightweight areas-index carries AREA
/// names only, so Browse trail search would otherwise match only trails whose
/// area geom is already cached (see AreaDataService.trailSearchHits). This
/// loads a compact `[name, areaId, trailId, distanceMi, difficulty]` index
/// from R2 (built by scripts/build-trail-search-index.py, ~1.3 MB gzipped) so
/// EVERY trail name is searchable; tapping a hit fetches that area's geom as
/// before.
///
/// Best-effort and additive: if the index hasn't loaded (offline, first
/// launch, fetch error), Browse falls back to the local loaded-areas search,
/// so behaviour never regresses below today's.
@MainActor
@Observable
final class TrailSearchService {
    static let shared = TrailSearchService()

    struct Entry: Sendable {
        let trailName: String
        let areaId: String
        let trailId: String
        let distanceMi: Double
        let difficulty: Difficulty
        /// Pre-lowercased name so per-keystroke filtering doesn't re-lowercase
        /// 80k strings.
        let searchKey: String
    }

    private(set) var entries: [Entry] = []

    private let cdnURL = URL(string: "https://cdn.trekdex.app/trail-search.json")!
    private static let etagKey = "trailSearchETag"
    private let cachedURL: URL = {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("trail-search.json")
    }()

    private var loading = false

    private init() {}

    /// Load once per launch: seed instantly from the on-disk cache if present,
    /// then revalidate against R2 in the background. Cheap to call repeatedly.
    func loadIfNeeded() async {
        guard entries.isEmpty, !loading else { return }
        loading = true
        defer { loading = false }
        let url = cachedURL
        let cached = await Task.detached(priority: .utility) { () -> [Entry] in
            guard let data = try? Data(contentsOf: url) else { return [] }
            return Self.parse(data)
        }.value
        if entries.isEmpty, !cached.isEmpty { entries = cached }
        await revalidate()
    }

    /// Trail-name substring matches, capped. Empty until the index loads —
    /// callers should fall back to the local search when this returns [].
    func search(_ query: String, limit: Int = 25) -> [Entry] {
        let k = query.lowercased()
        guard !k.isEmpty, !entries.isEmpty else { return [] }
        var out: [Entry] = []
        out.reserveCapacity(limit)
        for e in entries where e.searchKey.contains(k) {
            out.append(e)
            if out.count >= limit { break }
        }
        return out
    }

    /// Fetch with `If-None-Match`; on 200 parse (off-main) + persist + record
    /// the ETag, on 304 keep what we have. Network/parse errors are swallowed —
    /// the local fallback keeps search working.
    func revalidate() async {
        var request = URLRequest(url: cdnURL)
        // Bypass the local HTTP cache so If-None-Match actually reaches the
        // origin (same fix as AreaIndexService / #356).
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let etag = UserDefaults.standard.string(forKey: Self.etagKey) {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return }
            if http.statusCode == 304 { return }
            guard (200..<300).contains(http.statusCode) else {
                log.notice("trail-search revalidate: HTTP \(http.statusCode)")
                return
            }
            let parsed = await Task.detached(priority: .utility) { Self.parse(data) }.value
            guard !parsed.isEmpty else {
                log.error("trail-search revalidate: parse empty, keeping prior")
                return
            }
            entries = parsed
            try? data.write(to: cachedURL, options: .atomic)
            if let newETag = http.value(forHTTPHeaderField: "ETag") {
                UserDefaults.standard.set(newETag, forKey: Self.etagKey)
            }
            log.notice("trail-search: \(parsed.count) trails")
        } catch {
            log.notice("trail-search revalidate: \(error.localizedDescription, privacy: .public)")
        }
    }

    /// Synchronous, isolation-free parse of the
    /// `[[name, areaId, trailId, mi, difficulty]]` array — run off-main via
    /// Task.detached. A malformed row is skipped, not fatal.
    nonisolated private static func parse(_ data: Data) -> [Entry] {
        guard let rows = try? JSONDecoder().decode([[JSONValue]].self, from: data) else {
            return []
        }
        var out: [Entry] = []
        out.reserveCapacity(rows.count)
        for r in rows where r.count >= 5 {
            guard let name = r[0].stringValue,
                  let areaId = r[1].stringValue,
                  let trailId = r[2].stringValue,
                  let mi = r[3].doubleValue,
                  let diffStr = r[4].stringValue,
                  let diff = Difficulty(rawValue: diffStr)
            else { continue }
            out.append(Entry(trailName: name, areaId: areaId, trailId: trailId,
                             distanceMi: mi, difficulty: diff,
                             searchKey: name.lowercased()))
        }
        return out
    }
}
