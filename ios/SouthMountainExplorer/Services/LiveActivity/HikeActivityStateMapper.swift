import Foundation

/// Estimates expressed as seconds from now. Produced read-only by
/// `LiveActivityEstimateSource`; any field may be nil.
struct LiveActivityEstimates: Equatable, Sendable {
    var trailEndSeconds: TimeInterval?
    var backSeconds: TimeInterval?
    static let none = LiveActivityEstimates(trailEndSeconds: nil, backSeconds: nil)
}

/// Everything the mapper needs, built by the coordinator from observable state.
struct LiveActivityRecordingSnapshot: Equatable, Sendable {
    var recordingId: String
    var startedAt: Date
    var isWalk: Bool
    var distanceMeters: Double
    var gpsStatus: GpsIngest.ActiveStatus
    var title: String
    var subtitle: String?
    var trailLengthMeters: Double?
    var estimates: LiveActivityEstimates
    var usesMetric: Bool
}

/// Pure snapshot -> `ContentState` mapping.
enum HikeActivityStateMapper {
    typealias ContentState = HikeActivityAttributes.ContentState

    static func contentState(from snapshot: LiveActivityRecordingSnapshot,
                             now: Date,
                             finishedAt: Date? = nil) -> ContentState {
        let status: ContentState.Status
        if finishedAt != nil {
            status = .finished
        } else {
            switch snapshot.gpsStatus {
            case .good: status = .recording
            case .waiting: status = .waitingForGPS
            case .paused: status = .gpsPaused
            case .recovered: status = .gpsRecovered
            }
        }

        let distance = snapshot.distanceMeters.isFinite ? max(0, snapshot.distanceMeters) : 0
        let units: UnitsPreference = snapshot.usesMetric ? .metric : .imperial

        var progress: Double?
        var lengthText: String?
        if let length = snapshot.trailLengthMeters, length.isFinite, length > 0, distance <= length {
            progress = distance / length
            lengthText = UnitFormatter.distance(meters: length, units: units)
        }

        var trailEnd: Date?
        var back: Date?
        if finishedAt == nil, !status.isDegraded {
            if let s = snapshot.estimates.trailEndSeconds, s.isFinite, s > 0 {
                trailEnd = now.addingTimeInterval(s)
            }
            if let s = snapshot.estimates.backSeconds, s.isFinite, s > 0 {
                back = now.addingTimeInterval(s)
            }
        }

        let trimmed = snapshot.title.trimmingCharacters(in: .whitespacesAndNewlines)
        let title = trimmed.isEmpty ? (snapshot.isWalk ? "Walk" : "Hiking") : trimmed
        let subtitle = snapshot.subtitle?.trimmingCharacters(in: .whitespacesAndNewlines)

        return ContentState(
            status: status,
            title: title,
            subtitle: (subtitle?.isEmpty ?? true) ? nil : subtitle,
            distanceText: UnitFormatter.distance(meters: distance, units: units),
            trailLengthText: lengthText,
            progress: progress,
            estimatedTrailEndAt: trailEnd,
            estimatedBackAt: back,
            endedAt: finishedAt
        )
    }
}
