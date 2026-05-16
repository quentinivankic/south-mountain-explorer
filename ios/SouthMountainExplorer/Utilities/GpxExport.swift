import Foundation

/// Build a GPX 1.1 document for a single saved hike. Imports
/// cleanly into Garmin Connect (as a Course), gpx.studio,
/// Strava, Komoot, AllTrails, etc.
///
/// Track points include `<ele>` when the GPS sample had altitude
/// (build-17 PR A onwards), `<time>` from the sample's epoch-ms
/// timestamp, and the lat/lon at the precision the recorder
/// stored them. Pre-elevation hikes simply omit `<ele>`.
enum GpxExport {
    static func gpxString(hike: SavedRecording, areaName: String?) -> String {
        var s = ""
        s += #"<?xml version="1.0" encoding="UTF-8"?>"# + "\n"
        s += #"<gpx version="1.1" creator="TrekDex iOS" xmlns="http://www.topografix.com/GPX/1/1">"# + "\n"
        s += "  <metadata>\n"
        s += "    <name>\(xmlEscape(trackName(hike: hike, areaName: areaName)))</name>\n"
        s += "    <time>\(iso8601(hike.startedAt))</time>\n"
        s += "  </metadata>\n"
        s += "  <trk>\n"
        s += "    <name>\(xmlEscape(trackName(hike: hike, areaName: areaName)))</name>\n"
        s += "    <trkseg>\n"
        for p in hike.path where p.count >= 3 {
            let lat = p[0]
            let lon = p[1]
            let tsMs = p[2]
            let date = Date(timeIntervalSince1970: tsMs / 1000)
            s += "      <trkpt lat=\"\(formatCoord(lat))\" lon=\"\(formatCoord(lon))\">\n"
            // GpsPoint is 3-element [lat, lon, ts] for pre-elevation
            // recordings, 4-element [lat, lon, ts, altitudeMeters]
            // after build-17 PR A. PR A's `Array.altitudeMeters`
            // extension would be the natural reader, but this PR
            // branched off main before that extension landed —
            // inline the check directly so the two PRs don't depend
            // on each other's merge order.
            if p.count >= 4 {
                s += "        <ele>\(formatElevation(p[3]))</ele>\n"
            }
            s += "        <time>\(iso8601(date))</time>\n"
            s += "      </trkpt>\n"
        }
        s += "    </trkseg>\n"
        s += "  </trk>\n"
        s += "</gpx>\n"
        return s
    }

    /// Write the GPX to a fresh file in the system temp directory
    /// and return its URL. Caller uses the URL with `ShareLink` /
    /// `ShareSheet` to surface the iOS share sheet. Files in temp
    /// are auto-pruned by the OS — no manual cleanup needed.
    static func temporaryFile(hike: SavedRecording, areaName: String?) throws -> URL {
        let body = gpxString(hike: hike, areaName: areaName)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(filename(hike: hike, areaName: areaName)).gpx")
        try body.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    /// Human-friendly trail name embedded in the `<name>` tags.
    /// "Pima Wash Trail — May 15, 2026" when a trail name is
    /// available, else "South Mountain — May 15, 2026". Spaces +
    /// punctuation are XML-escaped by the caller.
    static func trackName(hike: SavedRecording, areaName: String?) -> String {
        let date = DateFormatter()
        date.dateStyle = .medium
        date.timeStyle = .none
        let area = areaName ?? "Hike"
        return "\(area) — \(date.string(from: hike.startedAt))"
    }

    /// Filename-safe form (no spaces, no punctuation that confuses
    /// share-sheet handlers). Length-capped so the resulting URL
    /// isn't unwieldy on devices with long area names.
    static func filename(hike: SavedRecording, areaName: String?) -> String {
        let raw = (areaName ?? "Hike")
            .lowercased()
            .replacingOccurrences(of: " ", with: "-")
            .components(separatedBy: CharacterSet.alphanumerics.union(.init(charactersIn: "-")).inverted)
            .joined()
        let safe = String(raw.prefix(40))
        let stamp = ISO8601DateFormatter().string(from: hike.startedAt).prefix(10) // YYYY-MM-DD
        return "trekdex-\(safe)-\(stamp)"
    }

    // MARK: - Internal helpers

    /// 6 decimals of lat/lon is the precision the recorder
    /// already rounds to (`appendPoint` formats via `%.6f`).
    /// Matching it here avoids the GPX output looking spuriously
    /// more precise than the source data.
    private static func formatCoord(_ d: Double) -> String {
        String(format: "%.6f", d)
    }

    /// Elevation precision: one decimal place. GPS altitude
    /// rarely justifies more.
    private static func formatElevation(_ d: Double) -> String {
        String(format: "%.1f", d)
    }

    private static let iso8601Formatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static func iso8601(_ date: Date) -> String {
        iso8601Formatter.string(from: date)
    }

    /// XML-escape the five characters that require it in element
    /// text + attribute values. GPX consumers are strict — Garmin
    /// rejects malformed XML outright.
    private static func xmlEscape(_ s: String) -> String {
        var out = s
        out = out.replacingOccurrences(of: "&", with: "&amp;")
        out = out.replacingOccurrences(of: "<", with: "&lt;")
        out = out.replacingOccurrences(of: ">", with: "&gt;")
        out = out.replacingOccurrences(of: "\"", with: "&quot;")
        out = out.replacingOccurrences(of: "'", with: "&apos;")
        return out
    }
}
