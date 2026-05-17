import Foundation
import Testing
@testable import SouthMountainExplorer

/// Pin the shape of the per-trail GPX export. Strict consumers
/// like Garmin Connect reject malformed XML outright, so the
/// test parses the output with `XMLParser` to validate
/// well-formedness alongside spot-checks for expected fields.
struct GpxExportTests {

    private func makeTrail(
        id: String = "pima-wash-trail",
        name: String = "Pima Wash Trail",
        segments: [[[Double]]]
    ) -> Trail {
        Trail(
            id: id,
            name: name,
            distanceMi: 2.5,
            difficulty: .easy,
            segments: segments
        )
    }

    @Test func gpxIsWellFormedXml() {
        let trail = makeTrail(segments: [
            [[33.3, -112.0], [33.3001, -112.0], [33.3002, -112.0001]]
        ])
        let gpx = GpxExport.gpxString(trail: trail, areaName: "South Mountain Park")
        let parser = XMLParser(data: Data(gpx.utf8))
        #expect(parser.parse(), "GPX output should be well-formed XML")
    }

    @Test func multiSegmentTrailsGetMultipleTrkseg() {
        // OSM-split trails routinely have 2+ disconnected segments
        // under the same way relation. Each should serialize as its
        // own <trkseg> under one <trk>.
        let trail = makeTrail(segments: [
            [[33.30, -112.00], [33.31, -112.00]],
            [[33.32, -112.00], [33.33, -112.00]]
        ])
        let gpx = GpxExport.gpxString(trail: trail, areaName: nil)
        let opens = gpx.components(separatedBy: "<trkseg>").count - 1
        let closes = gpx.components(separatedBy: "</trkseg>").count - 1
        #expect(opens == 2, "expected 2 opens, got \(opens)")
        #expect(closes == 2, "expected 2 closes, got \(closes)")
        // Still one <trk> wrapping them.
        #expect(gpx.components(separatedBy: "<trk>").count - 1 == 1)
    }

    @Test func emptySegmentsAreSkipped() {
        // If OSM gave us an empty inner segment for some reason,
        // don't emit a useless <trkseg></trkseg> — Garmin tolerates
        // it but the file looks broken in side-by-side diffs.
        let trail = makeTrail(segments: [
            [[33.30, -112.00], [33.31, -112.00]],
            [],
            [[33.32, -112.00], [33.33, -112.00]]
        ])
        let gpx = GpxExport.gpxString(trail: trail, areaName: nil)
        #expect(gpx.components(separatedBy: "<trkseg>").count - 1 == 2)
    }

    @Test func trkptsHaveNoTimeOrElevation() {
        // Trail GPX is a static route, not a recording. <time> and
        // <ele> on each point would imply a hike, which Garmin
        // courses don't want and gpx.studio would render as
        // start/end timestamps.
        let trail = makeTrail(segments: [
            [[33.30, -112.00], [33.31, -112.00]]
        ])
        let gpx = GpxExport.gpxString(trail: trail, areaName: nil)
        #expect(!gpx.contains("<time>"))
        #expect(!gpx.contains("<ele>"))
    }

    @Test func areaNameLandsInDesc() {
        // Area context shouldn't bloat the <name> (Garmin Connect's
        // course list looks cleaner with just the trail name), but
        // it's useful metadata to keep around — put it in <desc>.
        let trail = makeTrail(segments: [
            [[33.30, -112.00], [33.31, -112.00]]
        ])
        let gpx = GpxExport.gpxString(trail: trail, areaName: "South Mountain")
        #expect(gpx.contains("<desc>South Mountain</desc>"))
        // Name is just the trail name, not the combo.
        #expect(gpx.contains("<name>Pima Wash Trail</name>"))
    }

    @Test func nilOrEmptyAreaSkipsDesc() {
        let trail = makeTrail(segments: [
            [[33.30, -112.00], [33.31, -112.00]]
        ])
        #expect(!GpxExport.gpxString(trail: trail, areaName: nil).contains("<desc>"))
        #expect(!GpxExport.gpxString(trail: trail, areaName: "").contains("<desc>"))
    }

    @Test func xmlSpecialsAreEscaped() {
        let trail = makeTrail(name: "Foo & <Bar> \"Tricky\"", segments: [
            [[33.30, -112.00], [33.31, -112.00]]
        ])
        let gpx = GpxExport.gpxString(trail: trail, areaName: "A & B")
        #expect(!gpx.contains("Foo & <Bar>"))
        #expect(gpx.contains("Foo &amp; &lt;Bar&gt; &quot;Tricky&quot;"))
        #expect(gpx.contains("A &amp; B"))
    }

    @Test func filenameIsFilesystemSafe() {
        let trail = makeTrail(name: "Pima Wash / Trail", segments: [])
        let name = GpxExport.filename(trail: trail, areaName: "South Mountain Park & Preserve")
        #expect(!name.contains("/"))
        #expect(!name.contains(" "))
        #expect(!name.contains("&"))
        #expect(name.hasPrefix("trekdex-"))
        #expect(name.contains("south-mountain-park"))
        #expect(name.contains("pima-wash-trail"))
    }

    // MARK: - Area-wide export

    private func makeArea(trails: [Trail]) -> Area {
        Area(
            id: "south-mountain-park-and-preserve-az",
            name: "South Mountain Park",
            subtitle: "Arizona",
            centerLat: 33.3,
            centerLon: -112.0,
            zoom: 12,
            bbox: nil,
            trails: trails,
            trailCount: trails.count,
            totalMi: nil,
            cachedAt: nil
        )
    }

    @Test func areaGpxOneTrkPerTrail() {
        let area = makeArea(trails: [
            makeTrail(id: "a", name: "A", segments: [[[33.30, -112.00], [33.31, -112.00]]]),
            makeTrail(id: "b", name: "B", segments: [[[33.32, -112.00], [33.33, -112.00]]]),
            makeTrail(id: "c", name: "C", segments: [[[33.34, -112.00], [33.35, -112.00]]])
        ])
        let gpx = GpxExport.gpxString(area: area)
        let trks = gpx.components(separatedBy: "<trk>").count - 1
        #expect(trks == 3, "expected 3 <trk> entries, got \(trks)")
        // Each trail's name lands inside its own <trk>.
        #expect(gpx.contains("<name>A</name>"))
        #expect(gpx.contains("<name>B</name>"))
        #expect(gpx.contains("<name>C</name>"))
    }

    @Test func areaGpxIsWellFormedXml() {
        let area = makeArea(trails: [
            makeTrail(name: "Pima Wash", segments: [[[33.30, -112.00], [33.31, -112.00]]]),
            makeTrail(id: "loop", name: "Pima Loop", segments: [
                [[33.32, -112.00], [33.33, -112.00]],
                [[33.34, -112.00], [33.35, -112.00]]
            ])
        ])
        let gpx = GpxExport.gpxString(area: area)
        let parser = XMLParser(data: Data(gpx.utf8))
        #expect(parser.parse(), "area GPX should be well-formed XML")
    }

    @Test func areaGpxEmptyTrailsListProducesEmptyGpx() {
        // No trails → valid GPX 1.1 with just a <metadata>. Garmin
        // Connect handles an empty-track file gracefully ("no
        // courses imported") rather than crashing on parse.
        let area = makeArea(trails: [])
        let gpx = GpxExport.gpxString(area: area)
        #expect(XMLParser(data: Data(gpx.utf8)).parse())
        #expect(!gpx.contains("<trk>"))
        #expect(gpx.contains("<name>South Mountain Park</name>"))
    }

    @Test func areaGpxEscapesNamesAndDesc() {
        let area = Area(
            id: "a", name: "A & B", subtitle: "AZ",
            centerLat: 0, centerLon: 0, zoom: 12, bbox: nil,
            trails: [makeTrail(name: "X <Y>", segments: [[[33.30, -112.00], [33.31, -112.00]]])],
            trailCount: 1, totalMi: nil, cachedAt: nil
        )
        let gpx = GpxExport.gpxString(area: area)
        #expect(!gpx.contains("A & B"))
        #expect(!gpx.contains("X <Y>"))
        #expect(gpx.contains("A &amp; B"))
        #expect(gpx.contains("X &lt;Y&gt;"))
    }
}
