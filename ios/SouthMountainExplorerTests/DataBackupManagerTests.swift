import Foundation
import Testing
@testable import SouthMountainExplorer

/// Fail-closed import coverage plus export checks isolated from host Documents.
@Suite
@MainActor
struct DataBackupManagerTests {

    private enum FixtureError: Error {
        case userDefaultsSuiteUnavailable
    }

    private final class Fixture {
        let root: URL
        let documents: URL
        let defaults: UserDefaults
        private let suiteName: String

        init() throws {
            suiteName = "DataBackupManagerTests.\(UUID().uuidString)"
            guard let suiteDefaults = UserDefaults(suiteName: suiteName) else {
                throw FixtureError.userDefaultsSuiteUnavailable
            }
            defaults = suiteDefaults
            defaults.removePersistentDomain(forName: suiteName)

            root = FileManager.default.temporaryDirectory
                .appendingPathComponent("DataBackupManagerTests-\(UUID().uuidString)", isDirectory: true)
            documents = root.appendingPathComponent("Documents", isDirectory: true)
            try FileManager.default.createDirectory(
                at: documents,
                withIntermediateDirectories: true
            )
        }

        func cleanup() {
            defaults.removePersistentDomain(forName: suiteName)
            try? FileManager.default.removeItem(at: root)
        }
    }

    private var literalSchemaV1Backup: Data {
        Data("""
        {"version":1,"exportedAt":"2025-06-01T12:00:00Z","appBuild":"legacy","userDefaults":{"summit:onboarded":{"bool":{"_0":true}}},"files":{}}
        """.utf8)
    }

    @Test func importAlwaysFailsClosedBeforeCurrentStateChanges() throws {
        let fixture = try Fixture()
        defer { fixture.cleanup() }

        fixture.defaults.set("metric", forKey: StorageKeys.units)
        let originalHistory = Data("existing-history".utf8)
        let historyURL = fixture.documents.appendingPathComponent("hike-history.json")
        try originalHistory.write(to: historyURL)

        for payload in [Data("not-json".utf8), literalSchemaV1Backup] {
            #expect(
                DataBackupManager.importRejection(for: payload)
                    == .importTemporarilyUnavailable
            )

            var capturedError: DataBackupManager.ImportError?
            do {
                try DataBackupManager.performImport(from: payload)
            } catch let error as DataBackupManager.ImportError {
                capturedError = error
            }

            #expect(capturedError == .importTemporarilyUnavailable)
            #expect(
                capturedError?.localizedDescription
                    == "Import is temporarily unavailable while restore safety is being improved. Your existing data was not changed. Export All Data remains available."
            )
            #expect(fixture.defaults.string(forKey: StorageKeys.units) == "metric")
            #expect(try Data(contentsOf: historyURL) == originalHistory)
        }
    }

    @Test func schemaV1BackupRemainsReadable() throws {
        let export = try JSONDecoder().decode(
            DataBackupManager.Export.self,
            from: literalSchemaV1Backup
        )

        #expect(export.version == 1)
        let onboarded = try #require(export.userDefaults[StorageKeys.onboarded])
        switch onboarded {
        case .bool(let value):
            #expect(value)
        default:
            #expect(Bool(false), "schema-v1 bool value changed representation")
        }
    }

    @Test func exportRemainsAvailableUsingIsolatedState() throws {
        let fixture = try Fixture()
        defer { fixture.cleanup() }

        fixture.defaults.set("metric", forKey: StorageKeys.units)
        let history = Data("isolated-history".utf8)
        try history.write(
            to: fixture.documents.appendingPathComponent("hike-history.json")
        )

        let data = try DataBackupManager.collectExport(
            userDefaults: fixture.defaults,
            documentsDirectory: fixture.documents
        )
        let export = try JSONDecoder().decode(DataBackupManager.Export.self, from: data)

        #expect(export.version == DataBackupManager.schemaVersion)
        let units = try #require(export.userDefaults[StorageKeys.units])
        switch units {
        case .string(let value):
            #expect(value == "metric")
        default:
            #expect(Bool(false), "units should retain its schema-v1 string representation")
        }
        let encodedHistory = try #require(export.files["hike-history.json"])
        #expect(Data(base64Encoded: encodedHistory) == history)
    }

    @Test func exportStillFailsLoudlyForUnreadableHistory() throws {
        let fixture = try Fixture()
        defer { fixture.cleanup() }

        try FileManager.default.createDirectory(
            at: fixture.documents.appendingPathComponent("hike-history.json"),
            withIntermediateDirectories: true
        )

        #expect(throws: DataBackupManager.ExportError.self) {
            _ = try DataBackupManager.collectExport(
                userDefaults: fixture.defaults,
                documentsDirectory: fixture.documents
            )
        }
    }
}
