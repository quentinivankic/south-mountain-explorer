import Foundation

private let storageKey = "summit:favorites"

@MainActor
@Observable
final class FavoritesService {
    static let shared = FavoritesService()

    private(set) var favoriteIds: Set<String> = []

    private init() {
        favoriteIds = Set(readLocal())
        Task { await downloadFavoriteAreas() }
    }

    func isFavorite(_ areaId: String) -> Bool {
        favoriteIds.contains(areaId)
    }

    func toggle(areaId: String) async {
        if favoriteIds.contains(areaId) {
            favoriteIds.remove(areaId)
        } else {
            favoriteIds.insert(areaId)
            Task { await downloadFavoriteArea(id: areaId) }
        }
        saveLocal()
    }

    var favoriteAreas: [AreaSummary] {
        favoriteIds.compactMap { id in
            AreaDataService.shared.summaries.first { $0.id == id }
        }
    }

    // MARK: - Download full area data when favorited

    private func downloadFavoriteAreas() async {
        for id in favoriteIds { await downloadFavoriteArea(id: id) }
    }

    private func downloadFavoriteArea(id: String) async {
        _ = await AreaDataService.shared.area(id: id)
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
