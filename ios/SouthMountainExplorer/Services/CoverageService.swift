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
        Task { await syncWithServer() }
        Task { await observeAuthChanges() }
    }

    func coverage(for areaId: String) -> [String: Double] {
        state[areaId] ?? [:]
    }

    func trailCoverage(areaId: String, trailId: String) -> Double {
        state[areaId]?[trailId] ?? 0
    }

    func mergeCoverage(areaId: String, delta: [String: Double]) async {
        var area = state[areaId] ?? [:]
        struct Row: Encodable {
            let user_id, area_id, trail_id: String
            let coverage: Double
        }
        var rows: [Row] = []
        let uid = AuthService.shared.userId

        for (tid, v) in delta {
            let next = min(1.0, max(area[tid] ?? 0, v))
            if next != area[tid] {
                area[tid] = next
                if let uid {
                    rows.append(Row(user_id: uid, area_id: areaId, trail_id: tid, coverage: next))
                }
            }
        }
        state[areaId] = area
        saveLocal()

        guard !rows.isEmpty else { return }
        try? await supabase.from("trail_coverage")
            .upsert(rows, onConflict: "user_id,area_id,trail_id")
            .execute()
    }

    func resetAreaCoverage(areaId: String) async {
        state[areaId] = [:]
        saveLocal()
        guard let uid = AuthService.shared.userId else { return }
        try? await supabase.from("trail_coverage")
            .delete()
            .eq("user_id", value: uid)
            .eq("area_id", value: areaId)
            .execute()
    }

    private func observeAuthChanges() async {
        for await (_, session) in supabase.auth.authStateChanges {
            if session != nil { await syncWithServer() }
        }
    }

    private func syncWithServer() async {
        guard let uid = AuthService.shared.userId else { return }
        await pushLocalToServer(uid: uid)
        await pullFromServer(uid: uid)
    }

    private func pushLocalToServer(uid: String) async {
        struct Row: Encodable {
            let user_id, area_id, trail_id: String
            let coverage: Double
        }
        var rows: [Row] = []
        for (areaId, trails) in state {
            for (trailId, v) in trails where v > 0 {
                rows.append(Row(user_id: uid, area_id: areaId, trail_id: trailId, coverage: v))
            }
        }
        guard !rows.isEmpty else { return }
        try? await supabase.from("trail_coverage")
            .upsert(rows, onConflict: "user_id,area_id,trail_id")
            .execute()
    }

    private func pullFromServer(uid: String) async {
        struct Row: Decodable {
            let area_id, trail_id: String
            let coverage: Double
        }
        guard let rows: [Row] = try? await supabase
            .from("trail_coverage")
            .select("area_id, trail_id, coverage")
            .eq("user_id", value: uid)
            .execute()
            .value
        else { return }

        var next: [String: [String: Double]] = [:]
        for row in rows {
            next[row.area_id, default: [:]][row.trail_id] = row.coverage
        }
        // Merge: keep local if higher (in-flight changes)
        for (aid, trails) in state {
            for (tid, v) in trails {
                next[aid, default: [:]][tid] = max(next[aid]?[tid] ?? 0, v)
            }
        }
        state = next
        saveLocal()
    }

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
