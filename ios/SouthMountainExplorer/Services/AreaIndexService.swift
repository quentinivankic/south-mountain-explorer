import Foundation
import OSLog

private let indexLoadLog = OSLog(subsystem: "com.trekdex.app", category: "indexLoad")
private let log = Logger(subsystem: "com.trekdex.app", category: "indexLoad")

/// Fetches the master `areas-index.json` from R2 with ETag-cached
/// revalidation. The bundled copy at `Resources/areas-index.json`
/// stays as the offline-first fallback — first launch on a new
/// install reads it instantly, and the R2 fetch happens in the
/// background. When R2 returns a newer copy (200), we swap it in
/// and persist to the Caches directory + record the new ETag so
/// the next launch can revalidate with `If-None-Match` for a cheap
/// 304.
///
/// Mirrors `AreaSilhouetteService` / area-geom fetch shapes. Lives
/// in `Services/` next to those so the network-layer pattern is
/// consistent.
@MainActor
final class AreaIndexService {
    static let shared = AreaIndexService()

    private let cdnURL = URL(string: "https://cdn.trekdex.app/index.json")!

    private let cachedIndexURL: URL = {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("areas-index-remote.json")
    }()

    private static let etagKey = "areaIndexETag"

    private init() {}

    /// The freshest index bytes we have right now — R2 cache > bundle.
    /// Returns nil only when neither the cache file nor the bundle
    /// resource is available, which shouldn't happen in normal app
    /// flow (bundle is always shipped).
    func currentIndexData() -> Data? {
        if let cached = try? Data(contentsOf: cachedIndexURL) {
            return cached
        }
        if let bundled = bundleURL().flatMap({ try? Data(contentsOf: $0) }) {
            return bundled
        }
        return nil
    }

    /// Source provenance for the data returned by `currentIndexData()`.
    /// Mostly useful for diagnostics and the "is this build's data
    /// fresh?" question the Settings screen could surface later.
    enum Source { case bundle, cache }
    var currentSource: Source {
        FileManager.default.fileExists(atPath: cachedIndexURL.path) ? .cache : .bundle
    }

    /// Bundle URL for the shipped fallback. Same key
    /// `AreaDataService` reads via `Bundle.main.url(forResource:)`.
    private func bundleURL() -> URL? {
        Bundle.main.url(forResource: "areas-index", withExtension: "json")
    }

    /// Kick the R2 revalidation. Sends `If-None-Match` if we have a
    /// stored ETag; on 304 the call is a no-op. On 200, writes the
    /// new bytes to the Caches directory atomically, stores the new
    /// ETag, and returns `true` so callers (AreaDataService) can
    /// reload their summary list.
    ///
    /// Network errors swallowed silently — the bundle/cache combo
    /// keeps the app fully functional offline. Logged for the
    /// diagnostics bundle.
    @discardableResult
    func revalidate() async -> Bool {
        let signpostID = OSSignpostID(log: indexLoadLog)
        os_signpost(.begin, log: indexLoadLog, name: "revalidate", signpostID: signpostID)
        defer { os_signpost(.end, log: indexLoadLog, name: "revalidate", signpostID: signpostID) }

        var request = URLRequest(url: cdnURL)
        if let etag = UserDefaults.standard.string(forKey: Self.etagKey) {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return false }
            if http.statusCode == 304 {
                log.notice("index revalidate: 304 not modified")
                return false
            }
            guard (200..<300).contains(http.statusCode) else {
                log.notice("index revalidate: HTTP \(http.statusCode)")
                return false
            }
            // Validate the body decodes as the expected tuple shape
            // before persisting — corrupted CDN data must never
            // replace a valid bundled / cached copy.
            guard (try? JSONDecoder().decode([[JSONValue]].self, from: data)) != nil else {
                log.error("index revalidate: decode failed, keeping prior copy")
                return false
            }
            try data.write(to: cachedIndexURL, options: .atomic)
            if let newETag = http.value(forHTTPHeaderField: "ETag") {
                UserDefaults.standard.set(newETag, forKey: Self.etagKey)
            }
            log.notice("index revalidate: updated (\(data.count) bytes)")
            return true
        } catch {
            log.notice("index revalidate: \(error.localizedDescription, privacy: .public)")
            return false
        }
    }
}
