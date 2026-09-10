import Foundation

/// Typed failures from the completed-recording history boundary.
///
/// A missing file is the only state interpreted as an empty history. If a file
/// exists but cannot be read or decoded, callers receive an error and no write
/// is attempted, preserving the original bytes for recovery.
enum RecordingHistoryStoreError: Error, LocalizedError, Sendable, Equatable {
    case unreadable(path: String, reason: String)
    case corrupt(path: String, reason: String)
    case encodingFailed(reason: String)
    case writeFailed(path: String, reason: String)
    case verificationFailed(path: String, reason: String)
    case identifierConflict(String)

    var errorDescription: String? {
        switch self {
        case .unreadable(_, let reason):
            return "Your hike history exists but couldn't be read (\(reason)). It was left untouched. Try again after relaunching; do not reset your data."
        case .corrupt(_, let reason):
            return "Your hike history is damaged and couldn't be decoded (\(reason)). It was left untouched so it can be recovered."
        case .encodingFailed(let reason):
            return "This recording couldn't be prepared for saving (\(reason)). Your active recording is still safe."
        case .writeFailed(_, let reason):
            return "This recording couldn't be written safely (\(reason)). Your active recording is still safe; retry the save."
        case .verificationFailed(_, let reason):
            return "The saved hike couldn't be verified (\(reason)). Your active recording is still safe; retry the save."
        case .identifierConflict(let id):
            return "A different saved hike already uses recording identifier \(id). The active recording was left untouched for recovery."
        }
    }
}

/// Serialized JSON-file storage for completed recordings.
///
/// Every read-modify-write operation holds one lock, preventing a detached UI
/// read or delete from racing a stop/save. Replacements use Foundation's atomic
/// write, then read and decode the bytes again before reporting success.
final class RecordingHistoryStore: @unchecked Sendable {
    typealias AtomicWriter = @Sendable (Data, URL) throws -> Void

    let fileURL: URL

    private let lock = NSLock()
    private let atomicWriter: AtomicWriter

    init(
        fileURL: URL,
        atomicWriter: @escaping AtomicWriter = { data, url in
            try data.write(to: url, options: .atomic)
        }
    ) {
        self.fileURL = fileURL
        self.atomicWriter = atomicWriter
    }

    func load() throws -> [SavedRecording] {
        lock.lock()
        defer { lock.unlock() }
        return try loadUnlocked()
    }

    /// Idempotently prepend a completed recording. Reusing the same stable
    /// identifier succeeds only for an exact payload match; a different path or
    /// timestamp with that ID is a recovery conflict and is never discarded.
    @discardableResult
    func prepend(_ recording: SavedRecording) throws -> SavedRecording {
        lock.lock()
        defer { lock.unlock() }

        var history = try loadUnlocked()
        if let existing = history.first(where: { $0.id == recording.id }) {
            guard existing == recording else {
                throw RecordingHistoryStoreError.identifierConflict(recording.id)
            }
            return existing
        }
        history.insert(recording, at: 0)
        try replaceUnlocked(history)
        return recording
    }

    func record(id: String) throws -> SavedRecording? {
        lock.lock()
        defer { lock.unlock() }
        return try loadUnlocked().first { $0.id == id }
    }

    /// Apply a read-modify-write while holding the same lock for the complete
    /// transaction. Used by history repair so it cannot replace a hike that was
    /// appended after an earlier unlocked read.
    func update(_ transform: (inout [SavedRecording]) throws -> Void) throws {
        lock.lock()
        defer { lock.unlock() }
        var history = try loadUnlocked()
        let original = history
        try transform(&history)
        guard history != original else { return }
        try replaceUnlocked(history)
    }

    func replace(_ history: [SavedRecording]) throws {
        lock.lock()
        defer { lock.unlock() }
        try replaceUnlocked(history)
    }

    func delete(ids: Set<String>) throws {
        guard !ids.isEmpty else { return }
        lock.lock()
        defer { lock.unlock() }

        var history = try loadUnlocked()
        let originalCount = history.count
        history.removeAll { ids.contains($0.id) }
        guard history.count != originalCount else { return }
        try replaceUnlocked(history)
    }

    func removeFileIfPresent() throws {
        lock.lock()
        defer { lock.unlock() }
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
        try FileManager.default.removeItem(at: fileURL)
    }

    private func loadUnlocked() throws -> [SavedRecording] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return [] }

        let data: Data
        do {
            data = try Data(contentsOf: fileURL)
        } catch {
            throw RecordingHistoryStoreError.unreadable(
                path: fileURL.path,
                reason: error.localizedDescription
            )
        }

        do {
            return try JSONDecoder().decode([SavedRecording].self, from: data)
                .sorted { $0.startedAt > $1.startedAt }
        } catch {
            throw RecordingHistoryStoreError.corrupt(
                path: fileURL.path,
                reason: error.localizedDescription
            )
        }
    }

    private func replaceUnlocked(_ history: [SavedRecording]) throws {
        let data: Data
        do {
            data = try JSONEncoder().encode(history)
            let preflight = try JSONDecoder().decode([SavedRecording].self, from: data)
            guard preflight == history else {
                throw RecordingHistoryStoreError.verificationFailed(
                    path: fileURL.path,
                    reason: "the encoded records did not round-trip"
                )
            }
        } catch let error as RecordingHistoryStoreError {
            throw error
        } catch {
            throw RecordingHistoryStoreError.encodingFailed(reason: error.localizedDescription)
        }

        do {
            try atomicWriter(data, fileURL)
        } catch {
            // Some storage layers can commit the atomic replacement and then
            // surface a late error. Reconcile that outcome here so callers do
            // not retain/retry an active checkpoint for a row already verified
            // on disk. Any mismatch remains a hard failure.
            if let persistedData = try? Data(contentsOf: fileURL),
               persistedData == data,
               let persisted = try? JSONDecoder().decode([SavedRecording].self, from: persistedData),
               persisted == history {
                return
            }
            throw RecordingHistoryStoreError.writeFailed(
                path: fileURL.path,
                reason: error.localizedDescription
            )
        }

        let persistedData: Data
        do {
            persistedData = try Data(contentsOf: fileURL)
        } catch {
            throw RecordingHistoryStoreError.verificationFailed(
                path: fileURL.path,
                reason: "read-back failed: \(error.localizedDescription)"
            )
        }
        guard persistedData == data else {
            throw RecordingHistoryStoreError.verificationFailed(
                path: fileURL.path,
                reason: "read-back bytes differed from the atomic write"
            )
        }
        do {
            let persisted = try JSONDecoder().decode([SavedRecording].self, from: persistedData)
            guard persisted == history else {
                throw RecordingHistoryStoreError.verificationFailed(
                    path: fileURL.path,
                    reason: "read-back records differed from the requested history"
                )
            }
        } catch let error as RecordingHistoryStoreError {
            throw error
        } catch {
            throw RecordingHistoryStoreError.verificationFailed(
                path: fileURL.path,
                reason: "read-back decode failed: \(error.localizedDescription)"
            )
        }
    }
}
