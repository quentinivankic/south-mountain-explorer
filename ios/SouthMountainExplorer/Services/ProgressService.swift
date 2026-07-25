import Foundation
import UIKit

// areaId -> trailId -> ISO8601 completion date
private let storageKey = StorageKeys.completedTrails

@MainActor
@Observable
final class ProgressService {
    static let shared = ProgressService()

    private(set) var completions: [String: [String: String]] = [:]

    /// Geometry fingerprints of every completed trail, so completion follows the
    /// PHYSICAL trail across duplicate areas (see `Trail.completionFingerprint`).
    /// Persisted, and backfilled from `completions` as areas load via
    /// `indexArea`. A superset key — never the source of truth for a specific
    /// (areaId, trailId), only the cross-area credit.
    private(set) var completedFingerprints: Set<String> = []

    private init() {
        completions = readLocal()
        completedFingerprints = readFingerprints()
    }

    // MARK: - Read

    func isComplete(areaId: String, trailId: String) -> Bool {
        completions[areaId]?[trailId] != nil
    }

    /// Completion for a trail you have in hand — credits it if this exact
    /// (areaId, trailId) is complete OR if the SAME physical trail (identical
    /// geometry) is complete in any area. Fixes progress recorded under a
    /// duplicate-area twin not showing here. Falls back to the id check when the
    /// fingerprint set hasn't indexed the source twin yet.
    func isComplete(_ trail: Trail, areaId: String) -> Bool {
        if completions[areaId]?[trail.id] != nil { return true }
        return completedFingerprints.contains(trail.completionFingerprint)
    }

    /// Backfill the fingerprint index from an area's now-loaded geometry: every
    /// trail that is complete by (areaId, trailId) contributes its fingerprint.
    /// Idempotent; call on area load. This is how existing completions (stored
    /// before fingerprints existed) become cross-area, and how a live completion
    /// propagates to its twin on next load.
    func indexArea(areaId: String, trails: [Trail]) {
        guard let done = completions[areaId], !done.isEmpty else { return }
        var added = false
        for t in trails where done[t.id] != nil {
            if completedFingerprints.insert(t.completionFingerprint).inserted { added = true }
        }
        if added { saveFingerprints() }
    }

    /// Parsed completion timestamp for the trail, or nil if never
    /// completed. Drives the map's walked-since-completion overlay
    /// — that filter wants to know which hikes happened *after*
    /// the last completion event.
    /// ISO8601DateFormatter isn't Sendable so we build one per call;
    /// at sheet-open / selection-change rate that's free.
    func completionDate(areaId: String, trailId: String) -> Date? {
        guard let iso = completions[areaId]?[trailId] else { return nil }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: iso)
    }

    func completedTrails(in areaId: String) -> [String: String] {
        completions[areaId] ?? [:]
    }

    func completionCount(in areaId: String) -> Int {
        completions[areaId]?.count ?? 0
    }

    /// Number of completed trails for an area, restricted to IDs that exist
    /// in the supplied trail set. Use this when displaying a count alongside
    /// the area's *current* trail data — otherwise the raw `completionCount`
    /// can include orphan completions whose IDs were rotated out by a Refresh
    /// Trail Data call.
    func completionCount(in areaId: String, validTrailIds: Set<String>) -> Int {
        guard let area = completions[areaId] else { return 0 }
        return area.keys.filter(validTrailIds.contains).count
    }

    /// Completion count that also credits duplicate-area twins, so the header
    /// count matches the per-row checkmarks (both go through `isComplete(_:)`).
    /// Needs the trails in hand to fingerprint them.
    func completionCount(in areaId: String, trails: [Trail]) -> Int {
        trails.reduce(0) { $0 + (isComplete($1, areaId: areaId) ? 1 : 0) }
    }

    /// Ids of the trails in `trails` that are complete, crediting duplicate-area
    /// twins by geometry. Map coloring and header counts must go through this
    /// (not the raw `completedTrails(in:).keys`), or they show 0 under a twin
    /// where the completion was recorded under the OTHER identical area —
    /// the cyan lines and the card count would then disagree with the checkmarks.
    func completedTrailIds(in areaId: String, among trails: [Trail]) -> Set<String> {
        Set(trails.lazy.filter { self.isComplete($0, areaId: areaId) }.map(\.id))
    }

    /// Drop completions whose trail IDs no longer match anything in the
    /// current area data. Kept for explicit cleanup paths but no longer
    /// called automatically — pruning silently lost a tester's progress
    /// after a Refresh Trail Data call. Display-time filtering via
    /// `completionCount(in:validTrailIds:)` is the safer pattern.
    func pruneOrphanCompletions(areaId: String, validTrailIds: Set<String>) {
        guard var area = completions[areaId], !area.isEmpty else { return }
        let stale = area.keys.filter { !validTrailIds.contains($0) }
        guard !stale.isEmpty else { return }
        for tid in stale { area.removeValue(forKey: tid) }
        completions[areaId] = area
        saveLocal()
    }

    /// Mark a batch of trail completions without firing per-trail haptics —
    /// used when re-deriving completions from recorded hike history on area
    /// load so we don't buzz the user every time they open an area.
    func bulkMarkComplete(areaId: String, trailIds: Set<String>) {
        guard !trailIds.isEmpty else { return }
        var area = completions[areaId] ?? [:]
        var added = false
        let stamp = ISO8601DateFormatter().string(from: Date())
        for tid in trailIds where area[tid] == nil {
            area[tid] = stamp
            added = true
        }
        if added {
            completions[areaId] = area
            saveLocal()
        }
    }

    // MARK: - Write

    func markComplete(areaId: String, trailId: String) async {
        var area = completions[areaId] ?? [:]
        let wasComplete = area[trailId] != nil
        area[trailId] = ISO8601DateFormatter().string(from: Date())
        completions[areaId] = area
        saveLocal()
        if !wasComplete {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    func toggleTrail(areaId: String, trailId: String) async {
        if isComplete(areaId: areaId, trailId: trailId) {
            var area = completions[areaId] ?? [:]
            area.removeValue(forKey: trailId)
            completions[areaId] = area
            saveLocal()
            UISelectionFeedbackGenerator().selectionChanged()
        } else {
            await markComplete(areaId: areaId, trailId: trailId)
        }
    }

    func resetArea(areaId: String) async {
        completions[areaId] = [:]
        saveLocal()
    }

    /// Wipe every completion across every area and clear the
    /// underlying UserDefaults entry. Called by Settings → Reset All
    /// Progress. Mutates the `@Observable` `completions` dictionary
    /// so SwiftUI views holding it refresh immediately — without
    /// this the UI keeps showing checkmarks until next launch.
    func resetAll() {
        completions = [:]
        completedFingerprints = []
        UserDefaults.standard.removeObject(forKey: storageKey)
        UserDefaults.standard.removeObject(forKey: StorageKeys.completedTrailFingerprints)
    }

    /// Re-read the completions dictionary from UserDefaults. Called
    /// after Settings → Import overwrites the underlying entry — the
    /// in-memory `@Observable` copy was loaded at init and would
    /// otherwise keep showing pre-import state until next launch.
    func reload() {
        completions = readLocal()
        // Imported completions carry no geometry, so drop the fingerprint index
        // and let `indexArea` rebuild it lazily as areas load.
        completedFingerprints = []
        UserDefaults.standard.removeObject(forKey: StorageKeys.completedTrailFingerprints)
    }

    /// Rewrite every trail-id key through `transform`. Used by the
    /// build-8 migration to canonicalize legacy ids so old marked
    /// completions line up with stable post-fix ids. On collision
    /// (two old keys collapse to the same canonical key), keep the
    /// earlier completion timestamp.
    func rekeyTrailIds(_ transform: (String) -> String) {
        completions = Self.rekey(completions, transform: transform)
        saveLocal()
    }

    /// Pure-function form of the rekey-with-collision-merge logic.
    /// Tests hit this directly so they don't have to instantiate
    /// the @Observable singleton or write through UserDefaults.
    /// Collision rule: keep the earlier ISO8601 timestamp. The
    /// timestamps are formatted ISO8601 strings, so lexicographic
    /// comparison gives chronological ordering.
    ///
    /// `nonisolated` because this is a pure function over its
    /// arguments — it touches no `ProgressService` instance state.
    /// Without this the static would inherit the enclosing class's
    /// `@MainActor` isolation and become unreachable from
    /// non-actor-isolated callers (including unit tests).
    nonisolated static func rekey(_ completions: [String: [String: String]],
                                  transform: (String) -> String) -> [String: [String: String]] {
        var newCompletions: [String: [String: String]] = [:]
        for (areaId, areaComp) in completions {
            var newArea: [String: String] = [:]
            for (tid, stamp) in areaComp {
                let newTid = transform(tid)
                if let existing = newArea[newTid], existing <= stamp {
                    continue
                }
                newArea[newTid] = stamp
            }
            newCompletions[areaId] = newArea
        }
        return newCompletions
    }

    // MARK: - Local persistence

    private func readLocal() -> [String: [String: String]] {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let decoded = try? JSONDecoder().decode([String: [String: String]].self, from: data)
        else { return [:] }
        return decoded
    }

    private func saveLocal() {
        guard let data = try? JSONEncoder().encode(completions) else { return }
        UserDefaults.standard.set(data, forKey: storageKey)
    }

    private func readFingerprints() -> Set<String> {
        guard let data = UserDefaults.standard.data(forKey: StorageKeys.completedTrailFingerprints),
              let decoded = try? JSONDecoder().decode(Set<String>.self, from: data)
        else { return [] }
        return decoded
    }

    private func saveFingerprints() {
        guard let data = try? JSONEncoder().encode(completedFingerprints) else { return }
        UserDefaults.standard.set(data, forKey: StorageKeys.completedTrailFingerprints)
    }
}
