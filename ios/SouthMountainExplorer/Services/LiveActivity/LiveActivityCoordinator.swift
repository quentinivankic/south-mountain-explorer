import Foundation
import Observation
import UIKit

/// What the coordinator needs from the app each evaluation.
struct LiveActivityObservedState: Equatable, Sendable {
    var snapshot: LiveActivityRecordingSnapshot?
    var isStopping: Bool
    var isRecoveryBlocked: Bool
}

/// Starts, updates and ends the hike Live Activity by OBSERVING the recording
/// services. It never writes to them, so recording code stays untouched.
@MainActor
final class LiveActivityCoordinator {
    static let shared = LiveActivityCoordinator()

    typealias ContentState = HikeActivityAttributes.ContentState

    static let staleInterval: TimeInterval = 120
    static let tickInterval: Duration = .seconds(15)
    static let retryInterval: TimeInterval = 60
    static let maxStartAttempts = 3
    static let systemActivityLimit: TimeInterval = 8 * 3600

    private let client: LiveActivityClient
    private let observedProvider: @MainActor () -> LiveActivityObservedState
    private let finishedLookup: @MainActor (String) async -> Bool
    private let log: @MainActor (String, [String: String]) -> Void
    private let config: LiveActivityThrottlePolicy.Config

    private var currentActivityId: String?
    private var tracked: LiveActivityRecordingIdentity?
    private var lastSnapshot: LiveActivityRecordingSnapshot?
    private var lastSent: LiveActivityThrottlePolicy.SentRecord?
    private var sawStopping = false
    private var userDismissedRecordingId: String?
    private var startFailures = 0
    private var lastStartFailureAt: Date?
    private var isEvaluating = false
    private var needsReevaluate = false
    private var tickTask: Task<Void, Never>?
    private var started = false

    init(client: LiveActivityClient? = nil,
         observedProvider: (@MainActor () -> LiveActivityObservedState)? = nil,
         finishedLookup: (@MainActor (String) async -> Bool)? = nil,
         log: (@MainActor (String, [String: String]) -> Void)? = nil,
         config: LiveActivityThrottlePolicy.Config = .init()) {
        self.client = client ?? ActivityKitClient()
        self.observedProvider = observedProvider ?? { LiveActivityCoordinator.liveObservedState() }
        self.finishedLookup = finishedLookup ?? { id in
            await RecordingService.shared.loadHistory().contains { $0.id == id }
        }
        self.log = log ?? { action, ctx in
            ActivityLogService.shared.log(category: "liveActivity", action: action, context: ctx)
        }
        self.config = config
    }

    // MARK: - Production wiring

    /// Called once from app init. Reconciles leftovers, then observes.
    func start() {
        guard !started else { return }
        // The screenshot UI tests inject demo recordings; keep them quiet.
        if CommandLine.arguments.contains(where: { $0.hasPrefix("--uitest") }) { return }
        started = true
        arm()
        Task { await evaluate() }
        Task { [weak self] in
            guard let stream = self?.client.enablementUpdates() else { return }
            for await _ in stream { await self?.evaluate() }
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in await self?.evaluate() }
        }
    }

    private func arm() {
        withObservationTracking {
            let rec = RecordingService.shared
            _ = rec.activeRecording
            _ = rec.isStopping
            _ = rec.recoveryIssue
            _ = LocationService.shared.lastFixDate
        } onChange: { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                await self.evaluate()
                self.arm()
            }
        }
    }

    private static func liveObservedState() -> LiveActivityObservedState {
        let recording = RecordingService.shared
        let blocked = recording.recoveryIssue != nil
        guard let rec = recording.activeRecording else {
            return LiveActivityObservedState(snapshot: nil, isStopping: recording.isStopping,
                                             isRecoveryBlocked: blocked)
        }
        let location = LocationService.shared
        let areas = AreaDataService.shared
        let isWalk = rec.mode == .walk
        let area = areas.cachedArea(id: rec.areaId)
        let areaName = area?.name ?? areas.summaries.first { $0.id == rec.areaId }?.name
        let trail = rec.trailId.flatMap { id in area?.trails.first { $0.id == id } }

        let title: String
        var subtitle: String?
        if isWalk {
            title = "Walk"
        } else if let trail {
            title = trail.name
            subtitle = areaName
        } else {
            title = areaName ?? "Hiking"
        }
        let units = UserDefaults.standard.string(forKey: StorageKeys.units)
        let id = rec.recordingId ?? LiveActivityRecordingIdentity.fallbackId(startedAt: rec.startedAt)
        let snapshot = LiveActivityRecordingSnapshot(
            recordingId: id,
            startedAt: rec.startedAt,
            isWalk: isWalk,
            distanceMeters: rec.distanceMi * 1609.344,
            gpsStatus: GpsIngest.activeStatus(path: rec.path, lastFixAt: location.lastFixDate),
            title: title,
            subtitle: subtitle,
            trailLengthMeters: (rec.mode == .trail) ? trail.map { $0.distanceMi * 1609.344 } : nil,
            estimates: LiveActivityEstimateSource.estimates(for: rec, recording: recording),
            usesMetric: units == UnitsPreference.metric.rawValue
        )
        return LiveActivityObservedState(snapshot: snapshot, isStopping: recording.isStopping,
                                         isRecoveryBlocked: blocked)
    }

    // MARK: - Evaluation

    func evaluate(now: Date = Date()) async {
        if isEvaluating { needsReevaluate = true; return }
        isEvaluating = true
        defer { isEvaluating = false }
        repeat {
            needsReevaluate = false
            await evaluateOnce(observed: observedProvider(), now: now)
        } while needsReevaluate
    }

    private func evaluateOnce(observed: LiveActivityObservedState, now: Date) async {
        let snapshot = observed.snapshot
        let identity = snapshot.map { LiveActivityRecordingIdentity(id: $0.recordingId, startedAt: $0.startedAt) }

        if let identity {
            if tracked?.id != identity.id {
                lastSent = nil
                sawStopping = false
                startFailures = 0
                lastStartFailureAt = nil
            }
            tracked = identity
            // Latched only while stopping; a failed save clears it again.
            sawStopping = observed.isStopping
            lastSnapshot = snapshot
        }

        var existing = client.currentActivities()

        // A tracked activity that vanished while the hike runs was swiped away.
        if let id = currentActivityId, let identity,
           !existing.contains(where: { $0.id == id && !$0.isEnded }) {
            currentActivityId = nil
            if now.timeIntervalSince(identity.startedAt) < Self.systemActivityLimit {
                userDismissedRecordingId = identity.id
                log("dismissedByUser", [:])
            }
        }

        // Recording vanished without having seen isStopping: a fast save can
        // finish between observations, so ask history (read-only).
        if identity == nil, let trackedId = tracked?.id, !sawStopping,
           existing.contains(where: { !$0.isEnded && $0.recordingId == trackedId }) {
            if await finishedLookup(trackedId) { sawStopping = true }
            existing = client.currentActivities()
        }

        let actions = LiveActivityLifecycle.decide(.init(
            recording: identity,
            sawStopping: sawStopping,
            activitiesEnabled: client.areActivitiesEnabled,
            existing: existing,
            previouslyTrackedRecordingId: tracked?.id,
            currentActivityId: currentActivityId,
            isRecoveryBlocked: observed.isRecoveryBlocked,
            userDismissedRecordingId: userDismissedRecordingId
        ))

        for action in actions {
            await perform(action, snapshot: snapshot, now: now)
        }

        if identity == nil {
            tracked = nil
            lastSnapshot = nil
            lastSent = nil
            currentActivityId = nil
            sawStopping = false
            userDismissedRecordingId = nil
            startFailures = 0
            stopTick()
        } else {
            startTickIfNeeded()
        }
    }

    private func perform(_ action: LiveActivityLifecycle.Action,
                         snapshot: LiveActivityRecordingSnapshot?,
                         now: Date) async {
        switch action {
        case .start:
            guard let snapshot, startFailures < Self.maxStartAttempts else { return }
            if let failedAt = lastStartFailureAt,
               now.timeIntervalSince(failedAt) < Self.retryInterval { return }
            let state = HikeActivityStateMapper.contentState(from: snapshot, now: now)
            let attributes = HikeActivityAttributes(recordingId: snapshot.recordingId,
                                                    startedAt: snapshot.startedAt,
                                                    isWalk: snapshot.isWalk)
            do {
                let id = try client.request(attributes: attributes, state: state,
                                            staleDate: now.addingTimeInterval(Self.staleInterval))
                currentActivityId = id
                lastSent = .init(state: state, sentAt: now, distanceMetersAtSend: snapshot.distanceMeters)
                startFailures = 0
                lastStartFailureAt = nil
                log("start", [:])
            } catch {
                startFailures += 1
                lastStartFailureAt = now
                log("startFailed", ["error": String(describing: error), "attempt": "\(startFailures)"])
            }
        case .adopt(let id):
            currentActivityId = id
            lastSent = nil
            log("adopt", [:])
            await sendUpdate(id: id, snapshot: snapshot, now: now)
        case .update(let id):
            await sendUpdate(id: id, snapshot: snapshot, now: now)
        case .endFinished(let id, let dismissAfter):
            var final: ContentState?
            if let last = lastSnapshot {
                final = HikeActivityStateMapper.contentState(from: last, now: now, finishedAt: now)
            }
            await client.end(id: id, finalState: final, dismissAfter: final == nil ? nil : dismissAfter)
            log("endFinished", [:])
        case .endImmediately(let id):
            await client.end(id: id, finalState: nil, dismissAfter: nil)
            log("endImmediately", [:])
        }
    }

    private func sendUpdate(id: String, snapshot: LiveActivityRecordingSnapshot?, now: Date) async {
        guard let snapshot else { return }
        let state = HikeActivityStateMapper.contentState(from: snapshot, now: now)
        let decision = LiveActivityThrottlePolicy.decide(
            lastSent: lastSent, candidate: state,
            candidateDistanceMeters: snapshot.distanceMeters, now: now, config: config)
        guard case .send = decision else { return }
        lastSent = .init(state: state, sentAt: now, distanceMetersAtSend: snapshot.distanceMeters)
        await client.update(id: id, state: state, staleDate: now.addingTimeInterval(Self.staleInterval))
    }

    // MARK: - Tick (GPS loss is time-based, so nothing observable fires)

    private func startTickIfNeeded() {
        guard tickTask == nil else { return }
        tickTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.tickInterval)
                guard !Task.isCancelled else { break }
                await self?.evaluate()
            }
        }
    }

    private func stopTick() {
        tickTask?.cancel()
        tickTask = nil
    }
}
