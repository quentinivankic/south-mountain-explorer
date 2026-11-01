import Foundation
import Testing
@testable import SouthMountainExplorer

/// Serves canned responses; records requests. Suite is serialized because the
/// handler table is process-wide.
final class StubCDNProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) -> (Int, [String: String], Data))?
    nonisolated(unsafe) static var requests: [URLRequest] = []
    private static let lock = NSLock()

    static func reset(_ h: (@Sendable (URLRequest) -> (Int, [String: String], Data))?) {
        lock.withLock { handler = h; requests = [] }
    }

    static var requestCount: Int { lock.withLock { requests.count } }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let h = Self.lock.withLock { () -> (@Sendable (URLRequest) -> (Int, [String: String], Data))? in
            Self.requests.append(request)
            return Self.handler
        }
        guard let h else {
            client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
            return
        }
        let (status, headers, body) = h(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@Suite(.serialized)
struct AreaCDNDownloaderTests {
    private func makeDownloader() -> (AreaCDNDownloader, URL) {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("AreaCDNDownloaderTests-\(UUID().uuidString)", isDirectory: true)
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubCDNProtocol.self]
        return (AreaCDNDownloader(session: URLSession(configuration: config), cacheDirectory: dir, baseURL: "https://cdn.test"), dir)
    }

    private func body(id: String, trails: Bool = true) throws -> Data {
        let trail = Trail(
            id: "t1", name: "Trail", distanceMi: 1, difficulty: .easy,
            segments: [[[33.3, -112], [33.31, -112.01]]]
        )
        let trailJSON = trails ? String(decoding: try JSONEncoder().encode([trail]), as: UTF8.self) : "[]"
        return Data("""
        {"id":"\(id)","name":"Area","state":"Utah","center_lat":33.3,"center_lon":-112,"zoom":13,"trails":\(trailJSON)}
        """.utf8)
    }

    @Test func okResponseStoresVerifiesAndReturnsETag() async throws {
        let (downloader, dir) = makeDownloader()
        defer { try? FileManager.default.removeItem(at: dir) }
        let data = try body(id: "a")
        StubCDNProtocol.reset { _ in (200, ["ETag": "\"v1\""], data) }
        let outcome = await downloader.download(id: "a", etag: nil)
        guard case .stored(let bytes, let etag) = outcome else {
            Issue.record("expected stored, got \(outcome)")
            return
        }
        #expect(bytes > 0)
        #expect(etag == "\"v1\"")
        #expect(downloader.isAvailable(id: "a"))
        // No staged or backup leftovers.
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: dir.path)
            .filter { $0.hasSuffix(".stage") || $0.hasSuffix(".backup") }
        #expect(leftovers.isEmpty)
    }

    @Test func notModifiedIsUnchangedAndSendsIfNoneMatchWithoutWriting() async throws {
        let (downloader, dir) = makeDownloader()
        defer { try? FileManager.default.removeItem(at: dir) }
        StubCDNProtocol.reset { _ in (304, [:], Data()) }
        let outcome = await downloader.download(id: "a", etag: "\"v1\"")
        #expect(outcome == .unchanged)
        #expect(StubCDNProtocol.requests.last?.value(forHTTPHeaderField: "If-None-Match") == "\"v1\"")
        #expect(!downloader.isAvailable(id: "a"))
    }

    @Test func serverErrorsAndMissingObjectsAreFailures() async {
        let (downloader, dir) = makeDownloader()
        defer { try? FileManager.default.removeItem(at: dir) }
        StubCDNProtocol.reset { _ in (500, [:], Data()) }
        #expect(await downloader.download(id: "a", etag: nil) == .failed(.http(500)))
        StubCDNProtocol.reset { _ in (404, [:], Data()) }
        #expect(await downloader.download(id: "a", etag: nil) == .failed(.http(404)))
        #expect(!downloader.isAvailable(id: "a"))
    }

    @Test func emptyTrailsWrongIdAndGarbageAreInvalidPayloadAndNothingIsStored() async throws {
        let (downloader, dir) = makeDownloader()
        defer { try? FileManager.default.removeItem(at: dir) }
        let empty = try body(id: "a", trails: false)
        StubCDNProtocol.reset { _ in (200, [:], empty) }
        #expect(await downloader.download(id: "a", etag: nil) == .failed(.invalidPayload))
        let wrong = try body(id: "other")
        StubCDNProtocol.reset { _ in (200, [:], wrong) }
        #expect(await downloader.download(id: "a", etag: nil) == .failed(.invalidPayload))
        StubCDNProtocol.reset { _ in (200, [:], Data("nope".utf8)) }
        #expect(await downloader.download(id: "a", etag: nil) == .failed(.invalidPayload))
        #expect(!downloader.isAvailable(id: "a"))
    }

    @Test func unsafeIDsAreRejectedBeforeAnyRequest() async {
        let (downloader, dir) = makeDownloader()
        defer { try? FileManager.default.removeItem(at: dir) }
        StubCDNProtocol.reset { _ in (200, [:], Data()) }
        for bad in ["", "../x", "a/b"] {
            #expect(await downloader.download(id: bad, etag: nil) == .failed(.invalidPayload))
        }
        #expect(StubCDNProtocol.requestCount == 0)
    }

    @Test func networkErrorsAreClassified() {
        #expect(AreaCDNDownloader.classify(URLError(.cancelled)) == .cancelled)
        #expect(AreaCDNDownloader.classify(URLError(.timedOut)) == .failed(.timeout))
        #expect(AreaCDNDownloader.classify(URLError(.dataNotAllowed)) == .policyBlocked)
        #expect(AreaCDNDownloader.classify(URLError(.notConnectedToInternet)) == .failed(.network))
    }

    @Test func oversizedETagIsNotSent() async {
        let (downloader, dir) = makeDownloader()
        defer { try? FileManager.default.removeItem(at: dir) }
        StubCDNProtocol.reset { _ in (304, [:], Data()) }
        _ = await downloader.download(id: "a", etag: String(repeating: "x", count: 300))
        #expect(StubCDNProtocol.requests.last?.value(forHTTPHeaderField: "If-None-Match") == nil)
    }
}
