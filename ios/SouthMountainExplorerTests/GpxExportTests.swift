import Foundation
import Testing
@testable import SouthMountainExplorer

/// Pin the shape of the GPX export. Strict consumers like Garmin
/// Connect reject malformed XML outright, so the test parses the
/// output with `XMLParser` to validate well-formedness in addition
/// to spot-checking that the expected fields are present.
struct GpxExportTests {

    private func makeHike(path: [GpsPoint]) -> SavedRecording {
        SavedRecording(
            id: "test-hike",
            areaId: "south-mountain-park-and-preserve-az",
            startedAt: Date(timeIntervalSince1970: 1_710_000_000),
            endedAt: Date(timeIntervalSince1970: 1_710_003_600),
            distanceMi: 2.5,
            durationSeconds: 3600,
            completedTrailIds: [],
            path: path,
            trailId: nil,
            revisitedTrailIds: []
        )
    }

    @Test func gpxIsWellFormedXml() throws {
        let hike = makeHike(path: [
            [33.3, -112.0, 1_710_000_000_000, 420.5],
            [33.3001, -112.0, 1_710_000_002_000, 421.2],
            [33.3002, -112.0001, 1_710_000_004_000, 422.0],
        ])
        let gpx = GpxExport.gpxString(hike: hike, areaName: "South Mountain Park")
        let parser = XMLParser(data: Data(gpx.utf8))
        // No custom delegate; XMLParser.parse() returns false on
        // malformed input, true on well-formed (regardless of
        // schema).
        #expect(parser.parse(), "GPX output should be well-formed XML")
    }

    @Test func gpxIncludesElevationWhenAvailable() {
        let hike = makeHike(path: [
            [33.3, -112.0, 1_710_000_000_000, 420.5],
            [33.3001, -112.0, 1_710_000_002_000, 421.2],
        ])
        let gpx = GpxExport.gpxString(hike: hike, areaName: "Test")
        #expect(gpx.contains("<ele>420.5</ele>"))
        #expect(gpx.contains("<ele>421.2</ele>"))
    }

    @Test func gpxOmitsElevationFor3ElementPoints() {
        // Pre-elevation-feature hikes had 3-element GpsPoint values.
        // Those should still serialize, just without <ele> tags.
        let hike = makeHike(path: [
            [33.3, -112.0, 1_710_000_000_000],
            [33.3001, -112.0, 1_710_000_002_000],
        ])
        let gpx = GpxExport.gpxString(hike: hike, areaName: "Test")
        #expect(!gpx.contains("<ele>"),
                "3-element points should produce no <ele> tags")
        // Other required fields still present.
        #expect(gpx.contains("<trkpt lat=\"33.300000\" lon=\"-112.000000\">"))
    }

    @Test func xmlSpecialsAreEscaped() {
        let hike = makeHike(path: [[33.3, -112.0, 1_710_000_000_000]])
        let gpx = GpxExport.gpxString(hike: hike, areaName: "Foo & <Bar>")
        // The unescaped substring must not appear; the escaped form
        // must.
        #expect(!gpx.contains("Foo & <Bar>"))
        #expect(gpx.contains("Foo &amp; &lt;Bar&gt;"))
    }

    @Test func filenameIsFilesystemSafe() {
        let hike = makeHike(path: [])
        let name = GpxExport.filename(hike: hike, areaName: "South Mountain / Park & Preserve")
        // No slashes, no spaces, no special chars.
        #expect(!name.contains("/"))
        #expect(!name.contains(" "))
        #expect(!name.contains("&"))
        #expect(name.hasPrefix("trekdex-"))
    }
}
