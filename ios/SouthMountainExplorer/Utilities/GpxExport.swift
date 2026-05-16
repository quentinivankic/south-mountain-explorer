import Foundation

/// Build a GPX 1.1 document for a single trail. Output is meant
/// as a *planning* artifact — load into Garmin Connect as a
/// Course, follow on the watch, get turn-by-turn cues + ETA.
///
/// Track points are just lat/lon (no `<time>`, no `<ele>`) —
/// the source is the trail's OSM polyline, not a recording, so
/// a static route is the right shape. Garmin Connect imports
/// this as a Course cleanly; gpx.studio / Komoot / AllTrails
/// likewise.
///
/// A single trail can have multiple disconnected segments (OSM
/// often splits a long trail at intersections); each becomes its
/// own `<trkseg>` under one `<trk>`. Consumers treat segments as
/// connected waypoints in sequence, which is the correct
/// interpretation for following the trail end-to-end.
enum GpxExport {
    static func gpxString(trail: Trail, areaName: String?) -> String {
        var s = ""
        s += #"<?xml version="1.0" encoding="UTF-8"?>"# + "\n"
        s += #"<gpx version="1.1" creator="TrekDex iOS" xmlns="http://www.topografix.com/GPX/1/1">"# + "\n"
        s += "  <metadata>\n"
        s += "    <name>\(xmlEscape(trackName(trail: trail)))</name>\n"
        s += "  </metadata>\n"
        s += "  <trk>\n"
        s += "    <name>\(xmlEscape(trackName(trail: trail)))</name>\n"
        if let areaName, !areaName.isEmpty {
            s += "    <desc>\(xmlEscape(areaName))</desc>\n"
        }
        for segment in trail.segments where !segment.isEmpty {
            s += "    <trkseg>\n"
            for node in segment where node.count >= 2 {
                s += "      <trkpt lat=\"\(formatCoord(node[0]))\" lon=\"\(formatCoord(node[1]))\"/>\n"
            }
            s += "    </trkseg>\n"
        }
        s += "  </trk>\n"
        s += "</gpx>\n"
        return s
    }

    /// Write the GPX to a fresh file in the system temp directory
    /// and return its URL. Caller uses the URL with `ShareLink` /
    /// `ShareSheet` to surface the iOS share sheet. Files in temp
    /// are auto-pruned by the OS — no manual cleanup needed.
    static func temporaryFile(trail: Trail, areaName: String?) throws -> URL {
        let body = gpxString(trail: trail, areaName: areaName)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(filename(trail: trail, areaName: areaName)).gpx")
        try body.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    /// Human-friendly name embedded in the `<name>` tags. Just the
    /// trail name — area context goes in `<desc>` instead so
    /// Garmin Connect's course list reads cleanly ("Pima Wash
    /// Trail" rather than "South Mountain Park — Pima Wash Trail").
    static func trackName(trail: Trail) -> String {
        trail.name.isEmpty ? "Trail" : trail.name
    }

    /// Filename-safe form: lowercased, hyphenated, alphanumerics
    /// + dash only, length-capped. Area slug suffixed when
    /// available to disambiguate trails with common names (lots
    /// of areas have an "Outer Loop").
    static func filename(trail: Trail, areaName: String?) -> String {
        let trailSlug = slugify(trail.name.isEmpty ? "trail" : trail.name)
        if let areaName, !areaName.isEmpty {
            return "trekdex-\(slugify(areaName))-\(trailSlug)"
        }
        return "trekdex-\(trailSlug)"
    }

    // MARK: - Internal helpers

    /// 6 decimals of lat/lon matches the precision OSM nodes
    /// arrive at. More would be spurious; less degrades the
    /// polyline visibly on a watch face.
    private static func formatCoord(_ d: Double) -> String {
        String(format: "%.6f", d)
    }

    private static func slugify(_ s: String) -> String {
        // Replace every non-alphanumeric run with a single dash so
        // names like "Pima Wash / Trail" become `pima-wash-trail`
        // rather than `pima-wash--trail`. Trim any leading/trailing
        // dash, then length-cap.
        let lowered = s.lowercased()
        let safeSet = CharacterSet.alphanumerics
        let pieces = lowered.unicodeScalars
            .split(whereSeparator: { !safeSet.contains($0) })
            .map(String.init)
        let joined = pieces.joined(separator: "-")
        return String(joined.prefix(50))
    }

    /// XML-escape the five characters that require it in element
    /// text. GPX consumers are strict — Garmin rejects malformed
    /// XML outright.
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
