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
            // Only the plan guide cues these two.
            case .straight: arrow = .straight
            case .turnAround: arrow = .turnAround
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

/// The plan guide's state while a hike started from a plan is guided
/// (Release B task 6), so the Lock Screen and Dynamic Island show the same
/// step as the bar.
struct LiveActivityPlanGuide: Equatable, Sendable {
    var steps: [PlanStep]
    var status: PlanGuideEngine.Status
}

/// Pure wording for the plan step row. It uses the bar's own text, so the
/// Lock Screen and the bar always agree.
enum LiveActivityPlanStepFormatter {
    typealias Row = LiveActivityNextTurnFormatter.Row

    /// - Route complete: flag, "Route complete".
    /// - Off route: warning, "Off route, 150 ft to route".
    /// - A turn cue is live (heads-up or at the turn): its arrow and "Turn left in 300 ft".
    /// - Back on route: check, "Back on route" (for the few seconds the cue shows).
    /// - Otherwise the step: its arrow and "2. Left onto Viewpoint Trail, 0.4 mi to next turn".
    /// The second line is "Step 2 of 4" except at the finish and off route.
    static func row(guide: LiveActivityPlanGuide, cue: LiveActivityNextTurn?, units: UnitsPreference) -> Row? {
        let status = guide.status
        let line = PlanGuideText.line(steps: guide.steps, status: status, units: units)
        if status.isComplete {
            return Row(arrow: .routeComplete, text: line, distanceText: nil, trail: nil, spoken: line)
        }
        if let off = status.offRouteMeters {
            return Row(arrow: .offTrail, text: line, distanceText: PlanGuideText.distance(meters: off, units: units),
                       trail: nil, spoken: line)
        }
        let counter = PlanGuideText.counter(status)
        if let cue {
            switch cue.kind {
            case .approach, .atJunction:
                if var row = LiveActivityNextTurnFormatter.row(for: cue, units: units) {
                    row.trail = counter
                    return row
                }
            case .backOnTrail:
                let t = TurnCuePhrasing.backOnRoute
                return Row(arrow: .backOnTrail, text: t, distanceText: nil, trail: counter, spoken: t)
            case .offTrail, .gpsWeak:
                break
            }
        }
        guard status.stepIndex < guide.steps.count else { return nil }
        let step = guide.steps[status.stepIndex]
        return Row(arrow: arrow(for: step.action), text: line,
                   distanceText: PlanGuideText.distance(meters: status.metersToNextTurn, units: units),
                   trail: counter, spoken: "\(counter). \(line)")
    }

    /// The step's own arrow. Start reads as straight ahead.
    static func arrow(for action: PlanStep.Action) -> HikeActivityAttributes.NextTurnArrow {
        switch action {
        case .start, .straight: return .straight
        case .keepLeft: return .keepLeft
        case .keepRight: return .keepRight
        case .left: return .turnLeft
        case .right: return .turnRight
        case .sharpLeft: return .sharpLeft
        case .sharpRight: return .sharpRight
        case .turnAround: return .turnAround
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
    /// Set while a hike started from a plan is guided; the row then shows the
    /// plan step instead of a trail cue.
    var planGuide: LiveActivityPlanGuide? = nil
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
        if snapshot.nextTurn?.kind == .offTrail || snapshot.planGuide?.status.offRouteMeters != nil { trailEnd = nil }

        var turnRow: LiveActivityNextTurnFormatter.Row?
        if finishedAt == nil, !status.isDegraded {
            if let guide = snapshot.planGuide {
                turnRow = LiveActivityPlanStepFormatter.row(guide: guide, cue: snapshot.nextTurn, units: units)
            } else if let turn = snapshot.nextTurn {
                turnRow = LiveActivityNextTurnFormatter.row(for: turn, units: units)
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
