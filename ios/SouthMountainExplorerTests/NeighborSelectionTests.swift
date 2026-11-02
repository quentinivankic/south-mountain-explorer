import Foundation
import Testing
@testable import SouthMountainExplorer

/// Which neighboring areas get loaded for a plan ("Plan a Hike", Task 7).
struct NeighborSelectionTests {
    typealias C = NeighborSelection.Candidate
    // 1 degree of latitude is 111,320 m (TurnCueProjector), so 0.01 deg is ~1.1 km.
    private static let lat0 = 33.0
    private static let lon0 = -112.0
    private static func km(_ id: String, north km: Double) -> C {
        C(id: id, centerLat: lat0 + km * 1000 / TurnCueProjector.metersPerDegree, centerLon: lon0)
    }
    private static func select(
        _ cands: [C], hours: Double, shape: RoutePlanRequest.Shape = .loop, pace: Double = 1.0,
        alias: Set<String> = [], cap: Int = 6
    ) -> NeighborSelection.Result {
        NeighborSelection.select(
            primaryId: "p", startLat: lat0, startLon: lon0, budgetSeconds: hours * 3600, shape: shape,
            paceFactor: pace, candidates: cands, isHiddenAlias: { alias.contains($0) }, maxExtra: cap)
    }

    @Test func loopRadiusIsHalfTheBudgetAtSixKmh() {
        // 2 h loop: 1 h out at 6 km/h = 6 km.
        let r = Self.select([Self.km("in", north: 5.9), Self.km("out", north: 6.1)], hours: 2)
        #expect(abs(r.radiusMeters - 6000) < 1e-6)
        #expect(r.includedIds == ["in"])
        #expect(r.decisions.first { $0.id == "out" }?.reason == .outOfReach)
    }

    @Test func oneWayRadiusIsTheWholeBudget() {
        let cands = [Self.km("in", north: 11.9), Self.km("out", north: 12.1)]
        #expect(Self.select(cands, hours: 2, shape: .oneWay).includedIds == ["in"])
        #expect(Self.select(cands, hours: 2, shape: .loop).includedIds.isEmpty)
    }

    @Test func fasterPaceWidensButSlowerPaceDoesNotNarrow() {
        let c = [Self.km("a", north: 8)]
        #expect(Self.select(c, hours: 2, pace: 1.0).includedIds.isEmpty)
        #expect(Self.select(c, hours: 2, pace: 1.5).includedIds == ["a"])
        // A slow pace never shrinks the reach (6 km/h is already the ceiling).
        #expect(Self.select([Self.km("a", north: 5.9)], hours: 2, pace: 0.5).includedIds == ["a"])
    }

    @Test func bboxIsUsedWhenKnownAndCentreOtherwise() {
        // Centre 20 km away, but the bbox reaches within 3 km of the start.
        let lat = Self.lat0 + 0.027
        let boxed = C(id: "boxed", centerLat: Self.lat0 + 0.18, centerLon: Self.lon0,
                      bbox: [Self.lon0 - 0.01, lat, Self.lon0 + 0.01, Self.lat0 + 0.3])
        let centreOnly = C(id: "centre", centerLat: Self.lat0 + 0.18, centerLon: Self.lon0)
        let r = Self.select([boxed, centreOnly], hours: 2)
        #expect(r.includedIds == ["boxed"])
        #expect(r.decisions.first { $0.id == "centre" }?.reason == .outOfReach)
        // Start inside a bbox is distance 0.
        let inside = C(id: "inside", centerLat: Self.lat0 + 1, centerLon: Self.lon0,
                       bbox: [Self.lon0 - 1, Self.lat0 - 1, Self.lon0 + 1, Self.lat0 + 1])
        #expect(NeighborSelection.distance(Self.lat0, Self.lon0, inside) == 0)
    }

    @Test func capKeepsTheNearestAndMarksTheRestOverCap() {
        let cands = (1...9).map { Self.km("n\($0)", north: Double($0) * 0.5) }.reversed()
        let r = Self.select(Array(cands), hours: 4, cap: 6)
        #expect(r.includedIds == ["n1", "n2", "n3", "n4", "n5", "n6"])
        #expect(r.decisions.filter { $0.reason == .overCap }.map(\.id) == ["n7", "n8", "n9"])
        #expect(Self.select(Array(cands), hours: 4, cap: 0).includedIds.isEmpty)
    }

    @Test func hiddenAliasesAndThePrimaryAreSkippedWithReasons() {
        let cands = [Self.km("p", north: 0), Self.km("twin", north: 1), Self.km("ok", north: 2)]
        let r = Self.select(cands, hours: 4, alias: ["twin"])
        #expect(r.includedIds == ["ok"])
        #expect(r.decisions.first { $0.id == "p" }?.reason == .primary)
        #expect(r.decisions.first { $0.id == "twin" }?.reason == .hiddenAlias)
        // An alias does not use up a cap slot.
        #expect(Self.select(cands, hours: 4, alias: ["twin"], cap: 1).includedIds == ["ok"])
    }

    @Test func deterministicWithTiesAndDuplicates() {
        let cands = [Self.km("b", north: 3), Self.km("a", north: 3), Self.km("a", north: 3), Self.km("c", north: 1)]
        let r1 = Self.select(cands, hours: 4)
        let r2 = Self.select(cands.reversed(), hours: 4)
        #expect(r1 == r2)
        #expect(r1.includedIds == ["c", "a", "b"])
    }

    @Test func zeroBudgetIncludesNothing() {
        #expect(Self.select([Self.km("a", north: 0.5)], hours: 0).includedIds.isEmpty)
    }
}
