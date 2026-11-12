import Testing
import Foundation
@testable import SouthMountainExplorer

/// The area screen's miles prefer the geom's `distinct_mi` (shared ground
/// counted once, `trailforge/serve/distinct_ground.py`) and fall back to
/// `total_mi` when it is absent, which is every geom published before it.
struct AreaDistinctMilesTests {

    private func decodeRow(_ json: String) throws -> AreaRow {
        try JSONDecoder().decode(AreaRow.self, from: Data(json.utf8))
    }

    private func geom(distinct: String?) -> String {
        """
        {
          "id": "test-area-az", "name": "Test Area", "state": "Arizona",
          "center_lat": 33.3, "center_lon": -112.0, "zoom": 13, "bbox": null,
          "trails": [
            {"id": "loop", "name": "Loop", "distanceMi": 1.38, "difficulty": "Easy",
             "segments": [[[40.0, -105.0], [40.01, -105.0], [40.02, -105.0]]]},
            {"id": "spur", "name": "Spur", "distanceMi": 0.69, "difficulty": "Easy",
             "segments": [[[40.0, -105.0], [40.01, -105.0]]]}
          ],
          "trail_count": 2, "total_mi": 2.1\(distinct.map { ", \"distinct_mi\": \($0)" } ?? "")
        }
        """
    }

    @Test func withoutTheFieldTheAreaShowsTotalMiAsBefore() throws {
        let area = try decodeRow(geom(distinct: nil)).toArea()
        #expect(area.distinctMi == nil)
        #expect(area.totalMi == 2.1)
        #expect(area.resolvedTotalMi == 2.1)
    }

    @Test func withTheFieldTheAreaShowsDistinctGround() throws {
        let area = try decodeRow(geom(distinct: "1.4")).toArea()
        #expect(area.distinctMi == 1.4)
        #expect(area.totalMi == 2.1)
        #expect(area.resolvedTotalMi == 1.4)
        #expect(area.resolvedTrailCount == 2)
    }

    @Test func noTotalAndNoDistinctStillSumsTheTrails() {
        let trail = Trail(id: "a", name: "A", distanceMi: 1.25, difficulty: .easy, segments: [])
        let area = Area(id: "x", name: "X", subtitle: "AZ", centerLat: 0, centerLon: 0,
                        zoom: 13, bbox: nil, trails: [trail, trail], trailCount: nil,
                        totalMi: nil, cachedAt: nil)
        #expect(area.resolvedTotalMi == 2.5)
    }

    @Test func distinctMiSurvivesTheLoadPathAndTheDiskCache() throws {
        let decoded = try decodeRow(geom(distinct: "1.4")).toArea()
        let canon = AreaDataService.canonicalizeTrailIds(decoded)
        let decimated = canon.withDecimatedSegments(epsilonMeters: 5.0)
        let loaded = decimated.with(rawTrails: canon.trails)
        #expect(canon.distinctMi == 1.4)
        #expect(decimated.distinctMi == 1.4)
        #expect(loaded.distinctMi == 1.4)
        #expect(loaded.resolvedTotalMi == 1.4)

        let reread = try JSONDecoder().decode(Area.self, from: JSONEncoder().encode(loaded))
        #expect(reread.distinctMi == 1.4)
        #expect(reread.resolvedTotalMi == 1.4)

        // A cache written before the field existed decodes with no distinctMi.
        let old = try decodeRow(geom(distinct: nil)).toArea()
        let oldData = try JSONEncoder().encode(old)
        #expect(!String(decoding: oldData, as: UTF8.self).contains("distinct_mi"))
        #expect(try JSONDecoder().decode(Area.self, from: oldData).resolvedTotalMi == 2.1)
    }

    /// Today's published data has no `distinct_mi`, so the real South Mountain
    /// geom must show exactly what it showed before this change: 122.6 mi.
    @Test func todaysSouthMountainGeomShowsTheSameNumber() throws {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        let data = try Data(contentsOf: url)
        #expect(!String(decoding: data, as: UTF8.self).contains("distinct_mi"))
        let area = AreaDataService.canonicalizeTrailIds(
            try JSONDecoder().decode(AreaRow.self, from: data).toArea())
        let before = area.totalMi ?? area.trails.reduce(0) { $0 + $1.distanceMi }
        #expect(area.distinctMi == nil)
        #expect(area.resolvedTotalMi == before)
        #expect(area.resolvedTotalMi == 122.6)
        #expect(UnitFormatter.distance(miles: area.resolvedTotalMi, units: .imperial)
                == UnitFormatter.distance(miles: before, units: .imperial))
        #expect(UnitFormatter.distance(miles: area.resolvedTotalMi, units: .metric)
                == UnitFormatter.distance(miles: before, units: .metric))
    }
}
