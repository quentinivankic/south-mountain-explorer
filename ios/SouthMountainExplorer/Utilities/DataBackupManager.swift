import Foundation

/// Bundles every supported piece of user-owned app data into a schema-v1 JSON
/// file that can be saved through the share sheet.
///
/// Export remains available. Restore is deliberately disabled until import can
/// provide durable, restart-safe recovery without risking current app data.
enum DataBackupManager {

    /// Kept stable so existing schema-v1 exports remain readable by future
    /// restore work.
    static let schemaVersion = 1

    private static let documentsDir: URL = {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }()

    /// User-owned Documents files included in each export.
    private static let documentFilenames: [String] = [
        "hike-history.json",
        "activity-log.json",
    ]

    /// UserDefaults keys included in each export. This is intentionally broader
    /// than `StorageKeys.resetAllKeys` so preferences and local telemetry are
    /// retained in the backup format for future restore work.
    private static let backupKeys: [String] = [
        StorageKeys.onboarded,
        StorageKeys.theme,
        StorageKeys.debugHUD,
        StorageKeys.mapStyle,
        StorageKeys.units,
        StorageKeys.completedTrails,
        StorageKeys.coverage,
        StorageKeys.coverageSinceCompletion,
        StorageKeys.favorites,
        StorageKeys.activeRecording,
        StorageKeys.userLocationLat,
        StorageKeys.userLocationLon,
        StorageKeys.areaOpenedAt,
        StorageKeys.appSessions,
        StorageKeys.prefetchNearbyLastLat,
        StorageKeys.prefetchNearbyLastLon,
        StorageKeys.hikeHistoryMigrationVersion,
    ]

    // MARK: - Export

    enum ExportError: LocalizedError {
        /// A backup file exists on disk but couldn't be read. Surfaced
        /// instead of silently dropping the file so the user never
        /// "backs up" an export that's secretly missing their hikes.
        case fileUnreadable(filename: String, underlying: String)

        var errorDescription: String? {
            switch self {
            case .fileUnreadable(let filename, let underlying):
                return "Couldn't read \(filename) for backup (\(underlying)). Nothing was exported — do NOT reset, because \(filename) can't be regenerated. Try again, or relaunch the app first."
            }
        }
    }

    struct Export: Codable {
        let version: Int
        let exportedAt: String
        let appBuild: String
        let userDefaults: [String: StoredValue]
        /// filename → base64-encoded contents
        let files: [String: String]
    }

    /// Discriminated union of UserDefaults value types this app
    /// actually uses. `Codable` synthesis emits clean JSON like
    /// `{"data":{"_0":"base64..."}}` which is portable + diff-able.
    enum StoredValue: Codable {
        case data(String)   // base64
        case string(String)
        case bool(Bool)
        case int(Int)
        case double(Double)
    }

    /// Gather everything into a single JSON blob ready to write to a
    /// file the user can save via the share sheet.
    static func collectExport() throws -> Data {
        try collectExport(
            userDefaults: .standard,
            documentsDirectory: documentsDir
        )
    }

    /// Isolated export seam for tests and future tooling. Callers can provide a
    /// temporary defaults suite and directory without touching host Documents.
    static func collectExport(
        userDefaults: UserDefaults,
        documentsDirectory: URL
    ) throws -> Data {
        var defaults: [String: StoredValue] = [:]
        for key in backupKeys {
            guard let raw = userDefaults.object(forKey: key) else { continue }
            defaults[key] = classify(raw)
        }

        var files: [String: String] = [:]
        for filename in documentFilenames {
            let url = documentsDirectory.appendingPathComponent(filename)
            // A MISSING file is a legitimate empty state — a user who
            // has never recorded a hike simply has no hike-history.json,
            // and that should export cleanly. But a file that EXISTS and
            // can't be read is a silent-data-loss trap: the old `try?`
            // dropped it from the export with no signal, so a user could
            // "back up", reset, and only then discover the recordings
            // were never in the file. Distinguish the two and fail loud
            // on the dangerous one.
            guard FileManager.default.fileExists(atPath: url.path) else { continue }
            do {
                let data = try Data(contentsOf: url)
                files[filename] = data.base64EncodedString()
            } catch {
                throw ExportError.fileUnreadable(
                    filename: filename,
                    underlying: error.localizedDescription)
            }
        }

        let exp = Export(
            version: schemaVersion,
            exportedAt: ISO8601DateFormatter().string(from: Date()),
            appBuild: Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?",
            userDefaults: defaults,
            files: files
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(exp)
    }

    /// Suggested filename for the share sheet — timestamped so users
    /// can keep multiple backups in Files without overwriting.
    static func suggestedFilename() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        return "trekdex-backup-\(formatter.string(from: Date())).json"
    }

    /// NSNumber/Bool disambiguation via objCType. `as? Bool` on a
    /// CFNumber-backed Int returns nil (good), and `as? Int` on a
    /// Double NSNumber would truncate silently (bad) — checking
    /// objCType first avoids both pitfalls.
    private static func classify(_ raw: Any) -> StoredValue? {
        if let d = raw as? Data {
            return .data(d.base64EncodedString())
        }
        if let s = raw as? String {
            return .string(s)
        }
        if let n = raw as? NSNumber {
            let type = String(cString: n.objCType)
            if type == "c" || type == "B" {
                return .bool(n.boolValue)
            }
            if type == "d" || type == "f" {
                return .double(n.doubleValue)
            }
            return .int(n.intValue)
        }
        return nil
    }

    // MARK: - Import

    enum ImportError: LocalizedError, Equatable {
        case importTemporarilyUnavailable

        var errorDescription: String? {
            switch self {
            case .importTemporarilyUnavailable:
                return "Import is temporarily unavailable while restore safety is being improved. Your existing data was not changed. Export All Data remains available."
            }
        }
    }

    /// Pure availability seam: every payload receives the same fail-closed
    /// result without decoding it or consulting current app state.
    static func importRejection(for _: Data) -> ImportError {
        .importTemporarilyUnavailable
    }

    /// Restore is fail-closed until it can be made restart-atomic. This throws
    /// before reading or mutating UserDefaults, Documents, or live services.
    @MainActor
    static func performImport(from data: Data) throws {
        throw importRejection(for: data)
    }
}
