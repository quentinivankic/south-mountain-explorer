import Foundation
import OSLog

private let log = Logger(subsystem: "com.trekdex.app", category: "trailShapes")

/// Compact per-trail thumbnail shapes for Browse search results, so a trail in
/// an UN-visited area still shows its linework instead of a generic icon.
///
/// Loaded from R2 as a SEPARATE file from the trail-search index (built by
/// scripts/build-trail-shapes.py, ~2.5 MB gzipped). Kept deliberately OFF the
/// search critical path: the index stays tiny and search is instant, while
/// this bigger file loads in the BACKGROUND (kicked at launch, warmed during
/// onboarding) and thumbnails fill in as it arrives. ETag-cached like the rest,
/// so it's one download per data version and updates propagate automatically.
///
/// Format: `{ "areaId/trailId": [x0, y0, x1, y1, …] }` — a Douglas-Peucker-
/// simplified polyline pre-normalized to a 0–255 box, so a thumbnail draws it
/// directly with no lat/lon projection.
@MainActor
@Observable
final class TrailShapeService {
    static let shared = TrailShapeService()

    private(set) var shapes: [String: [UInt8]] = [:]

    private let cdnURL = URL(string: "https://cdn.trekdex.app/trail-shapes.json")!
    private static let etagKey = "trailShapesETag"
    private let cachedURL: URL = {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("trail-shapes.json")
    }()

    private var loading = false

    private init() {}

    /// The simplified polyline for a trail, or nil if not present yet. Flat
    /// `[x0, y0, x1, y1, …]` in a 0–255 box.
    func shape(areaId: String, trailId: String) -> [UInt8]? {
        shapes["\(areaId)/\(trailId)"]
    }

    /// Load once per launch: seed from the on-disk cache, then revalidate
    /// against R2 in the background. Safe to call repeatedly (e.g. from both
    /// launch and onboarding).
    func loadIfNeeded() async {
        guard shapes.isEmpty, !loading else { return }
        loading = true
        defer { loading = false }
        let url = cachedURL
        let cached = await Task.detached(priority: .utility) { () -> [String: [UInt8]] in
            guard let data = try? Data(contentsOf: url) else { return [:] }
            return Self.parse(data)
        }.value
        if shapes.isEmpty, !cached.isEmpty { shapes = cached }
        await revalidate()
    }

    func revalidate() async {
        var request = URLRequest(url: cdnURL)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let etag = UserDefaults.standard.string(forKey: Self.etagKey) {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return }
            if http.statusCode == 304 { return }
            guard (200..<300).contains(http.statusCode) else {
                log.notice("trail-shapes revalidate: HTTP \(http.statusCode)")
                return
            }
            let parsed = await Task.detached(priority: .utility) { Self.parse(data) }.value
            guard !parsed.isEmpty else { return }
            shapes = parsed
            try? data.write(to: cachedURL, options: .atomic)
            if let newETag = http.value(forHTTPHeaderField: "ETag") {
                UserDefaults.standard.set(newETag, forKey: Self.etagKey)
            }
            log.notice("trail-shapes: \(parsed.count) shapes")
        } catch {
            log.notice("trail-shapes revalidate: \(error.localizedDescription, privacy: .public)")
        }
    }

    /// Off-main parse of `{ key: [0..255 ints] }`. Clamps to UInt8; a malformed
    /// entry is skipped.
    nonisolated private static func parse(_ data: Data) -> [String: [UInt8]] {
        guard let raw = try? JSONDecoder().decode([String: [Int]].self, from: data) else {
            return [:]
        }
        var out: [String: [UInt8]] = [:]
        out.reserveCapacity(raw.count)
        for (k, v) in raw where v.count >= 4 && v.count % 2 == 0 {
            out[k] = v.map { UInt8(clamping: $0) }
        }
        return out
    }
}
