import Foundation

private let storageKey = "summit:favorites"
private let staleDays: Double = 30

@MainActor
@Observable
final class FavoritesService {
    static let shared = FavoritesService()

    private(set) var favoriteIds: Set<String> = []

    private init() {
        favoriteIds = Set(readLocal())
        Task { await syncWithServer() }
        Task { await observeAuthChanges() }
        Task { await downloadFavoriteAreas() }
    }

    func isFavorite(_ areaId: String) -> Bool {
        favoriteIds.contains(areaId)
    }

    func toggle(areaId: String) async {
        let wasFav = favoriteIds.contains(areaId)
        if wasFav {
            favoriteIds.remove(areaId)
        } else {
            favoriteIds.insert(areaId)
        }
        saveLocal()

        guard let uid = AuthService.shared.userId else { return }
        if wasFav {
            try? await supabase.from("area_favorites")
                .delete()
                .eq("user_id", value: uid)
                .eq("area_id", value: areaId)
                .execute()
        } else {
            struct Row: Encodable { let user_id, area_id: String }
            try? await supabase.from("area_favorites")
                .insert(Row(user_id: uid, area_id: areaId))
                .execute()
            Task { await downloadFavoriteArea(id: areaId) }
        }
    }

    var favoriteAreas: [AreaSummary] {
        favoriteIds.compactMap { id in
            AreaDataService.shared.summaries.first { $0.id == id }
        }
    }

    // MARK: - Download

    private func downloadFavoriteAreas() async {
        for id in favoriteIds {
            await downloadFavoriteArea(id: id)
        }
    }

    private func downloadFavoriteArea(id: String) async {
        let area = await AreaDataService.shared.area(id: id)
        _ = area // cache side effect
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
        let local = readLocal()
        guard !local.isEmpty else { return }
        struct Row: Encodable { let user_id, area_id: String }
        let rows = local.map { Row(user_id: uid, area_id: $0) }
        try? await supabase.from("area_favorites")
            .upsert(rows, onConflict: "user_id,area_id")
            .execute()
    }

    private func pullFromServer(uid: String) async {
        struct Row: Decodable { let area_id: String }
        guard let rows: [Row] = try? await supabase
            .from("area_favorites")
            .select("area_id")
            .eq("user_id", value: uid)
            .execute()
            .value
        else { return }
        favoriteIds = Set(rows.map(\.area_id))
        saveLocal()
    }

    // MARK: - Local persistence

    private func readLocal() -> [String] {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let decoded = try? JSONDecoder().decode([String].self, from: data)
        else { return [] }
        return decoded
    }

    private func saveLocal() {
        guard let data = try? JSONEncoder().encode(Array(favoriteIds)) else { return }
        UserDefaults.standard.set(data, forKey: storageKey)
    }
}
