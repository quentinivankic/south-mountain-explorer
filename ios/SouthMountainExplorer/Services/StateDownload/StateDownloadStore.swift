import Foundation
import OSLog

/// Persists one ledger file per scope under Application Support (not Caches,
/// so the OS cannot purge the ledger while area files survive).
struct StateDownloadStore: Sendable {
    let directory: URL
    private static let log = Logger(subsystem: "com.southmountainexplorer.app", category: "StateDownloadStore")

    init(directory: URL) {
        self.directory = directory
    }

    static func live() -> StateDownloadStore {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return StateDownloadStore(
            directory: base.appendingPathComponent("StateDownloads", isDirectory: true)
        )
    }

    private func url(for stateKey: String) -> URL {
        directory.appendingPathComponent("\(StateCatalog.slug(stateKey)).json")
    }

    /// Missing, corrupt, or unknown-schema files read as a fresh idle ledger.
    /// Files stay on disk but become unowned, which is the safe direction.
    func load(stateKey: String) -> StateLedger {
        let fresh = StateLedger(stateKey: stateKey)
        guard let data = try? Data(contentsOf: url(for: stateKey)) else { return fresh }
        guard let ledger = try? JSONDecoder().decode(StateLedger.self, from: data),
              ledger.schemaVersion == StateLedger.currentSchemaVersion,
              ledger.record.stateKey == stateKey
        else {
            Self.log.warning("ledger for \(stateKey, privacy: .public) unreadable; resetting to idle")
            return fresh
        }
        return ledger
    }

    @discardableResult
    func save(_ ledger: StateLedger) -> Bool {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try JSONEncoder().encode(ledger)
            try data.write(to: url(for: ledger.record.stateKey), options: .atomic)
            return true
        } catch {
            Self.log.error("ledger save failed for \(ledger.record.stateKey, privacy: .public): \(error.localizedDescription, privacy: .public)")
            return false
        }
    }

    /// Forget every ledger (after Reset All / Clear All Offline Trails).
    func clearAll() {
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil
        ) else { return }
        for file in files where file.pathExtension == "json" {
            try? FileManager.default.removeItem(at: file)
        }
    }
}

/// Crash-window recovery: a kill between `store` and the next ledger flush
/// leaves a valid file with no claim. Claim it only if it can only have come
/// from this job.
enum StateClaimRecovery {
    static func recoverable(
        members: [String],
        availableOnDisk: Set<String>,
        preexisting: Set<String>,
        claims: [String: Claim],
        protected: Set<String>
    ) -> [String] {
        members.filter { id in
            availableOnDisk.contains(id)
                && !preexisting.contains(id)
                && claims[id] == nil
                && !protected.contains(id)
        }
    }
}
