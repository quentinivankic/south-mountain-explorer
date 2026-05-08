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
        Task { await syncWithServer() }
        Task { await observeAuthChanges() }
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

        guard let uid = AuthService.shared.userId else { return }
        struct Row: Encodable {
            let user_id, area_id, trail_id, completed_at: String
        }
        let row = Row(user_id: uid, area_id: areaId, trail_id: trailId, completed_at: date)
        try? await supabase.from("trail_completions").insert(row).execute()
    }

    func toggleTrail(areaId: String, trailId: String) async {
        if isComplete(areaId: areaId, trailId: trailId) {
            var area = completions[areaId] ?? [:]
            area.removeValue(forKey: trailId)
            completions[areaId] = area
            saveLocal()
            guard let uid = AuthService.shared.userId else { return }
            try? await supabase.from("trail_completions")
                .delete()
                .eq("user_id", value: uid)
                .eq("area_id", value: areaId)
                .eq("trail_id", value: trailId)
                .execute()
        } else {
            await markComplete(areaId: areaId, trailId: trailId)
        }
    }

    func resetArea(areaId: String) async {
        completions[areaId] = [:]
        saveLocal()
        guard let uid = AuthService.shared.userId else { return }
        try? await supabase.from("trail_completions")
            .delete()
            .eq("user_id", value: uid)
            .eq("area_id", value: areaId)
            .execute()
    }

    // MARK: - Sync

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
            let user_id, area_id, trail_id, completed_at: String
        }
        var rows: [Row] = []
        for (areaId, trails) in completions {
            for (trailId, date) in trails {
                rows.append(Row(user_id: uid, area_id: areaId, trail_id: trailId, completed_at: date))
            }
        }
        guard !rows.isEmpty else { return }
        try? await supabase.from("trail_completions")
            .upsert(rows, onConflict: "user_id,area_id,trail_id")
            .execute()
    }

    private func pullFromServer(uid: String) async {
        struct Row: Decodable {
            let area_id, trail_id, completed_at: String
        }
        guard let rows: [Row] = try? await supabase
            .from("trail_completions")
            .select("area_id, trail_id, completed_at")
            .eq("user_id", value: uid)
            .execute()
            .value
        else { return }

        var next: [String: [String: String]] = [:]
        for row in rows {
            next[row.area_id, default: [:]][row.trail_id] = row.completed_at
        }
        completions = next
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
