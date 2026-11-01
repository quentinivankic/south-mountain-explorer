import Foundation
import Testing
@testable import SouthMountainExplorer

private func summary(_ id: String, _ state: String, trails: Int? = 10) -> AreaSummary {
    AreaSummary(
        id: id, name: id, subtitle: state,
        centerLat: 40, centerLon: -100,
        trailCount: trails, totalMi: nil, osmRelationId: nil
    )
}

struct StateCatalogTests {
    @Test func groupsByStateAndSplitsCountries() {
        let scopes = StateCatalog.build(
            summaries: [
                summary("a1", "California"), summary("a2", "California"),
                summary("b1", "Colorado"),
                summary("d1", "District of Columbia"),
                summary("x1", "Denmark"),
            ],
            isHiddenAlias: { _ in false }
        )
        #expect(scopes.map(\.id) == ["California", "Colorado", "Denmark", "District of Columbia"])
        let ca = scopes.first { $0.id == "California" }
        #expect(ca?.areaIDs == ["a1", "a2"])
        #expect(ca?.kind == .usState)
        #expect(scopes.first { $0.id == "Denmark" }?.kind == .country)
        #expect(scopes.first { $0.id == "District of Columbia" }?.kind == .usState)
    }

    @Test func excludesAliasesBlankStatesAndDuplicates() {
        let scopes = StateCatalog.build(
            summaries: [
                summary("keep", "Utah"), summary("alias", "Utah"),
                summary("blank", "  "), summary("keep", "Utah"),
            ],
            isHiddenAlias: { $0 == "alias" }
        )
        #expect(scopes.count == 1)
        #expect(scopes[0].areaIDs == ["keep"])
    }

    @Test func safeIDsRejectTraversalAndEmpty() {
        #expect(StateCatalog.isSafeAreaID("south-mountain-az"))
        #expect(!StateCatalog.isSafeAreaID(""))
        #expect(!StateCatalog.isSafeAreaID("../etc"))
        #expect(!StateCatalog.isSafeAreaID("a/b"))
    }

    @Test func anyNonUSRegionBecomesACountry() {
        let scopes = StateCatalog.build(
            summaries: [summary("c1", "Canada"), summary("d1", "Denmark"), summary("u1", "Utah")],
            isHiddenAlias: { _ in false }
        )
        #expect(scopes.filter { $0.kind == .country }.map(\.id) == ["Canada", "Denmark"])
        #expect(scopes.filter { $0.kind == .usState }.map(\.id) == ["Utah"])
    }
    @Test func bundledIndexGroupsEveryNonUSValueAsACountry() throws {
        let url = try #require(Bundle.main.url(forResource: "areas-index", withExtension: "json"))
        let rows = try JSONDecoder().decode([[JSONValue]].self, from: Data(contentsOf: url))
        let summaries = rows.compactMap { row -> AreaSummary? in
            guard row.count >= 3, case .string(let id) = row[0], case .string(let state) = row[2]
            else { return nil }
            return summary(id, state)
        }
        let scopes = StateCatalog.build(summaries: summaries, isHiddenAlias: { _ in false })
        #expect(scopes.filter { $0.kind == .usState }.count == 51)
        let nonUS = Set(summaries.map(\.subtitle)).subtracting(StateCatalog.usStateNames)
        #expect(Set(scopes.filter { $0.kind == .country }.map(\.id)) == nonUS)
        #expect(scopes.filter { $0.kind == .usState }.allSatisfy { StateCatalog.usStateNames.contains($0.id) })
        #expect(scopes.reduce(0) { $0 + $1.areaIDs.count } == summaries.count)
    }
}

struct StateSizeEstimatorTests {
    private let table = StateSizeTable(
        states: [.init(state: "Utah", areas: 4, trails: 100, rawBytes: 1_000_000, wireBytes: 250_000)],
        global: .init(state: "global", areas: 10, trails: 1000, rawBytes: 10_000_000, wireBytes: 2_000_000)
    )

    private func scope(_ state: String, counts: [String: Int]) -> StateScope {
        StateScope(id: state, kind: .usState, areaIDs: counts.keys.sorted(), trailCounts: counts)
    }

    @Test func trailWeightedEstimateMatchesTableForFullState() {
        let s = scope("Utah", counts: ["a": 40, "b": 30, "c": 20, "d": 10])
        let e = table.estimate(for: s)
        #expect(e.wireBytes == 250_000)
        #expect(e.onDeviceBytes == 1_000_000)
    }

    @Test func excludedAreasLowerTheEstimate() {
        let full = table.estimate(for: scope("Utah", counts: ["a": 40, "b": 30, "c": 20, "d": 10]))
        let visible = table.estimate(for: scope("Utah", counts: ["a": 40, "b": 30, "c": 20]))
        #expect(visible.wireBytes < full.wireBytes)
        #expect(visible.wireBytes == 225_000)
    }

    @Test func missingIDsShrinkTheEstimate() {
        let s = scope("Utah", counts: ["a": 40, "b": 60])
        #expect(table.estimate(for: s, missingIDs: ["a"]).wireBytes == 100_000)
        #expect(table.estimate(for: s, missingIDs: []) == .zero)
    }

    @Test func unknownStateUsesGlobalRateAndMissingTrailCountsUseAverage() {
        let s = StateScope(id: "Denmark", kind: .country, areaIDs: ["x"], trailCounts: [:])
        // global avg trails per area = 100; rate = 2,000,000 / 1000 per trail
        #expect(table.estimate(for: s).wireBytes == 200_000)
    }

    @Test func bundledTableDecodesAndUSRowsSumToMeasuredTotal() throws {
        let bundled = try #require(StateSizeTable.loadBundled())
        let us = bundled.states.filter { StateCatalog.usStateNames.contains($0.state) }
        #expect(us.count == 51)
        #expect(us.reduce(0) { $0 + $1.rawBytes } == 487_320_180)
        #expect(bundled.global.rawBytes == 488_735_296)
        let ca = try #require(bundled.row(for: "California"))
        #expect(ca.areas == 898)
    }
}
