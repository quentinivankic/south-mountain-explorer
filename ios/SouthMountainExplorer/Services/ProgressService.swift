import Foundation
import UIKit

// areaId -> trailId -> ISO8601 completion date
private let storageKey = "summit:completed"

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
    /// current area data. Called from AreaView once the trails have loaded so
    /// the user's progress display lines up with what they can actually see.
    func pruneOrphanCompletions(areaId: String, validTrailIds: Set<String>) {
        guard var area = completions[areaId], !area.isEmpty else { return }
        let stale = area.keys.filter { !validTrailIds.contains($0) }
        guard !stale.isEmpty else { return }
        for tid in stale { area.removeValue(forKey: tid) }
        completions[areaId] = area
        saveLocal()
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
