import Foundation

/// CDN-only fetch for state jobs (no Overpass fallback). Same verified write
/// as the per-area pipeline: decode -> `AreaCacheStore.store` (staged, verified,
/// promoted) -> `validArea` read-back.
struct AreaCDNDownloader: Sendable {
    let session: URLSession
    let cacheDirectory: URL
    let baseURL: String

    static let maxETagLength = 256

    init(
        session: URLSession,
        cacheDirectory: URL,
        baseURL: String = "https://cdn.trekdex.app"
    ) {
        self.session = session
        self.cacheDirectory = cacheDirectory
        self.baseURL = baseURL
    }

    /// A dedicated session whose OS-level network access follows the user's
    /// cellular choice, so a mid-run switch to cellular fails instead of
    /// spending data.
    static func makeSession(allowCellular: Bool) -> URLSession {
        let config = URLSessionConfiguration.default
        config.allowsCellularAccess = allowCellular
        config.allowsExpensiveNetworkAccess = allowCellular
        config.waitsForConnectivity = false
        config.httpMaximumConnectionsPerHost = 6
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        return URLSession(configuration: config)
    }

    private func store() -> AreaCacheStore {
        AreaCacheStore(cacheDirectory: cacheDirectory)
    }

    func isAvailable(id: String) -> Bool {
        store().validArea(id: id) != nil
    }

    func download(id: String, etag: String?) async -> AreaOutcome {
        guard StateCatalog.isSafeAreaID(id),
              let url = URL(string: "\(baseURL)/\(id).json")
        else { return .failed(.invalidPayload) }

        var request = URLRequest(url: url, timeoutInterval: 30)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let etag, !etag.isEmpty, etag.count <= Self.maxETagLength {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            return Self.classify(error)
        }
        if Task.isCancelled { return .cancelled }
        guard let http = response as? HTTPURLResponse else { return .failed(.network) }
        if http.statusCode == 304 { return .unchanged }
        guard (200..<300).contains(http.statusCode) else { return .failed(.http(http.statusCode)) }

        guard let row = try? JSONDecoder().decode(AreaRow.self, from: data) else {
            return .failed(.invalidPayload)
        }
        let area = row.toArea()
        guard area.id == id, !area.trails.isEmpty else { return .failed(.invalidPayload) }

        let store = store()
        guard store.store(area, expectedID: id).succeeded,
              store.validArea(id: id) != nil
        else { return .failed(.storage) }

        let bytes = (try? FileManager.default.attributesOfItem(
            atPath: store.fileURL(for: id).path
        )[.size] as? Int) ?? data.count
        let tag = http.value(forHTTPHeaderField: "ETag")
            .flatMap { $0.count <= Self.maxETagLength ? $0 : nil }
        return .stored(bytes: bytes, etag: tag)
    }

    static func classify(_ error: Error) -> AreaOutcome {
        guard let urlError = error as? URLError else {
            return Task.isCancelled ? .cancelled : .failed(.network)
        }
        switch urlError.code {
        case .cancelled:
            return .cancelled
        case .timedOut:
            return .failed(.timeout)
        case .dataNotAllowed, .internationalRoamingOff:
            return .policyBlocked
        default:
            return .failed(.network)
        }
    }
}
