import Foundation
import Testing
@testable import SouthMountainExplorer

/// Real Phoenix-area geometry merged with South Mountain ("Plan a Hike", Task 7).
struct MultiAreaSouthMountainTests {
    typealias Key = TrailNetwork.TrailKey
    private struct GeomFile: Decodable {
        let id: String
        let bbox: [Double]?
        let trails: [Trail]
        let parking: [ParkingLot]?
    }
    private static let sm = "south-mountain-park-and-preserve-az"
    private static let neighborIds = [
        "hayden-butte-preserve-az", "echo-canyon-recreation-area-az", "sierra-estrella-wilderness-az",
        "phoenix-mountains-preserve-az", "estrella-mountain-regional-park-az",
        "north-mountain-preserve-az", "shadow-mountain-preserve-az", "south-mountain-preserve-az",
    ]
    private static func geom(_ id: String) throws -> GeomFile {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let url = repo.appendingPathComponent("public/areas/geom/\(id).json")
        return try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
    }
    /// Connected components of the edge graph -> set of area ids per component.
    private static func components(_ net: TrailNetwork) -> [Set<String>] {
        var parent = Array(0..<net.nodes.count)
        func find(_ x: Int) -> Int { var x = x; while parent[x] != x { parent[x] = parent[parent[x]]; x = parent[x] }; return x }
        for e in net.edges { parent[find(e.from)] = find(e.to) }
        var areas: [Int: Set<String>] = [:]
        for e in net.edges where e.kind == .trail {
            for k in e.trailKeys { areas[find(e.from), default: []].insert(k.areaId) }
        }
        return areas.keys.sorted().map { areas[$0]! }
    }
    private static func componentCount(_ net: TrailNetwork, area: String) -> Int {
        components(net).filter { $0.contains(area) }.count
    }

    @Test func mergesNeighborsAndReportsConnectivity() throws {
        let smGeom = try Self.geom(Self.sm)
        var sets = [AreaTrailSet(areaId: Self.sm, trails: smGeom.trails)]
        var loaded: [String] = []
        for id in Self.neighborIds {
            guard let g = try? Self.geom(id) else { continue }
            sets.append(AreaTrailSet(areaId: id, trails: g.trails)); loaded.append(id)
        }
        let alias: [String: String] = ["south-mountain-preserve-az": Self.sm]
        let merged = MultiAreaTrails.merge(areas: sets, canonical: { alias[$0] ?? $0 })
        // The hidden twin is never merged with its canonical.
        #expect(merged.droppedAliasAreas == ["south-mountain-preserve-az"])
        #expect(!merged.inputs.contains { $0.areaId == "south-mountain-preserve-az" })

        let solo = TrailNetwork(trails: smGeom.trails.map { .init(areaId: Self.sm, trail: $0) })
        let net = TrailNetwork(trails: merged.inputs)
        let cross = Self.components(net).filter { $0.count > 1 }
        print("TASK7 loaded neighbors: \(loaded)")
        print("TASK7 trails SM=\(smGeom.trails.count) merged=\(merged.inputs.count) copies=\(merged.copies.count)")
        print("TASK7 SM components solo=\(Self.componentCount(solo, area: Self.sm)) merged=\(Self.componentCount(net, area: Self.sm))")
        print("TASK7 cross-area components: \(cross.map { $0.sorted() })")
        // South Mountain's own pieces never get fewer trails or a different edge set by merging a far area.
        #expect(net.trailKeys.filter { $0.areaId == Self.sm }.count == solo.trailKeys.count)
    }

    @Test func plannerFromBursera4hWithAndWithoutNeighbors() throws {
        let smGeom = try Self.geom(Self.sm)
        let bursera = try #require(smGeom.parking?.first { $0.name == "Bursera Trailhead" })
        var sets = [AreaTrailSet(areaId: Self.sm, trails: smGeom.trails)]
        for id in Self.neighborIds where id != "south-mountain-preserve-az" {
            if let g = try? Self.geom(id) { sets.append(AreaTrailSet(areaId: id, trails: g.trails)) }
        }
        func run(_ s: [AreaTrailSet]) throws -> (trails: Int, minutes: Int, areas: Set<String>, secs: Double) {
            let m = MultiAreaTrails.merge(areas: s)
            let view = try TrailNetworkSnapper(network: TrailNetwork(trails: m.inputs)).insert(start: .parking(bursera))
            let req = PlanOutcomeRequest(network: view, trails: m.inputs, completion: m.completion(.none),
                                         timeLimitSeconds: 4 * 3600)
            let t0 = Date()
            guard case .planned(let best, _) = RoutePlanningService.planOutcome(req) else {
                Issue.record("expected a plan"); throw CancellationError()
            }
            return (best.finished.count, Int((best.metrics.seconds / 60).rounded()),
                    Set(best.finishedTrailKeys.map(\.areaId)), Date().timeIntervalSince(t0))
        }
        let without = try run([sets[0]])
        let with = try run(sets)
        print("TASK7 plan 4h Bursera without=\(without) with=\(with)")
        #expect(without.trails == with.trails)
        #expect(without.minutes == with.minutes)
    }
}
