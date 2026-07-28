import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for the global parking pool: the pure merge/endpoint helpers on `Area`,
/// and `ParkingPoolService.parse`.
///
/// The pool exists to stop the pipeline having to decide which area OWNS a lot —
/// the decision behind `_FED_EDGE_BUFFER_M`'s orphan tiebreak, NPS overlook lots
/// landing on a nested wilderness, and the 2,010 parking-blank areas with no
/// boundary that can never be filled at all. Nothing in the app ever asks who
/// owns a lot, so the ownership only ever cost us.
struct ParkingPoolTests {

    private func lot(_ lat: Double, _ lon: Double, name: String? = nil,
                     source: String? = nil, trailhead: Bool? = nil,
                     fee: Bool? = nil) -> ParkingLot {
        ParkingLot(lat: lat, lon: lon, name: name, fee: fee,
                   trailhead: trailhead, source: source)
    }

    private func trail(_ segments: [[[Double]]]) -> Trail {
        Trail(id: "t", name: "T", distanceMi: 1, difficulty: .easy, segments: segments)
    }

    // MARK: - Endpoints

    @Test func endpointsAreBothEndsOfEverySegment() {
        let t = trail([[[33.0, -112.0], [33.1, -112.0]],
                       [[34.0, -111.0], [34.1, -111.0]]])
        let ends = Area.trailEndpoints(t)
        #expect(ends.count == 4)
        #expect(ends.first?.0 == 33.0)
        #expect(ends.last?.0 == 34.1)
    }

    // MARK: - Merge

    /// The pool is built FROM the areas' own lots, so without dedup every pin
    /// would appear twice the moment the pool loads.
    @Test func mergingPoolDoesNotDoubleTheAreasOwnLots() {
        let own = [lot(33.30, -112.05, name: "Trailhead"), lot(33.31, -112.06)]
        let merged = Area.mergingPool(own, own)
        #expect(merged.count == 2)
    }

    /// The point of the whole exercise: a lot no area "owns" still reaches the
    /// hiker. This is the South Fork Trailhead case — 358 m outside Chiricahua
    /// Wilderness, inside Coronado National Forest, dropped today.
    @Test func mergingPoolAddsLotsTheAreaDoesNotHave() {
        let own = [lot(33.30, -112.05)]
        let pooled = [lot(33.30, -112.05), lot(31.8693, -109.1880, name: "South Fork Trailhead")]
        let merged = Area.mergingPool(own, pooled)
        #expect(merged.count == 2)
        #expect(merged.contains { $0.name == "South Fork Trailhead" })
    }

    /// Passing no pooled lots must reproduce today's behaviour exactly — that is
    /// what makes the pool additive, so a failed load costs nothing.
    @Test func mergingPoolWithEmptyPoolIsIdentity() {
        let own = [lot(33.30, -112.05), lot(33.31, -112.06)]
        #expect(Area.mergingPool(own, []).count == own.count)
        #expect(Area.mergingPool(nil, []).isEmpty)
    }

    @Test func mergingPoolHandlesAnAreaWithNoParkingAtAll() {
        // 2,010 areas have no boundary and so can never be edge-filled. The pool
        // is the only way they ever show a lot.
        let pooled = [lot(31.8693, -109.1880, name: "South Fork Trailhead")]
        let merged = Area.mergingPool(nil, pooled)
        #expect(merged.count == 1)
    }

    // MARK: - Parsing

    @Test func parseReadsThePositionalRowsIncludingFee() throws {
        let json = """
        [[33.3,-112.05,"South Fork Trailhead","usfs",1,0],
         [34.0,-111.0,null,null,0,1],
         [35.0,-110.0,null,null,0,null]]
        """.data(using: .utf8)!
        let lots = ParkingPoolService.parse(json)
        #expect(lots.count == 3)
        #expect(lots[0].name == "South Fork Trailhead")
        #expect(lots[0].source == "usfs")
        #expect(lots[0].trailhead == true)
        #expect(lots[0].fee == false)
        #expect(lots[1].fee == true)
        // Absent fee must stay UNKNOWN, not become "free" — the paid/free label is
        // exactly the detail a hiker cares about, so guessing it is worse than
        // omitting it.
        #expect(lots[2].fee == nil)
    }

    /// One bad row must not cost the whole pool.
    @Test func parseSkipsMalformedRowsWithoutFailing() {
        let json = """
        [[33.3,-112.05],["nope","nope"],[34.0],[35.0,-110.0]]
        """.data(using: .utf8)!
        let lots = ParkingPoolService.parse(json)
        #expect(lots.count == 2)
    }

    @Test func parseReturnsEmptyOnGarbageSoCallersKeepPriorData() {
        #expect(ParkingPoolService.parse(Data("not json".utf8)).isEmpty)
    }
}
