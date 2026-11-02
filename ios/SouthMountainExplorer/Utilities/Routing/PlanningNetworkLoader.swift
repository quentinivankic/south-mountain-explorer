import Foundation

/// Everything the planner needs from a multi-area load ("Plan a Hike", Task 7).
struct PlanningNetworkInput: Sendable {
    let merged: MergedTrails
    /// Base network (no start spliced in yet).
    let network: TrailNetwork
    let selection: NeighborSelection.Result
    /// Area ids merged into the network, primary first.
    let areaIds: [String]
    /// Chosen neighbors that are not on the device and could not be fetched offline.
    let notDownloaded: [String]
    /// Chosen neighbors whose fetch failed while online.
    let failed: [String]
    /// True when the base network came from the loader's cache.
    let networkWasCached: Bool
}

/// Thin app adapter: picks neighbors, loads their areas (cache first), merges
/// and builds the base network off the main actor. Not called from UI yet.
@MainActor
final class PlanningNetworkLoader {
    private let areas: AreaDataService
    private let isOnline: () -> Bool
    private var cache: [String: (merged: MergedTrails, network: TrailNetwork)] = [:]
    private var cacheOrder: [String] = []
    private static let cacheLimit = 4
    /// Nearby areas inspected before the reach filter.
    private static let nearbyLimit = 40

    init(areas: AreaDataService, isOnline: @escaping () -> Bool = { NetworkService.shared.isConnected }) {
        self.areas = areas
        self.isOnline = isOnline
    }

    func load(
        primary: Area, startLat: Double, startLon: Double,
        budgetSeconds: Double, shape: RoutePlanRequest.Shape, paceFactor: Double = 1.0
    ) async -> PlanningNetworkInput {
        let candidates = areas.nearby(lat: startLat, lon: startLon, limit: Self.nearbyLimit).map { s in
            NeighborSelection.Candidate(
                id: s.id, centerLat: s.centerLat, centerLon: s.centerLon,
                bbox: areas.cachedArea(id: s.id)?.bbox)
        }
        let selection = NeighborSelection.select(
            primaryId: primary.id, startLat: startLat, startLon: startLon,
            budgetSeconds: budgetSeconds, shape: shape, paceFactor: paceFactor,
            candidates: candidates, isHiddenAlias: { areas.isHiddenAlias($0) })

        var loaded: [Area] = [primary]
        var notDownloaded: [String] = []
        var failed: [String] = []
        for id in selection.includedIds {
            if let cached = areas.cachedArea(id: id), !cached.trails.isEmpty {
                loaded.append(cached)
            } else if !isOnline() {
                notDownloaded.append(id)
            } else if let fetched = await areas.area(id: id) {
                loaded.append(fetched)
            } else {
                failed.append(id)
            }
        }

        let key = Self.cacheKey(loaded)
        let sets = loaded.map { AreaTrailSet(areaId: $0.id, trails: $0.rawTrails ?? $0.trails) }
        let canonicalMap = Dictionary(uniqueKeysWithValues: loaded.map { ($0.id, areas.canonicalId(for: $0.id)) })
        let hit = cache[key]
        let built: (merged: MergedTrails, network: TrailNetwork)
        if let hit {
            built = hit
        } else {
            built = await Task.detached(priority: .userInitiated) {
                let merged = MultiAreaTrails.merge(areas: sets, canonical: { canonicalMap[$0] ?? $0 })
                return (merged, TrailNetwork(trails: merged.inputs))
            }.value
            cache[key] = built
            cacheOrder.removeAll { $0 == key }
            cacheOrder.append(key)
            while cacheOrder.count > Self.cacheLimit { cache[cacheOrder.removeFirst()] = nil }
        }
        return PlanningNetworkInput(
            merged: built.merged, network: built.network, selection: selection,
            areaIds: loaded.map(\.id), notDownloaded: notDownloaded, failed: failed,
            networkWasCached: hit != nil)
    }

    /// Primary id (it wins dedupe ties), then sorted area ids with each copy's
    /// version (cachedAt and trail count).
    static func cacheKey(_ areas: [Area]) -> String {
        let versions = areas.map { a -> String in
            let t = a.cachedAt.map { String(Int($0.timeIntervalSince1970)) } ?? "nil"
            return "\(a.id)@\(t)#\((a.rawTrails ?? a.trails).count)"
        }.sorted().joined(separator: "|")
        return "\(areas.first?.id ?? "")>" + versions
    }
}
