import Testing
import Foundation
@testable import SouthMountainExplorer

/// Verifies the trailhead-parking layer decodes from the area geom shape
/// (`AreaRow`) and survives `.toArea()`. The geom is written by
/// `scripts/add-parking.py` as a `parking` array of {lat,lon,name?,fee?,
/// trailhead?}.
struct AreaParkingTests {

    private func decodeRow(_ json: String) throws -> AreaRow {
        try JSONDecoder().decode(AreaRow.self, from: Data(json.utf8))
    }

    @Test func parkingDecodesAndFlowsThroughToArea() throws {
        let json = """
        {
          "id": "abalone-cove-reserve-ca",
          "name": "Abalone Cove Reserve",
          "state": "California",
          "center_lat": 33.7406, "center_lon": -118.3761,
          "zoom": 13, "bbox": [-118.38, 33.73, -118.36, 33.74],
          "trails": [], "trail_count": 0, "total_mi": 0,
          "parking": [
            {"lat": 33.7404, "lon": -118.3732, "name": "Trailhead Lot", "fee": false, "trailhead": true},
            {"lat": 33.7431, "lon": -118.3771}
          ]
        }
        """
        let area = try decodeRow(json).toArea()
        let parking = try #require(area.parking)
        #expect(parking.count == 2)
        #expect(parking[0].name == "Trailhead Lot")
        #expect(parking[0].fee == false)
        #expect(parking[0].trailhead == true)
        // Sparse lot: name/fee/trailhead absent -> nil, coords still present.
        #expect(parking[1].name == nil)
        #expect(parking[1].fee == nil)
        #expect(parking[1].trailhead == nil)
        #expect(abs(parking[1].lat - 33.7431) < 1e-9)
    }

    @Test func absentParkingIsNil() throws {
        let json = """
        {"id": "x", "name": "X", "state": "AZ", "center_lat": 33.3,
         "center_lon": -112.0, "zoom": 13, "trails": []}
        """
        let area = try decodeRow(json).toArea()
        #expect(area.parking == nil)
    }

    @Test func parkingSurvivesDecimationCopy() throws {
        let json = """
        {"id": "x", "name": "X", "state": "AZ", "center_lat": 33.3,
         "center_lon": -112.0, "zoom": 13, "trails": [],
         "parking": [{"lat": 33.3, "lon": -112.0, "name": "Lot"}]}
        """
        let area = try decodeRow(json).toArea().withDecimatedSegments(epsilonMeters: 5)
        #expect(area.parking?.first?.name == "Lot")
    }

    /// Regression: `cacheAreaForRendering` runs every loaded area through
    /// `canonicalizeTrailIds`, which rebuilds the `Area` via its memberwise
    /// init. That rebuild once omitted `parking:`, so a CDN payload with
    /// parking reached the map with `parking == nil` and no pins ever drew.
    /// The whole render transform (canonicalize -> decimate -> attach raw)
    /// must preserve parking.
    @Test func parkingSurvivesCanonicalization() throws {
        let json = """
        {"id": "sm", "name": "South Mountain", "state": "AZ",
         "center_lat": 33.33, "center_lon": -112.07, "zoom": 13,
         "trails": [{"id": "national-trail", "name": "National Trail", "distanceMi": 1.0,
                     "difficulty": "Easy", "segments": [[[33.3,-112.0],[33.31,-112.01]]]}],
         "parking": [{"lat": 33.30, "lon": -112.10, "name": "Bursera Trailhead", "fee": false},
                     {"lat": 33.31, "lon": -112.06}]}
        """
        let area = try decodeRow(json).toArea()
        let canon = AreaDataService.shared.canonicalizeTrailIds(area)
        let parking = try #require(canon.parking, "parking dropped by canonicalization")
        #expect(parking.count == 2)
        #expect(parking.first?.name == "Bursera Trailhead")
        #expect(parking.first?.fee == false)
    }
}
