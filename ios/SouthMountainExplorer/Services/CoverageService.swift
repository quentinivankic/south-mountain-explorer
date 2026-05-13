import Foundation

// areaId -> trailId -> coverage (0..1)
private let storageKey = StorageKeys.coverage

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

    /// Rewrite every trail-id key through `transform`. Used by the
    /// build-8 migration to canonicalize legacy ids (stripping the
    /// position-counter suffix) so old recorded coverage lines up
    /// with stable post-fix ids. On collision (two old keys collapse
    /// to the same canonical key), keep the higher fraction.
    func rekeyTrailIds(_ transform: (String) -> String) {
        state = Self.rekey(state, transform: transform)
        saveLocal()
    }

    /// Pure-function form of the rekey-with-collision-merge logic.
    /// Lives alongside the instance method so unit tests can
    /// exercise the merge rule without singleton / UserDefaults
    /// plumbing. The collision rule is "keep the higher fraction"
    /// — when two old keys collapse to the same canonical key, we
    /// preserve whatever the user has actually covered the most of.
    ///
    /// `nonisolated` because this is a pure function over its
    /// arguments — it touches no `CoverageService` instance state.
    /// Without this the static would inherit the enclosing class's
    /// `@MainActor` isolation and become unreachable from
    /// non-actor-isolated callers (including unit tests).
    nonisolated static func rekey(_ state: [String: [String: Double]],
                                  transform: (String) -> String) -> [String: [String: Double]] {
        var newState: [String: [String: Double]] = [:]
        for (areaId, areaCov) in state {
            var newArea: [String: Double] = [:]
            for (tid, v) in areaCov {
                let newTid = transform(tid)
                newArea[newTid] = max(newArea[newTid] ?? 0, v)
            }
            newState[areaId] = newArea
        }
        return newState
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
