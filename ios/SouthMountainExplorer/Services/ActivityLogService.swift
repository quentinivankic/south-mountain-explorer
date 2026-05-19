import Foundation

/// Append-only chronological log of meaningful user actions —
/// open area, tap trail, start/stop recording, change setting,
/// share GPX, etc. Captured to a JSON file in the Documents
/// directory and surfaced as the `activityLog` array inside the
/// Send Diagnostics bundle, so a user can attach the log to oral
/// feedback for faithful session replay.
///
/// Granularity is **meaningful actions only** (~50 entries/session
/// in heavy use). Not every tap or screen render. The instrumentation
/// sites are listed in build-18 PR C plan.
///
/// Privacy: no coordinates ever. The {areaId, trailId, mode,
/// preference-value} trio is the maximum content per entry.
@MainActor
@Observable
final class ActivityLogService {
    static let shared = ActivityLogService()

    /// Plain `Codable` value type. Stored as a JSON array.
    struct Entry: Codable, Sendable {
        let timestamp: Date
        let category: String
        let action: String
        let context: [String: String]
    }

    /// In-memory cache of the persisted log. Loaded lazily on
    /// first access so cold launch doesn't pay for a file read
    /// the user may never trigger a diagnostics bundle on.
    private var entries: [Entry] = []
    private var didLoad = false

    /// Pending entries waiting for the debounced write. Flushed
    /// every `writeDebounceInterval` seconds OR on `flush()`.
    /// Without debouncing, a burst of taps would thrash the file
    /// — the GPS-polling loop alone is 2 Hz during recording.
    private var dirty = false
    private var debounceTask: Task<Void, Never>?
    private let writeDebounceInterval: Duration = .seconds(1)

    /// Maximum entries kept in-memory + on-disk. ~5000 ≈ one
    /// heavy week of use; drops oldest 1000 in one shot when
    /// hit (avoids per-entry shift cost of always dropping one).
    private let maxEntries = 5000
    private let dropBatch = 1000

    private static var logFileURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("activity-log.json")
    }

    private init() {}

    // MARK: - Public API

    /// Append an entry. Cheap — appends in-memory, schedules a
    /// debounced disk write. Safe to call from any view body /
    /// button handler.
    func log(category: String, action: String, context: [String: String] = [:]) {
        loadIfNeeded()
        entries.append(Entry(
            timestamp: Date(),
            category: category,
            action: action,
            context: context
        ))
        if entries.count > maxEntries {
            entries.removeFirst(dropBatch)
        }
        scheduleWrite()
    }

    /// Snapshot of recent entries for inclusion in
    /// `DiagnosticsService.exportBundle`. Sorted newest-last
    /// (chronological) for grep-friendliness alongside the OSLog
    /// section.
    func recentEntries(limit: Int = 5000) -> [Entry] {
        loadIfNeeded()
        let slice = entries.suffix(limit)
        return Array(slice)
    }

    /// Force a disk write — call before snapshotting (so a
    /// just-logged entry lands on disk before the diag bundle is
    /// built). Also called on app background to make sure pending
    /// writes don't vanish if the app is later killed.
    func flush() {
        debounceTask?.cancel()
        debounceTask = nil
        if dirty { writeNow() }
    }

    /// Wipe the log. Wired into Reset All Progress so a fresh
    /// device starts with a clean slate. Disk file is deleted
    /// rather than left as `[]` so the next launch doesn't read a
    /// stale file.
    func clear() {
        entries.removeAll()
        dirty = false
        debounceTask?.cancel()
        debounceTask = nil
        try? FileManager.default.removeItem(at: Self.logFileURL)
    }

    // MARK: - Persistence

    /// Lazy-load the persisted log on first access. Bad / missing
    /// file produces an empty list — log loss is preferable to a
    /// startup crash.
    private func loadIfNeeded() {
        guard !didLoad else { return }
        didLoad = true
        guard let data = try? Data(contentsOf: Self.logFileURL) else { return }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        if let decoded = try? decoder.decode([Entry].self, from: data) {
            entries = decoded
        }
    }

    private func scheduleWrite() {
        dirty = true
        debounceTask?.cancel()
        debounceTask = Task { [weak self] in
            try? await Task.sleep(for: self?.writeDebounceInterval ?? .seconds(1))
            guard !Task.isCancelled else { return }
            self?.writeNow()
        }
    }

    private func writeNow() {
        dirty = false
        let snapshot = entries
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(snapshot) else { return }
        try? data.write(to: Self.logFileURL, options: .atomic)
    }
}
