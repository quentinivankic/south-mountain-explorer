import Testing
@testable import SouthMountainExplorer

/// Tests for `Area.nearestParkingWithFallback` — the rule that a trail with no
/// lot inside the 805 m gate still gets an answer.
///
/// Measured across 92,360 trails: 42% have a lot within the gate, but 45% show
/// NOTHING despite their area having parking (median nearest lot 2.7 mi). An
/// empty map is indistinguishable from "this place has no parking", which is the
/// "found a good hike, no idea where to park, never went" failure.
struct ParkingFallbackTests {

    private func trail(at lat: Double, _ lon: Double) -> Trail {
        Trail(id: "t", name: "T", distanceMi: 1, difficulty: .easy,
              segments: [[[lat, lon], [lat + 0.001, lon + 0.001]]])
    }

    private func area(lots: [ParkingLot]?) -> Area {
        Area(id: "a", name: "A", subtitle: "S", centerLat: 47, centerLon: -112,
             zoom: 13, bbox: nil, trails: [trail(at: 47.0, -112.0)],
             trailCount: 1, totalMi: 1, cachedAt: nil, parking: lots)
    }

    @Test func nearLotIsMarkedNear() {
        let lot = ParkingLot(lat: 47.001, lon: -112.0, name: "TH", fee: nil, trailhead: nil, source: "osm")
        let out = area(lots: [lot]).nearestParkingWithFallback(for: trail(at: 47.0, -112.0))
        #expect(out.count == 1)
        #expect(out[0].isNear, "a lot ~110 m away is inside the 805 m gate")
    }

    /// The behaviour change: previously this returned nothing at all.
    @Test func farLotIsStillOfferedButMarkedFar() {
        let lot = ParkingLot(lat: 47.05, lon: -112.0, name: "Far TH", fee: nil, trailhead: nil, source: "osm")
        let out = area(lots: [lot]).nearestParkingWithFallback(for: trail(at: 47.0, -112.0))
        #expect(out.count == 1, "must still answer rather than drawing an empty map")
        #expect(!out[0].isNear, "a lot ~5.5 km away must be flagged as far, not passed off as a trailhead")
        #expect(out[0].meters > 805)
    }

    @Test func noParkingInAreaReturnsNothing() {
        #expect(area(lots: nil).nearestParkingWithFallback(for: trail(at: 47.0, -112.0)).isEmpty)
        #expect(area(lots: []).nearestParkingWithFallback(for: trail(at: 47.0, -112.0)).isEmpty,
                "12% of trails are in areas with no parking data — invent nothing")
    }

    /// Near lots win outright; a far one must not pad the list beside them.
    @Test func nearLotsAreNotDilutedByFarOnes() {
        let near = ParkingLot(lat: 47.001, lon: -112.0, name: "Near", fee: nil, trailhead: nil, source: "osm")
        let far = ParkingLot(lat: 47.05, lon: -112.0, name: "Far", fee: nil, trailhead: nil, source: "osm")
        let out = area(lots: [far, near]).nearestParkingWithFallback(for: trail(at: 47.0, -112.0))
        #expect(out.count == 1)
        #expect(out[0].isNear)
    }

    /// The fallback offers fewer options than the near case — these are a place
    /// to drive to, not a set of choices at the trailhead.
    @Test func fallbackIsCappedAtTwo() {
        let lots = (1...5).map {
            ParkingLot(lat: 47.0 + Double($0) * 0.02, lon: -112.0,
                       name: "L\($0)", fee: nil, trailhead: nil, source: "osm")
        }
        let out = area(lots: lots).nearestParkingWithFallback(for: trail(at: 47.0, -112.0))
        #expect(out.count == 2, "far fallback caps at 2, got \(out.count)")
        #expect(out.allSatisfy { !$0.isNear })
    }

    /// Closest first, so the label and the pin agree about which to drive to.
    @Test func fallbackIsSortedNearestFirst() {
        let a = ParkingLot(lat: 47.06, lon: -112.0, name: "A", fee: nil, trailhead: nil, source: "osm")
        let b = ParkingLot(lat: 47.03, lon: -112.0, name: "B", fee: nil, trailhead: nil, source: "osm")
        let out = area(lots: [a, b]).nearestParkingWithFallback(for: trail(at: 47.0, -112.0))
        #expect(out[0].lot.name == "B", "nearest must come first")
        #expect(out[0].meters < out[1].meters)
    }
}
