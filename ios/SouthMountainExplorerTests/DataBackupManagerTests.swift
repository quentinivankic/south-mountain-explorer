import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for the export-time data-safety guard. The danger being
/// guarded against: an export that silently omits the user's
/// irreplaceable `hike-history.json`, letting them "back up", reset,
/// and only then discover the recordings were never in the file.
struct DataBackupManagerTests {

    private var documentsDir: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    /// Export must FAIL LOUDLY when a backup file exists but can't be
    /// read. Simulated by planting a DIRECTORY at hike-history.json's
    /// path: `FileManager.fileExists` returns true for it, but
    /// `Data(contentsOf:)` throws — exactly the "exists but unreadable"
    /// shape the guard exists to catch.
    @Test func exportThrowsWhenHistoryFileExistsButUnreadable() throws {
        let url = documentsDir.appendingPathComponent("hike-history.json")
        // Preserve anything the test host already has at that path.
        let saved = try? Data(contentsOf: url)
        try? FileManager.default.removeItem(at: url)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        defer {
            try? FileManager.default.removeItem(at: url)
            if let saved { try? saved.write(to: url) }
        }

        #expect(throws: DataBackupManager.ExportError.self) {
            _ = try DataBackupManager.collectExport()
        }
    }

    /// A MISSING backup file is a legitimate empty state (a user who
    /// has never recorded a hike has no hike-history.json) and must
    /// export cleanly rather than throwing.
    @Test func exportSucceedsWhenHistoryFileMissing() throws {
        let url = documentsDir.appendingPathComponent("hike-history.json")
        let saved = try? Data(contentsOf: url)
        // Remove a real file OR a leftover directory from the test above.
        try? FileManager.default.removeItem(at: url)
        defer { if let saved { try? saved.write(to: url) } }

        // Should not throw with the file absent — a thrown error fails
        // this (throwing) test.
        _ = try DataBackupManager.collectExport()
    }
}
