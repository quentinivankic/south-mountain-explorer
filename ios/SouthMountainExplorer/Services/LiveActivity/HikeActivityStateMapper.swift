import Foundation

/// Estimates expressed as seconds from now. Produced read-only by
/// `LiveActivityEstimateSource`; any field may be nil.
struct LiveActivityEstimates: Equatable, Sendable {
    var trailEndSeconds: TimeInterval?
    var backSeconds: TimeInterval?
    /// True when the matching estimate has `approximate` status.
    var trailEndApproximate = false
    var backApproximate = false
    static let none = LiveActivityEstimates(trailEndSeconds: nil, backSeconds: nil)
}

/// The turn cue engine's current cue, as the in-app banner shows it. Raw
/// values only; `LiveActivityNextTurnFormatter` does the wording and rounding.
struct LiveActivityNextTurn: Equatable, Sendable {
    var kind: TurnCueEvent.Kind
    var symbol: TurnCueSymbol
    var trailName: String
    /// Distance to the junction, a snapshot at evaluation time.
    var distanceMeters: Double?
}

/// Pure wording for the next-turn row. Distances use a coarse ladder so the
/// text only changes every few seconds of walking, never per GPS fix.
enum LiveActivityNextTurnFormatter {
    struct Row: Equatable {
        var arrow: HikeActivityAttributes.NextTurnArrow
        var text: String
        var distanceText: String?
        var trail: String?
        var spoken: String
    }

    /// (value, short unit, spoken unit) rounded to a coarse step.
    static func distance(meters: Double, units: UnitsPreference) -> (short: String, spoken: String)? {
        guard meters.isFinite else { return nil }
        let m = max(0, meters)
        switch units {
        case .imperial:
            let ft = m * TurnCuePhrasing.feetPerMeter
            if ft < 50 { return nil }
            let step = ft < 300 ? 50.0 : 100.0
            let v = Int((ft / step).rounded() * step)
            return ("\(v) ft", "\(v) feet")
        case .metric:
            if m < 15 { return nil }
            let step = m < 100 ? 25.0 : 50.0
            let v = Int((m / step).rounded() * step)
            return ("\(v) m", "\(v) meters")
        }
    }

    static func row(for turn: LiveActivityNextTurn, units: UnitsPreference) -> Row? {
        let trail = turn.trailName.trimmingCharacters(in: .whitespacesAndNewlines)
        let named = trail.isEmpty ? nil : trail
        switch turn.kind {
        case .gpsWeak:
            return nil
        case .offTrail:
            let t = named.map { "Off \($0)" } ?? "Off the trail"
            return Row(arrow: .offTrail, text: t, distanceText: nil, trail: nil, spoken: t)
        case .backOnTrail:
            let t = named.map { "Back on \($0)" } ?? "Back on the trail"
            return Row(arrow: .backOnTrail, text: t, distanceText: nil, trail: nil, spoken: t)
        case .approach, .atJunction:
            let arrow: HikeActivityAttributes.NextTurnArrow
            switch turn.symbol {
            case .keepLeft: arrow = .keepLeft
            case .keepRight: arrow = .keepRight
            case .turnLeft: arrow = .turnLeft
            case .turnRight: arrow = .turnRight
            case .sharpLeft: arrow = .sharpLeft
            case .sharpRight: arrow = .sharpRight
            case .offTrail, .backOnTrail, .gpsWeak: return nil
            }
            let action = TurnCuePhrasing.action(turn.symbol)
            let cap = TurnCuePhrasing.capitalized(action)
            let onto = named.map { " onto \($0)" } ?? ""
            if turn.kind == .atJunction {
                return Row(arrow: arrow, text: "\(cap) now", distanceText: "Now", trail: named,
                           spoken: "\(action) now\(onto)")
            }
            guard let meters = turn.distanceMeters else { return nil }
            guard let d = distance(meters: meters, units: units) else {
                return Row(arrow: arrow, text: "\(cap) now", distanceText: "Now", trail: named,
                           spoken: "\(action) now\(onto)")
            }
            return Row(arrow: arrow, text: "\(cap) in \(d.short)", distanceText: d.short, trail: named,
                       spoken: "\(action) in \(d.spoken)\(onto)")
        }
    }
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
    /// Nil when turn cues are not active or nothing is pending.
    var nextTurn: LiveActivityNextTurn? = nil
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

        // The turn engine and the estimator judge "off trail" separately, so
        // the cue can say off trail a moment before the estimate does. Finish
        // is meaningless off trail; hide it. Back (retrace) stays.
        if snapshot.nextTurn?.kind == .offTrail { trailEnd = nil }

        var turnRow: LiveActivityNextTurnFormatter.Row?
        if finishedAt == nil, !status.isDegraded, let turn = snapshot.nextTurn {
            turnRow = LiveActivityNextTurnFormatter.row(for: turn, units: units)
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
            endedAt: finishedAt,
            estimatedTrailEndApprox: trailEnd == nil ? nil : snapshot.estimates.trailEndApproximate,
            estimatedBackApprox: back == nil ? nil : snapshot.estimates.backApproximate,
            nextTurnArrow: turnRow?.arrow,
            nextTurnText: turnRow?.text,
            nextTurnDistanceText: turnRow?.distanceText,
            nextTurnTrail: turnRow?.trail,
            nextTurnSpoken: turnRow?.spoken
        )
    }
}
