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

    enum CodingKeys: String, CodingKey {
        case id
        case areaId = "area_id"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case distanceMi = "distance_mi"
        case durationSeconds = "duration_s"
        case completedTrailIds = "completed_trail_ids"
        case path
    }
}
