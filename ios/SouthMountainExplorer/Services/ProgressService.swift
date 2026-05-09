import Foundation

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

    // MARK: - Write

    func markComplete(areaId: String, trailId: String) async {
        let date = ISO8601DateFormatter().string(from: Date())
        var area = completions[areaId] ?? [:]
        area[trailId] = date
        completions[areaId] = area
        saveLocal()
    }

    func toggleTrail(areaId: String, trailId: String) async {
        if isComplete(areaId: areaId, trailId: trailId) {
            var area = completions[areaId] ?? [:]
            area.removeValue(forKey: trailId)
            completions[areaId] = area
            saveLocal()
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
