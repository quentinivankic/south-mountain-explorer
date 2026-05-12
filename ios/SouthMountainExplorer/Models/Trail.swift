import Foundation

enum Difficulty: String, Codable, CaseIterable {
    case easy = "Easy"
    case moderate = "Moderate"
    case hard = "Hard"
}

struct Trail: Codable, Identifiable, Sendable {
    let id: String
    let name: String
    let distanceMi: Double
    let difficulty: Difficulty
    // segments: array of polylines, each polyline is array of [lat, lon]
    let segments: [[[Double]]]

    enum CodingKeys: String, CodingKey {
        case id, name, segments, difficulty
        case distanceMi = "distanceMi"
    }
}

extension String {
    /// Strips a trailing `-<digits>` segment counter from a trail id.
    /// Legacy build-trail-counts.py ids embedded the trail's position
    /// in the alphabetically-sorted build list (e.g.
    /// `unnamed-494466239-29`). Position is unstable across area
    /// rebuilds, so the same physical trail could get different ids
    /// on different days, breaking history dedup + the running
    /// coverage state. This helper makes the id position-independent
    /// at the iOS data boundary so old persisted state lines up with
    /// new CDN payloads.
    ///
    /// Idempotent. Examples:
    /// - `unnamed-494466239-43` → `unnamed-494466239` (position
    ///   counter stripped)
    /// - `alta-0` → `alta` (position counter stripped)
    /// - `unnamed-494466239` → `unnamed-494466239` (9-digit wayid,
    ///   not a position counter — left alone)
    /// - `pima-canyon-loop-trail` → unchanged
    ///
    /// Heuristic for telling "position counter" from "wayid trailing
    /// digits": position counters from `build-trail-counts.py` were
    /// 1-3 digits in practice (no real area exceeds 999 trails),
    /// while OSM way ids embedded in `unnamed-<wayid>` slugs are
    /// always 7+ digits. So we only strip a trailing `-<digits>`
    /// group when the digit count is 1-3. This collapses with new
    /// build-script slug-collision counters too — acceptable since
    /// slug collisions across distinct named trails are
    /// vanishingly rare in our areas.
    var canonicalTrailId: String {
        guard let dashIdx = self.lastIndex(of: "-") else { return self }
        let after = self.index(after: dashIdx)
        guard after < self.endIndex else { return self }
        let tail = self[after...]
        guard !tail.isEmpty, tail.allSatisfy(\.isNumber), tail.count <= 3
        else { return self }
        return String(self[..<dashIdx])
    }
}

struct AreaSummary: Codable, Identifiable, Sendable {
    let id: String
    let name: String
    let subtitle: String   // US state
    let centerLat: Double
    let centerLon: Double
    var trailCount: Int?
    var totalMi: Double?
    /// OSM relation ID baked in by the seed/build pipeline so the iOS
    /// side can query Overpass with the exact same polygon Python used.
    /// Without this, both sides re-run Nominatim and sometimes pick
    /// different relations for the same name (the 48 vs 56 South
    /// Mountain mismatch).
    var osmRelationId: Int?

    var search: String { "\(name) \(subtitle)".lowercased() }
}

// Raw tuple from bundled index.json: [id, name, state, lat, lon]
typealias AreaTuple = [JSONValue]

enum JSONValue: Codable, Sendable {
    case string(String)
    case number(Double)

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let n = try? c.decode(Double.self) { self = .number(n); return }
        throw DecodingError.typeMismatch(JSONValue.self,
            .init(codingPath: decoder.codingPath, debugDescription: "Expected String or Number"))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        }
    }

    var stringValue: String? { if case .string(let s) = self { return s }; return nil }
    var doubleValue: Double? { if case .number(let n) = self { return n }; return nil }
}

extension AreaSummary {
    init?(tuple: AreaTuple) {
        guard tuple.count >= 5,
              let id = tuple[0].stringValue,
              let name = tuple[1].stringValue,
              let state = tuple[2].stringValue,
              let lat = tuple[3].doubleValue,
              let lon = tuple[4].doubleValue
        else { return nil }
        self.id = id
        self.name = name
        self.subtitle = state
        self.centerLat = lat
        self.centerLon = lon
        self.trailCount = tuple.count > 5 ? tuple[5].doubleValue.map { Int($0) } : nil
        self.totalMi = tuple.count > 6 ? tuple[6].doubleValue : nil
        self.osmRelationId = tuple.count > 7 ? tuple[7].doubleValue.map { Int($0) } : nil
    }
}
