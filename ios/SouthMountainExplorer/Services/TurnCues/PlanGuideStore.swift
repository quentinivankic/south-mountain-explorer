import Foundation
import os

private let storeLog = Logger(subsystem: "com.trekdex.app", category: "turn-cues")

// "Plan a Hike", Release B task 4: the plan handed to the guide by Start
// hike, saved next to the recording it started, so the guide comes back on
// the same plan and step if iOS closes the app mid-hike.

/// What is saved: the recording, the plan's steps, and where the guide was.
struct PlanGuideSnapshot: Codable, Equatable, Sendable {
    struct Coord: Codable, Equatable, Sendable {
        let lat: Double
        let lon: Double
    }

    struct Step: Codable, Equatable, Sendable {
        let number: Int
        let action: String
        let kind: String
        let trailName: String?
        let staysOnTrail: Bool
        let meters: Double
        let at: Coord
        let coords: [Coord]
        let isLast: Bool
    }

    static let currentVersion = 1

    var version = Self.currentVersion
    let recordingId: String
    let steps: [Step]
    /// 0-based index of the step the guide was on.
    var stepIndex: Int
    var isComplete: Bool

    init(recordingId: String, steps: [PlanStep], stepIndex: Int = 0, isComplete: Bool = false) {
        self.recordingId = recordingId
        self.steps = steps.map(Step.init)
        self.stepIndex = stepIndex
        self.isComplete = isComplete
    }

    /// The saved steps as plan steps. nil when any value is not recognised.
    var planSteps: [PlanStep]? {
        var out: [PlanStep] = []
        for s in steps {
            guard let step = s.planStep else { return nil }
            out.append(step)
        }
        return out
    }
}

extension PlanGuideSnapshot.Step {
    init(_ s: PlanStep) {
        self.init(
            number: s.number, action: Self.name(s.action), kind: Self.name(s.kind),
            trailName: s.trailName, staysOnTrail: s.staysOnTrail, meters: s.meters,
            at: .init(lat: s.at.lat, lon: s.at.lon),
            coords: s.coords.map { .init(lat: $0.lat, lon: $0.lon) }, isLast: s.isLast)
    }

    var planStep: PlanStep? {
        guard let a = Self.actions.first(where: { $0.1 == action })?.0,
              let k = Self.kinds.first(where: { $0.1 == kind })?.0 else { return nil }
        return PlanStep(
            number: number, action: a, kind: k, trailName: trailName, staysOnTrail: staysOnTrail,
            meters: meters, at: .init(lat: at.lat, lon: at.lon),
            coords: coords.map { .init(lat: $0.lat, lon: $0.lon) }, isLast: isLast)
    }

    private static let actions: [(PlanStep.Action, String)] = [
        (.start, "start"), (.straight, "straight"), (.keepLeft, "keepLeft"), (.keepRight, "keepRight"),
        (.left, "left"), (.right, "right"), (.sharpLeft, "sharpLeft"), (.sharpRight, "sharpRight"),
        (.turnAround, "turnAround"),
    ]
    private static let kinds: [(PlannedRoute.Kind, String)] = [
        (.trail, "trail"), (.connector, "connector"), (.bridge, "bridge"),
    ]

    private static func name(_ a: PlanStep.Action) -> String { actions.first { $0.0 == a }!.1 }
    private static func name(_ k: PlannedRoute.Kind) -> String { kinds.first { $0.0 == k }!.1 }
}

/// One saved plan at a time, in a small JSON file. Writes are atomic; a file
/// that cannot be read is treated as no plan (the recording itself is safe
/// in `RecordingService`, this only restores the guide).
final class PlanGuideStore {
    let fileURL: URL

    init(fileURL: URL) {
        self.fileURL = fileURL
    }

    static func live() -> PlanGuideStore {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return PlanGuideStore(fileURL: base.appendingPathComponent("plan-guide.json"))
    }

    func load() -> PlanGuideSnapshot? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        guard let snap = try? JSONDecoder().decode(PlanGuideSnapshot.self, from: data),
              snap.version == PlanGuideSnapshot.currentVersion else {
            storeLog.notice("plan guide: saved plan unreadable, ignored")
            return nil
        }
        return snap
    }

    func save(_ snapshot: PlanGuideSnapshot) {
        do {
            try FileManager.default.createDirectory(
                at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(snapshot).write(to: fileURL, options: .atomic)
        } catch {
            storeLog.error("plan guide: save failed")
        }
    }

    /// The guide ended (Exit, the recording stopped, or another one started).
    func clear() {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
        try? FileManager.default.removeItem(at: fileURL)
    }

    /// What to do with a saved plan when the active recording is `activeId`.
    enum Decision: Equatable {
        /// Guide this recording again, from the saved step.
        case restore
        /// The saved plan belongs to a recording that is no longer active.
        case discard
        /// No active recording yet (or a recovery is pending): leave it.
        case keep
    }

    static func decide(_ snapshot: PlanGuideSnapshot, activeRecordingId activeId: String?) -> Decision {
        guard let activeId, !activeId.isEmpty else { return .keep }
        return snapshot.recordingId == activeId ? .restore : .discard
    }
}
