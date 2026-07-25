import Foundation
import CryptoKit

enum Difficulty: String, Codable, CaseIterable {
    case easy = "Easy"
    case moderate = "Moderate"
    case hard = "Hard"
}

struct Trail: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let name: String
    let distanceMi: Double
    let difficulty: Difficulty
    // segments: array of polylines, each polyline is array of [lat, lon]
    let segments: [[[Double]]]
    /// Elevation gain in FEET, baked into the geom by the trailforge DEM pass
    /// (serve/elevation.py / add-elevation.py). Optional: nil for areas not
    /// yet run through the elevation post-process, and for the live-Overpass
    /// fallback path. `= nil` default keeps the memberwise init backward-
    /// compatible for existing call sites/tests; Codable still decodes it via
    /// decodeIfPresent, so old data (no gainFt key) stays valid.
    var gainFt: Int? = nil
    /// Elevation samples in FEET, evenly spaced BY DISTANCE along `segments`,
    /// baked by the trailforge DEM pass (`serve/elevation.py`). Even spacing is
    /// what lets `TrailProfile` map a position to an index arithmetically
    /// instead of shipping a parallel distance array in every geom file.
    ///
    /// DIRECTION IS MEANINGLESS: the series follows arbitrary OSM way order, so
    /// index 0 is NOT the trailhead. Read it through `TrailProfile`, which
    /// anchors on the hiker's snapped position and orients by travel direction.
    ///
    /// Optional for the same reason as `gainFt` — areas published before the
    /// profile pass, and the live-Overpass fallback, simply won't have it.
    var profileFt: [Int]? = nil
    /// Where `profileFt` is DISCONTINUOUS, as `[[sampleIndex, gapMetres], ...]`.
    ///
    /// Some trails are several stretches that share a name but do not join —
    /// Dearborn River is two pieces 7.4 mi apart inside one national forest.
    /// Drawing the series as one line implies you can walk straight through, so
    /// the chart breaks at these indices instead.
    ///
    /// MARKED, NOT SPACED: `distanceMi` excludes inter-segment gaps and the
    /// x-axis is labelled from it, so a gap has ZERO width on the chart. The
    /// index is the sample boundary the break falls on; the metres are carried
    /// only so the break can be labelled with a real distance.
    ///
    /// Baked by `serve/elevation.py` (>=805 m / 0.5 mi only) and present ONLY on
    /// trails that actually break, so nothing changes for the common case. Older
    /// geom simply won't have it.
    var profileGaps: [[Int]]? = nil

    enum CodingKeys: String, CodingKey {
        case id, name, segments, difficulty, gainFt, profileFt, profileGaps
        case distanceMi = "distanceMi"
    }

    /// A stable identity for the PHYSICAL trail, derived from its geometry.
    ///
    /// Completion is stored per (areaId, trailId), but the same place is often
    /// seeded twice under name variants — measured 2026-07-25: 903 areas (10%)
    /// have an identical-trail-set twin (`south-mountain-preserve` vs
    /// `-park-and-preserve`, `haleakalā` vs `haleakal`, accents/typos). Progress
    /// recorded under one twin was invisible under the other. This lets
    /// ProgressService credit completion across areas by the TRAIL, not the
    /// container.
    ///
    /// Keyed on GEOMETRY, not id, on purpose: 5,205 trail ids collide across
    /// UNRELATED areas (`ice-age-trail` spans 47 areas, a different segment in
    /// each), so id-equality would falsely credit one segment's walk to all 47.
    /// Identical geometry is the only safe "same trail" signal.
    ///
    /// SHA256 of coordinates rounded to 5 dp (~1 m) so float noise never splits
    /// a match; hex-truncated. Deterministic across launches (unlike
    /// `Hasher`, which is per-process seeded) because it is persisted.
    var completionFingerprint: String {
        var canon = ""
        for seg in segments {
            for p in seg where p.count >= 2 {
                canon += String(format: "%.5f,%.5f;", p[0], p[1])
            }
            canon += "|"
        }
        return Self.sha256Hex(canon)
    }

    private static func sha256Hex(_ s: String) -> String {
        let digest = SHA256.hash(data: Data(s.utf8))
        return digest.prefix(16).map { String(format: "%02x", $0) }.joined()
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
    case null

    /// `null` must be checked BEFORE attempting String/Double decode —
    /// without this case, any null anywhere in the index array (an area
    /// row with no osm_relation_id, padded to 8 elements with a trailing
    /// null by the publish pipeline) throws typeMismatch, which fails the
    /// WHOLE-ARRAY decode of `[[JSONValue]]`, not just that one row. Found
    /// via a real report: Otter Creek State Forest never appeared in the
    /// app despite the CDN correctly serving it — revalidate() decodes as
    /// a validation step before persisting, so this silently failed for
    /// EVERY user on EVERY refresh attempt the moment any way-sourced area
    /// (osm_relation_id = null by design, see seed-areas.py) got published.
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let n = try? c.decode(Double.self) { self = .number(n); return }
        throw DecodingError.typeMismatch(JSONValue.self,
            .init(codingPath: decoder.codingPath, debugDescription: "Expected String, Number, or null"))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .null: try c.encodeNil()
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
