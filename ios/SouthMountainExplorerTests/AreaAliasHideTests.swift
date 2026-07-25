import Testing
import Foundation
@testable import SouthMountainExplorer

/// Browse/search hide for duplicate + nested areas (docs/adr/0002). The alias
/// map itself is validated in Python (scripts/detect-duplicate-areas.py proves
/// the hide is lossless); here we pin the client filter that consumes it.
struct AreaAliasHideTests {

    private func tuples(_ json: String) throws -> [[JSONValue]] {
        try JSONDecoder().decode([[JSONValue]].self, from: Data(json.utf8))
    }

    @Test func hidesAliasedAreaButKeepsItsCanonical() throws {
        let rows = try tuples("""
        [["south-mountain-preserve-az","South Mountain Preserve","Arizona",33.3,-112.1,77,50.0],
         ["south-mountain-park-and-preserve-az","South Mountain Park and Preserve","Arizona",33.3,-112.1,77,50.0],
         ["papago-park-az","Papago Park","Arizona",33.4,-111.9,12,8.0]]
        """)
        let visible = AreaDataService.visibleSummaries(
            from: rows, hidden: ["south-mountain-preserve-az"]).map(\.id)
        #expect(!visible.contains("south-mountain-preserve-az"),
                "the hidden twin must not appear in Browse")
        #expect(visible.contains("south-mountain-park-and-preserve-az"),
                "the canonical twin must stay")
        #expect(visible.contains("papago-park-az"))
    }

    @Test func stillDropsZeroTrailAreas() throws {
        let rows = try tuples("""
        [["ghost-area-az","Ghost","Arizona",33.0,-112.0,0]]
        """)
        #expect(AreaDataService.visibleSummaries(from: rows, hidden: []).isEmpty)
    }

    @Test func emptyHiddenSetHidesNothing() throws {
        let rows = try tuples("""
        [["a-az","A","Arizona",33.0,-112.0,5],["b-az","B","Arizona",33.1,-112.1,6]]
        """)
        #expect(AreaDataService.visibleSummaries(from: rows, hidden: []).count == 2)
    }
}
