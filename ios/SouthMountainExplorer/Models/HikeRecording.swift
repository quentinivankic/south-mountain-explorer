import Foundation

// [lat, lon, timestamp(ms)] or [lat, lon, timestamp(ms), altitudeMeters]
// when the recorder had a vertical-accuracy-valid altitude from the
// GPS at sample time. Existing pre-elevation-feature records persist
// as the 3-element form and decode/serialize transparently; the
// optional 4th element is read via `point.altitudeMeters` below.
typealias GpsPoint = [Double]

extension Array where Element == Double {
    /// Altitude in meters at this GPS sample, when available. Records
    /// older than the elevation-capture feature were saved as 3-element
    /// `[lat, lon, ts]`; they return `nil`. New records are 4-element
    /// `[lat, lon, ts, altitudeMeters]`. Read sites that need
    /// elevation should gracefully skip nil samples.
    var altitudeMeters: Double? { count >= 4 ? self[3] : nil }
}

/// In-memory view-layer pairing of a recorded hike's path with its
/// **start** timestamp. `AreaView` holds an array of these and
/// threads them through to `TrailMapView`, which uses them for the
/// cyan past-hike halo (path only) and the orange
/// walked-since-completion overlay (path + startedAt filter).
///
/// `startedAt` rather than `endedAt` because the post-completion
/// filter (`hike.startedAt > completionDate`) needs to **exclude
/// the hike that itself caused the completion** — a single-session
/// completion lives at `startedAt < completionDate < endedAt`, so
/// gating on `startedAt` correctly drops the completion hike and
/// includes only legitimate later re-walks.
struct PastHike: Sendable {
    let path: [GpsPoint]
    let startedAt: Date
}

enum RecordingMode: String, Codable, Sendable {
    case roam
    case trail
    /// Area-less "start anywhere" recording: the map shows every trail
    /// from the ~12 nearest areas, and at stop time coverage/completions
    /// are credited to EVERY area the path touched. Persisted only in
    /// `ActiveRecording` (transient); `SavedRecording` never stores a
    /// mode — a saved walk is identified by `multiAreaCompletions !=
    /// nil`, because an old build decoding an unknown enum raw value
    /// would fail the whole history array (see SavedRecording docs).
    case walk
}

struct ActiveRecording: Codable, Sendable {
    let areaId: String
    let mode: RecordingMode
    let trailId: String?
    let startedAt: Date
    var path: [GpsPoint]
    var distanceMi: Double
    /// Trail IDs that were ALREADY at completion coverage when this
    /// recording started — captured by `RecordingService.startRecording`
    /// so the end-of-hike classification can distinguish "newly
    /// completed by this session" from "re-walked while already
    /// complete." Without this snapshot, intra-session
    /// `applyLiveCoverage` writes flip the trail to "complete" in
    /// CoverageService mid-hike, and `stopRecording` then misreads it
    /// as "previously complete" — surfacing a first-time-walked trail
    /// in History under "previously completed."
    /// Optional for backward-compat with recordings persisted before
    /// this field existed (nil → treat as empty set).
    let priorCompleteTrailIds: Set<String>?
    /// Walk mode only: the areas whose trails the walk can credit —
    /// the ~12 nearest areas captured when the walk started. `nil` for
    /// roam/trail recordings. `var … = nil` so the synthesized
    /// memberwise init keeps its defaults and existing construction
    /// sites compile unchanged.
    var nearbyAreaIds: [String]? = nil
    /// Walk mode only: per-area snapshot of trails already at the
    /// completion threshold when the walk started — the multi-area
    /// analogue of `priorCompleteTrailIds`, consumed by `stopWalk`'s
    /// per-area newly-completed vs revisited classification.
    var priorCompleteByArea: [String: Set<String>]? = nil
}

struct FinishedRecording: Sendable {
    let areaId: String
    let mode: RecordingMode
    let trailId: String?
    let startedAt: Date
    let endedAt: Date
    let durationSeconds: Int
    let path: [GpsPoint]
    let distanceMi: Double
    /// Trails that crossed the completion threshold for the first time on
    /// this hike.
    let newlyCompletedTrailIds: [String]
    /// Trails that were already complete before this hike but were walked
    /// to ≥ the completion threshold again on this hike. Lets History
    /// show "X previously completed" instead of swallowing the visit.
    let revisitedTrailIds: [String]
    let coverageDelta: [String: Double]
    /// Walk mode: newly-completed trails per area, across every area the
    /// walk touched (INCLUDING the primary — a uniform dict for walk-
    /// aware consumers; the flat arrays above stay primary-area-only for
    /// legacy ones). `nil` for roam/trail recordings. `var … = nil`
    /// keeps the memberwise init's existing call sites compiling.
    var multiAreaCompletions: [String: [String]]? = nil
    /// Walk mode: revisited trails per area, same shape as
    /// `multiAreaCompletions`. `nil` for roam/trail recordings.
    var multiAreaRevisited: [String: [String]]? = nil
}

struct SavedRecording: Codable, Identifiable, Sendable {
    let id: String
    let areaId: String
    let startedAt: Date
    let endedAt: Date
    let distanceMi: Double
    let durationSeconds: Int
    let completedTrailIds: [String]
    let path: [GpsPoint]
    /// Set when the user started the hike via "Record This Trail" on a
    /// trail row. Optional + decoded with a default so old persisted
    /// records (which don't have this key) still parse.
    let trailId: String?
    /// Trails walked again on this hike that were already complete before
    /// it. Empty for old persisted records.
    let revisitedTrailIds: [String]
    /// Present (possibly empty) iff this record is a WALK — an area-less
    /// recording whose completions credit every area the path touched.
    /// areaId → newly-completed trailIds, INCLUDING the primary area
    /// (`areaId` above), so walk-aware consumers read one uniform dict;
    /// `completedTrailIds` stays primary-area-only for legacy consumers.
    ///
    /// Back-compat notes (why this is a dict field and NOT a persisted
    /// mode enum): old builds ignore unknown JSON keys, so they read a
    /// walk as a plain hike — but an unknown RecordingMode raw value
    /// would throw, and `loadHistorySync` decodes the WHOLE history
    /// array with `try?`, so one bad record would blank History and the
    /// next save would truncate hike-history.json. Also: an old build
    /// that rewrites the history file drops this key from every record
    /// (Codable re-encode) — accepted, matches every optional field here.
    let multiAreaCompletions: [String: [String]]?
    /// Walk mode: revisited trails per area, same shape as
    /// `multiAreaCompletions`. `nil` for non-walk records.
    let multiAreaRevisited: [String: [String]]?

    /// True when this record was captured by the area-less walk mode.
    var isWalk: Bool { multiAreaCompletions != nil }

    enum CodingKeys: String, CodingKey {
        case id, areaId, startedAt, endedAt, distanceMi, durationSeconds, completedTrailIds, path, trailId, revisitedTrailIds, multiAreaCompletions, multiAreaRevisited
    }

    init(
        id: String,
        areaId: String,
        startedAt: Date,
        endedAt: Date,
        distanceMi: Double,
        durationSeconds: Int,
        completedTrailIds: [String],
        path: [GpsPoint],
        trailId: String? = nil,
        revisitedTrailIds: [String] = [],
        multiAreaCompletions: [String: [String]]? = nil,
        multiAreaRevisited: [String: [String]]? = nil
    ) {
        self.id = id
        self.areaId = areaId
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.distanceMi = distanceMi
        self.durationSeconds = durationSeconds
        self.completedTrailIds = completedTrailIds
        self.path = path
        self.trailId = trailId
        self.revisitedTrailIds = revisitedTrailIds
        self.multiAreaCompletions = multiAreaCompletions
        self.multiAreaRevisited = multiAreaRevisited
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        areaId = try c.decode(String.self, forKey: .areaId)
        startedAt = try c.decode(Date.self, forKey: .startedAt)
        endedAt = try c.decode(Date.self, forKey: .endedAt)
        distanceMi = try c.decode(Double.self, forKey: .distanceMi)
        durationSeconds = try c.decode(Int.self, forKey: .durationSeconds)
        completedTrailIds = try c.decode([String].self, forKey: .completedTrailIds)
        path = try c.decode([GpsPoint].self, forKey: .path)
        trailId = try c.decodeIfPresent(String.self, forKey: .trailId)
        revisitedTrailIds = try c.decodeIfPresent([String].self, forKey: .revisitedTrailIds) ?? []
        multiAreaCompletions = try c.decodeIfPresent([String: [String]].self, forKey: .multiAreaCompletions)
        multiAreaRevisited = try c.decodeIfPresent([String: [String]].self, forKey: .multiAreaRevisited)
    }

    /// The set of area ids this record touches: the primary area plus —
    /// for walks — every area in `multiAreaCompletions` /
    /// `multiAreaRevisited`. Used by per-area consumers (coverage
    /// rebuilds, Dex, anchors) so walks count in every credited area.
    var touchedAreaIds: Set<String> {
        var ids: Set<String> = [areaId]
        if let m = multiAreaCompletions { ids.formUnion(m.keys) }
        if let r = multiAreaRevisited { ids.formUnion(r.keys) }
        return ids
    }

    /// Newly-completed trail ids credited to `areaId` by this record —
    /// walk-aware: reads the multi-area dict for walks, the flat array
    /// for regular hikes (where it only ever holds the primary area's).
    func completedTrailIds(in area: String) -> [String] {
        if let m = multiAreaCompletions { return m[area] ?? [] }
        return area == areaId ? completedTrailIds : []
    }

    /// Revisited trail ids credited to `areaId` — same shape as
    /// `completedTrailIds(in:)`.
    func revisitedTrailIds(in area: String) -> [String] {
        if let r = multiAreaRevisited { return r[area] ?? [] }
        return area == areaId ? revisitedTrailIds : []
    }
}
