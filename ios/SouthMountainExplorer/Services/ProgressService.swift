import Foundation
import UIKit

// areaId -> trailId -> ISO8601 completion date
private let storageKey = StorageKeys.completedTrails

@MainActor
@Observable
final class ProgressService {
    static let shared = ProgressService()

    private(set) var completions: [String: [String: String]] = [:]

    private init() {
        completions = readLocal()
    }

    // MARK: - Read

    func isComplete(areaId: String, trailId: String) -> Bool {
        completions[areaId]?[trailId] != nil
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
}
