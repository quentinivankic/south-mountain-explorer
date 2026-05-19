import Foundation

/// Bundle every piece of user-owned app data into a single JSON file
/// you can save to Files, and restore from that same file later.
///
/// Use this to safely test the new-user experience: Export your data
/// to Files → tap Reset All Progress → delete + reinstall (or just
/// poke around the fresh state) → Import the saved JSON → everything
/// back exactly as it was.
///
/// Covered:
///   - All `StorageKeys.*` UserDefaults entries (progress, coverage,
///     favourites, prefs, telemetry).
///   - `Documents/hike-history.json` — the recorded hikes themselves.
///   - `Documents/activity-log.json` — the diag-bundle activity stream.
///
/// NOT covered (regenerable, not user-owned):
///   - `Caches/areas/...` — area data, re-fetched from R2 on demand.
///   - The bundled `areas-index.json` — read-only, ships with the app.
enum DataBackupManager {

    /// Bumped if a future format change makes older exports
    /// incompatible. Import rejects mismatched versions with a clear
    /// message rather than silently corrupting state.
    static let schemaVersion = 1

    private static let documentsDir: URL = {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }()

    /// Files in Documents/ that hold real user data. The activity log
    /// is included so a backup-then-restore truly restores everything,
    /// including the audit trail the Send Diagnostics bundle samples.
    private static let documentFilenames: [String] = [
        "hike-history.json",
        "activity-log.json",
    ]

    /// UserDefaults keys we round-trip. Intentionally broader than
    /// `StorageKeys.resetAllKeys` — a backup-then-restore should look
    /// like nothing happened, so we also preserve prefs (theme, units)
    /// and engagement telemetry (areaOpenedAt, appSessions).
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
        var defaults: [String: StoredValue] = [:]
        for key in backupKeys {
            guard let raw = UserDefaults.standard.object(forKey: key) else { continue }
            defaults[key] = classify(raw)
        }

        var files: [String: String] = [:]
        for filename in documentFilenames {
            let url = documentsDir.appendingPathComponent(filename)
            if let data = try? Data(contentsOf: url) {
                files[filename] = data.base64EncodedString()
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

    enum ImportError: LocalizedError {
        case unsupportedVersion(Int)
        case decodeFailed(String)
        case activeRecordingInProgress

        var errorDescription: String? {
            switch self {
            case .unsupportedVersion(let v):
                return "Backup is version \(v); this app expects version \(DataBackupManager.schemaVersion). Make a fresh export from a build that matches."
            case .decodeFailed(let detail):
                return "Couldn't read the backup file: \(detail)"
            case .activeRecordingInProgress:
                return "Stop the active recording before importing — importing while recording would lose the in-progress hike."
            }
        }
    }

    /// Replace every backup-covered piece of state with what's in
    /// `data`. Atomic in the sense that we wipe ALL backup keys + files
    /// first, then restore, so a restored install matches the export
    /// snapshot exactly (no stale keys from the destination linger).
    ///
    /// Throws if the JSON doesn't decode, the schema version differs,
    /// or a recording is currently in progress (importing then would
    /// silently discard the user's mid-hike state).
    @MainActor
    static func performImport(from data: Data) throws {
        if RecordingService.shared.activeRecording != nil {
            throw ImportError.activeRecordingInProgress
        }

        let exp: Export
        do {
            exp = try JSONDecoder().decode(Export.self, from: data)
        } catch {
            throw ImportError.decodeFailed(error.localizedDescription)
        }

        guard exp.version == schemaVersion else {
            throw ImportError.unsupportedVersion(exp.version)
        }

        // Wipe first so any keys/files in the destination that aren't
        // in the export don't survive the restore.
        for key in backupKeys {
            UserDefaults.standard.removeObject(forKey: key)
        }
        for filename in documentFilenames {
            try? FileManager.default.removeItem(
                at: documentsDir.appendingPathComponent(filename)
            )
        }

        // Restore UserDefaults.
        for (key, value) in exp.userDefaults {
            switch value {
            case .data(let b64):
                if let d = Data(base64Encoded: b64) {
                    UserDefaults.standard.set(d, forKey: key)
                }
            case .string(let s):
                UserDefaults.standard.set(s, forKey: key)
            case .bool(let b):
                UserDefaults.standard.set(b, forKey: key)
            case .int(let i):
                UserDefaults.standard.set(i, forKey: key)
            case .double(let dv):
                UserDefaults.standard.set(dv, forKey: key)
            }
        }

        // Restore Documents files.
        for (filename, b64) in exp.files {
            guard let d = Data(base64Encoded: b64) else { continue }
            let url = documentsDir.appendingPathComponent(filename)
            try? d.write(to: url, options: .atomic)
        }
    }
}
