import Foundation

// [lat, lon, timestamp(ms)]
typealias GpsPoint = [Double]

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
    let newlyCompletedTrailIds: [String]
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

    enum CodingKeys: String, CodingKey {
        case id, areaId, startedAt, endedAt, distanceMi, durationSeconds, completedTrailIds, path, trailId
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
        trailId: String? = nil
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
    }
}
