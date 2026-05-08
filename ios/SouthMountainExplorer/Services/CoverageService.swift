import Foundation

// areaId -> trailId -> coverage (0..1)
private let storageKey = "summit:coverage"

@MainActor
@Observable
final class CoverageService {
    static let shared = CoverageService()

    private(set) var state: [String: [String: Double]] = [:]

    private init() {
        state = readLocal()
    }

    func coverage(for areaId: String) -> [String: Double] {
        state[areaId] ?? [:]
    }

    func trailCoverage(areaId: String, trailId: String) -> Double {
        state[areaId]?[trailId] ?? 0
    }

    func mergeCoverage(areaId: String, delta: [String: Double]) async {
        var area = state[areaId] ?? [:]
        for (tid, v) in delta {
            area[tid] = min(1.0, max(area[tid] ?? 0, v))
        }
        state[areaId] = area
        saveLocal()
    }

    func resetAreaCoverage(areaId: String) async {
        state[areaId] = [:]
        saveLocal()
    }

    // MARK: - Local persistence

    private func readLocal() -> [String: [String: Double]] {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let decoded = try? JSONDecoder().decode([String: [String: Double]].self, from: data)
        else { return [:] }
        return decoded
    }

    private func saveLocal() {
        guard let data = try? JSONEncoder().encode(state) else { return }
        UserDefaults.standard.set(data, forKey: storageKey)
    }
}
