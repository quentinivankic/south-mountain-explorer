import Foundation

@MainActor
@Observable
final class AreaSilhouetteService {
    static let shared = AreaSilhouetteService()

    private var byId: [String: AreaSilhouette] = [:]

    private init() {
        load()
    }

    func silhouette(for areaId: String) -> AreaSilhouette? {
        byId[areaId]
    }

    private func load() {
        guard let url = Bundle.main.url(forResource: "silhouettes", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let decoded = try? JSONDecoder().decode([String: AreaSilhouette].self, from: data)
        else { return }
        byId = decoded
    }
}
