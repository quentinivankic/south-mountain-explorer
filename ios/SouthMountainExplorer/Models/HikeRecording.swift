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
/// end timestamp. `AreaView` holds an array of these and threads
/// them through to `TrailMapView`, which uses them for both the
/// cyan past-hike halo (path only) and the orange
/// walked-since-completion overlay (path + timestamp, filtered
/// against `ProgressService.completionDate`).
struct PastHike: Equatable, Sendable {
    let path: [GpsPoint]
    let endedAt: Date
}

enum RecordingMode: String, Codable, Sendable {
    case roam
    case trail
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

    enum CodingKeys: String, CodingKey {
        case id, areaId, startedAt, endedAt, distanceMi, durationSeconds, completedTrailIds, path, trailId, revisitedTrailIds
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
        revisitedTrailIds: [String] = []
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
    }
}
