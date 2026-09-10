import Foundation
import Testing
@testable import SouthMountainExplorer

/// Source-architecture guards for retirement of automatic diagnostics upload.
/// These checks do not classify manual diagnostic payloads as nonsensitive;
/// they only pin the compiled-source and network-transport boundaries.
struct DiagnosticsTransportArchitectureTests {
    private var appSourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // test file
            .deletingLastPathComponent() // SouthMountainExplorerTests
            .appendingPathComponent("SouthMountainExplorer", isDirectory: true)
    }

    private func swiftSources() throws -> [(url: URL, text: String)] {
        let keys: [URLResourceKey] = [.isRegularFileKey]
        let enumerator = try #require(FileManager.default.enumerator(
            at: appSourceRoot,
            includingPropertiesForKeys: keys
        ))
        var sources: [(URL, String)] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let values = try url.resourceValues(forKeys: Set(keys))
            guard values.isRegularFile == true else { continue }
            sources.append((url, try String(contentsOf: url, encoding: .utf8)))
        }
        return sources
    }

    private func relativePath(for url: URL) -> String {
        String(url.path.dropFirst(appSourceRoot.path.count + 1))
    }

    @Test func compiledSourcesDoNotReferenceRetiredUploader() throws {
        let sources = try swiftSources()
        let retiredTokens = ["DebugDiagSync", "uploadIfEnabled", "Auto-sync Diagnostics"]
        let hits = sources.flatMap { source in
            retiredTokens.compactMap { token in
                source.text.contains(token) ? "\(relativePath(for: source.url)): \(token)" : nil
            }
        }.sorted()

        #expect(hits.isEmpty, "Compiled source still references the retired uploader: \(hits)")
    }

    @Test func fullBackupCollectionIsOnlyCalledFromSettingsAndNotCoupledToTransport() throws {
        let sources = try swiftSources()
        let collectionCall = "DataBackupManager.collectExport()"
        let transportTokens = ["URLSession", "URLRequest", "httpMethod", "httpBody"]

        let callers = sources
            .filter { $0.text.contains(collectionCall) }
            .map { relativePath(for: $0.url) }
            .sorted()
        #expect(callers == ["Views/Settings/SettingsView.swift"])

        let coupled = sources
            .filter { source in
                source.text.contains(collectionCall)
                    && transportTokens.contains(where: source.text.contains)
            }
            .map { relativePath(for: $0.url) }
            .sorted()
        #expect(coupled.isEmpty, "Full-backup collection gained network transport in: \(coupled)")
    }

    @Test func manualDiagnosticsExporterDoesNotCollectFullBackupOrOwnTransport() throws {
        let url = appSourceRoot
            .appendingPathComponent("Services", isDirectory: true)
            .appendingPathComponent("DiagnosticsService.swift")
        let source = try String(contentsOf: url, encoding: .utf8)
        let forbiddenTokens = [
            "DataBackupManager.collectExport()",
            "URLSession",
            "URLRequest",
            "httpMethod",
            "httpBody",
        ]
        let hits = forbiddenTokens.filter(source.contains)

        #expect(hits.isEmpty, "Manual diagnostics exporter owns a forbidden boundary: \(hits)")
    }
}
