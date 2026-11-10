import CallKit
import Foundation
import Observation
import UIKit
import os

private let coordinatorLog = Logger(subsystem: "com.trekdex.app", category: "turn-cues")

/// What the banner shows. Pure data so the view stays dumb.
struct TurnCueBannerState: Equatable, Identifiable {
    let id: UUID
    let kind: TurnCueEvent.Kind
    let symbol: TurnCueSymbol
    let headline: String
    var distanceText: String?
    let junctionId: Int?
    let expiresAt: Date?
}

/// App-level owner of turn cues. Watches `RecordingService.activeRecording`
/// (never edits it), builds the route for a trail recording, feeds fixes to
/// the pure `TurnCueEngine`, and hands events to the speech / haptic / banner
/// adapters through `TurnCueDispatcher`.
@MainActor
@Observable
final class TurnCueCoordinator {
    static let shared = TurnCueCoordinator(
        recording: .shared, location: .shared, areas: .shared
    )

    private(set) var bannerCue: TurnCueBannerState?
    /// The plan guide's step, distance and off-route state while a hike
    /// started from a plan is being guided. Nil otherwise.
    private(set) var planGuide: PlanGuideEngine.Status?

    @ObservationIgnored private let recording: RecordingService
    @ObservationIgnored private let location: LocationService
    @ObservationIgnored private let areas: AreaDataService
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let speaker: TurnCueSpeaker
    @ObservationIgnored private let haptics: TurnCueHaptics
    @ObservationIgnored private let notifier: TurnCueNotifier
    @ObservationIgnored private let planStore: PlanGuideStore
    @ObservationIgnored private let callObserver = CXCallObserver()
    @ObservationIgnored private var started = false
    @ObservationIgnored private var currentKey: Key?
    @ObservationIgnored private var session: Task<Void, Never>?
    @ObservationIgnored private var engine: TurnCueEngine?
    @ObservationIgnored private var lastFixTimestamp: Date?
    @ObservationIgnored private var routeCache: [String: TurnCueRoute] = [:]
    @ObservationIgnored private var planEngine: PlanGuideEngine?
    @ObservationIgnored private var planSource: PlanSource?
    /// The recording the saved plan was last checked against, so the file is
    /// read once per recording, not on every fix.
    @ObservationIgnored private var storeCheckedFor: String??

    private enum Key: Equatable {
        case trail(recordingId: String, areaId: String, trailId: String)
        case plan(recordingId: String, session: UUID)

        var recordingId: String {
            switch self {
            case .trail(let id, _, _), .plan(let id, _): return id
            }
        }
    }

    /// A plan handed over by Start hike, for the recording it started.
    private struct PlanSource {
        let recordingId: String
        let route: PlanGuideRoute
        let session: UUID
        /// What is saved so the guide survives a relaunch.
        var snapshot: PlanGuideSnapshot
        /// Brought back from the store after a relaunch: resume at the saved step.
        let restored: Bool
    }

    init(
        recording: RecordingService,
        location: LocationService,
        areas: AreaDataService,
        defaults: UserDefaults = .standard,
        speaker: TurnCueSpeaker? = nil,
        haptics: TurnCueHaptics? = nil,
        notificationCenter: (any TurnCueNotificationCenter)? = nil,
        planStore: PlanGuideStore? = nil
    ) {
        self.planStore = planStore ?? .live()
        self.recording = recording
        self.location = location
        self.areas = areas
        self.defaults = defaults
        self.speaker = speaker ?? TurnCueSpeaker()
        self.haptics = haptics ?? TurnCueHaptics()
        self.notifier = TurnCueNotifier(center: notificationCenter ?? UNTurnCueNotificationCenter())
    }

    func start() {
        guard !started else { return }
        started = true
        observe()
        reconcile()
        #if DEBUG
        debugAutoStartIfRequested()
        #endif
    }

    // MARK: - Lifecycle

    private func observe() {
        withObservationTracking {
            _ = recording.activeRecording
        } onChange: { [weak self] in
            Task { @MainActor in
                self?.reconcile()
                self?.observe()
            }
        }
    }

    private func reconcile() {
        let rec = recording.activeRecording
        let recordingId = rec.map { $0.recordingId ?? "" }
        // The plan's recording ended (or another one started): the guide goes with it.
        if let plan = planSource, case .plan(_, plan.session) = currentKey, recordingId != plan.recordingId {
            planSource = nil
            planStore.clear()
        }
        restoreSavedPlanIfNeeded(activeRecordingId: recordingId)
        let key: Key?
        if let plan = planSource, plan.recordingId == recordingId {
            key = .plan(recordingId: plan.recordingId, session: plan.session)
        } else if let rec, rec.mode == .trail, let trailId = rec.trailId {
            key = .trail(recordingId: rec.recordingId ?? "", areaId: rec.areaId, trailId: trailId)
        } else {
            key = nil
        }
        guard key != currentKey else { return }
        teardown()
        currentKey = key
        switch key {
        case .trail?:
            session = Task { [weak self] in await self?.run(key!) }
        case .plan?:
            guard let route = planSource?.route else { return }
            session = Task { [weak self] in await self?.runPlan(key!, route: route) }
        case nil:
            break
        }
    }

    private func teardown() {
        if let ended = currentKey { notifier.recordingEnded(recordingId: ended.recordingId) }
        session?.cancel()
        session = nil
        engine = nil
        planEngine = nil
        planGuide = nil
        lastFixTimestamp = nil
        bannerCue = nil
        speaker.cancelAll()
    }

    // MARK: - Plan guide

    /// Start hike from a plan: guide `steps` for recording `recordingId`
    /// instead of trail cues, and save them so a relaunch resumes the guide.
    /// Ends with that recording or `endPlanGuide()`. false when the steps
    /// have nothing to walk.
    @discardableResult
    func beginPlanGuide(steps: [PlanStep], recordingId: String) -> Bool {
        guard !recordingId.isEmpty, let route = PlanGuideRoute(steps: steps) else { return false }
        let snapshot = PlanGuideSnapshot(recordingId: recordingId, steps: steps)
        planStore.save(snapshot)
        storeCheckedFor = .some(recordingId)
        planSource = PlanSource(recordingId: recordingId, route: route, session: UUID(),
                                snapshot: snapshot, restored: false)
        reconcile()
        return true
    }

    /// Exit: ends the guide; the recording keeps going.
    func endPlanGuide() {
        planSource = nil
        planStore.clear()
        reconcile()
    }

    /// Back and Next during a hike move the guide to step `index`.
    func movePlanGuide(toStep index: Int) {
        guard var e = planEngine else { return }
        e.move(toStep: index)
        planEngine = e
        speaker.cancelAll()
        bannerCue = nil
        planGuide = e.status
        savePlanProgress(e)
    }

    /// After a relaunch: the saved plan for the active recording becomes the
    /// guide again. A plan saved for a recording that is no longer active is
    /// dropped. Checked once per recording.
    private func restoreSavedPlanIfNeeded(activeRecordingId: String?) {
        guard planSource == nil, storeCheckedFor != .some(activeRecordingId) else { return }
        storeCheckedFor = .some(activeRecordingId)
        guard let saved = planStore.load() else { return }
        switch PlanGuideStore.decide(saved, activeRecordingId: activeRecordingId) {
        case .keep:
            break
        case .discard:
            planStore.clear()
        case .restore:
            guard let steps = saved.planSteps, let route = PlanGuideRoute(steps: steps) else {
                planStore.clear()
                return
            }
            coordinatorLog.notice("plan guide restored at step \(saved.stepIndex + 1)")
            planSource = PlanSource(recordingId: saved.recordingId, route: route, session: UUID(),
                                    snapshot: saved, restored: true)
        }
    }

    /// Saves the guide's step when it changes, so a relaunch resumes there.
    private func savePlanProgress(_ e: PlanGuideEngine) {
        guard var plan = planSource,
              plan.snapshot.stepIndex != e.stepIndex || plan.snapshot.isComplete != e.isComplete else { return }
        plan.snapshot.stepIndex = e.stepIndex
        plan.snapshot.isComplete = e.isComplete
        planSource = plan
        planStore.save(plan.snapshot)
    }

    private func runPlan(_ key: Key, route: PlanGuideRoute) async {
        guard currentKey == key else { return }
        var e = PlanGuideEngine(route: route, config: PlanGuideConfig(units: currentUnits()))
        if let plan = planSource, plan.restored {
            // Resume on the saved step, searching forward from its start.
            e.restore(toStep: plan.snapshot.stepIndex, isComplete: plan.snapshot.isComplete)
        }
        planEngine = e
        planGuide = e.status
        // Start from "now" so a fix cached before the guide started is replayed once.
        lastFixTimestamp = nil
        while !Task.isCancelled, currentKey == key {
            pollPlan()
            try? await Task.sleep(for: .seconds(1))
        }
    }

    private func pollPlan() {
        guard var e = planEngine else { return }
        let now = Date()
        e.config.units = currentUnits()
        let update: PlanGuideEngine.Update
        if let live = location.liveFix, live.timestamp != lastFixTimestamp {
            lastFixTimestamp = live.timestamp
            update = e.ingest(Self.fix(live), now: now.timeIntervalSince1970)
        } else {
            update = e.tick(now: now.timeIntervalSince1970)
        }
        planEngine = e
        savePlanProgress(e)
        for event in update.events {
            deliver(event, now: now)
        }
        if update.routeCompleted {
            // The finish is a buzz and "Route complete" on the bar, no voice.
            bannerCue = nil
            if TurnCueDispatcher.boolSetting(StorageKeys.turnCueHaptics, defaults: defaults) {
                haptics.play(.backOnTrail)
            }
        }
        if e.status != planGuide { planGuide = e.status }
        refreshBanner(advisory: e.advisory, now: now)
    }

    private static func fix(_ live: LiveFix) -> TurnCueFix {
        TurnCueFix(
            lat: live.coordinate.latitude,
            lon: live.coordinate.longitude,
            timestamp: live.timestamp.timeIntervalSince1970,
            horizontalAccuracy: live.horizontalAccuracy,
            speed: live.speed
        )
    }

    private func isStale(_ event: TurnCueEvent) -> Bool {
        if let planEngine { return planEngine.isStale(event) }
        return engine?.isStale(event) ?? true
    }

    private func run(_ key: Key) async {
        guard case .trail(_, let areaId, let trailId) = key,
              let route = await loadRoute(areaId: areaId, trailId: trailId),
              !Task.isCancelled, currentKey == key else { return }
        var e = TurnCueEngine(route: route, config: TurnCueConfig(units: currentUnits()))
        // Start from "now" so a fix cached before the route loaded is replayed once.
        lastFixTimestamp = nil
        while !Task.isCancelled {
            poll(&e)
            engine = e
            try? await Task.sleep(for: .seconds(1))
        }
    }

    private func loadRoute(areaId: String, trailId: String) async -> TurnCueRoute? {
        let cacheKey = "\(areaId)|\(trailId)"
        if let cached = routeCache[cacheKey] { return cached }
        let area: Area?
        if let cachedArea = areas.cachedArea(id: areaId) {
            area = cachedArea
        } else {
            area = await areas.area(id: areaId)
        }
        guard let area else {
            coordinatorLog.notice("turn cues idle: area unavailable")
            return nil
        }
        let trails = area.rawTrails ?? area.trails
        guard let selected = trails.first(where: { $0.id == trailId }) else {
            coordinatorLog.notice("turn cues idle: trail not found")
            return nil
        }
        let route = await Task.detached(priority: .utility) {
            TurnCueRouteBuilder.build(selected: selected, others: trails)
        }.value
        guard !route.isEmpty else {
            coordinatorLog.notice("turn cues idle: empty route")
            return nil
        }
        coordinatorLog.notice("route ready: chains=\(route.chains.count) junctions=\(route.junctions.count)")
        routeCache[cacheKey] = route
        return route
    }

    /// Read-only snapshot of the cue the in-app banner is showing, for the
    /// Live Activity. Nil when turn cues are inactive or nothing is shown.
    func liveCue() -> LiveActivityNextTurn? {
        guard let cue = bannerCue else { return nil }
        if let planEngine {
            let adv = planEngine.advisory
            let distance = adv?.junctionId == cue.junctionId ? adv?.distanceMeters : nil
            // Plan cues are direction only: no trail name.
            return LiveActivityNextTurn(kind: cue.kind, symbol: cue.symbol, trailName: "", distanceMeters: distance)
        }
        guard let engine else { return nil }
        let adv = engine.advisory
        let distance = (adv != nil && adv?.junctionId == cue.junctionId) ? adv?.distanceMeters : nil
        return LiveActivityNextTurn(kind: cue.kind, symbol: cue.symbol,
                                    trailName: engine.route.trailName, distanceMeters: distance)
    }

    // MARK: - Poll

    private func poll(_ e: inout TurnCueEngine) {
        let now = Date()
        e.config.units = currentUnits()
        var events: [TurnCueEvent] = []
        if let live = location.liveFix, live.timestamp != lastFixTimestamp {
            lastFixTimestamp = live.timestamp
            let fix = TurnCueFix(
                lat: live.coordinate.latitude,
                lon: live.coordinate.longitude,
                timestamp: live.timestamp.timeIntervalSince1970,
                horizontalAccuracy: live.horizontalAccuracy,
                speed: live.speed
            )
            events = e.ingest(fix, now: now.timeIntervalSince1970)
        } else {
            events = e.tick(now: now.timeIntervalSince1970)
        }
        engine = e
        for event in events {
            deliver(event, now: now)
        }
        refreshBanner(advisory: e.advisory, now: now)
    }

    private func deliver(_ event: TurnCueEvent, now: Date) {
        let ctx = TurnCueDeliveryContext(
            voiceEnabled: TurnCueDispatcher.boolSetting(StorageKeys.turnCueVoice, defaults: defaults),
            hapticsEnabled: TurnCueDispatcher.boolSetting(StorageKeys.turnCueHaptics, defaults: defaults),
            voiceOverRunning: UIAccessibility.isVoiceOverRunning,
            appActive: location.isApplicationActive,
            callActive: callObserver.calls.contains { !$0.hasEnded }
        )
        let actions = TurnCueDispatcher.plan(event, ctx)
        postLockedNotification(for: event, ctx: ctx, actions: actions)
        for action in actions {
            switch action {
            case .banner(let ev):
                showBanner(for: ev, now: now)
            case .haptic(let pattern):
                haptics.play(pattern)
            case .speak(let text):
                let priority: TurnCueSpeaker.Priority = event.kind == .approach ? .far : .near
                speaker.speak(text, priority: priority) { [weak self] in self?.isStale(event) ?? true }
            case .announce(let text):
                coordinatorLog.notice("announce: \(text, privacy: .public)")
                UIAccessibility.post(
                    notification: .announcement,
                    argument: NSAttributedString(
                        string: text,
                        attributes: [.accessibilitySpeechQueueAnnouncement: true]
                    )
                )
            }
        }
    }

    /// Lock Screen cue: only while the app is not active; the notifier
    /// dedupes, replaces and checks permission.
    private func postLockedNotification(for event: TurnCueEvent, ctx: TurnCueDeliveryContext, actions: [TurnCueAction]) {
        guard let key = currentKey else { return }
        let speaks = actions.contains { if case .speak = $0 { return true } else { return false } }
        let nctx = TurnCueNotificationContext(
            enabled: defaults.bool(forKey: StorageKeys.turnCueNotifyLocked),
            appActive: ctx.appActive,
            recordingId: key.recordingId,
            trailName: engine?.route.trailName ?? "",
            units: currentUnits(),
            voiceWillSpeak: speaks)
        guard nctx.enabled, !nctx.appActive else { return }
        Task { @MainActor [notifier] in await notifier.handle(event, nctx) }
    }

    // MARK: - Banner

    private func showBanner(for event: TurnCueEvent, now: Date) {
        let units = currentUnits()
        let expires: Date?
        switch event.kind {
        case .approach: expires = nil
        case .atJunction: expires = now.addingTimeInterval(8)
        case .offTrail: expires = now.addingTimeInterval(60)
        case .backOnTrail, .gpsWeak: expires = now.addingTimeInterval(6)
        }
        bannerCue = TurnCueBannerState(
            id: UUID(), kind: event.kind, symbol: event.symbol, headline: event.shortText,
            distanceText: event.distanceMeters.map { TurnCuePhrasing.shortDistance(meters: $0, units: units) },
            junctionId: event.junctionId, expiresAt: expires
        )
    }

    private func refreshBanner(advisory: TurnCueAdvisory?, now: Date) {
        guard var cue = bannerCue else { return }
        if let exp = cue.expiresAt, now >= exp { bannerCue = nil; return }
        switch cue.kind {
        case .approach, .atJunction:
            guard let adv = advisory, adv.junctionId == cue.junctionId else { bannerCue = nil; return }
            if let d = adv.distanceMeters {
                cue.distanceText = TurnCuePhrasing.shortDistance(meters: d, units: currentUnits())
                if cue != bannerCue { bannerCue = cue }
            }
        case .offTrail:
            if advisory?.symbol != .offTrail { bannerCue = nil }
        case .backOnTrail, .gpsWeak:
            break
        }
    }

    private func currentUnits() -> UnitsPreference {
        (defaults.string(forKey: StorageKeys.units)).flatMap(UnitsPreference.init(rawValue:)) ?? .imperial
    }

    #if DEBUG
    /// DEBUG only: `-turnCueDebugStart <areaId>:<trailId>` starts a trail
    /// recording once the area is available, so the simulator hike can run
    /// without UI taps. Compiled out of Release.
    private func debugAutoStartIfRequested() {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-turnCueDebugStart"), i + 1 < args.count else { return }
        let parts = args[i + 1].split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2, recording.activeRecording == nil else { return }
        Task { @MainActor in
            _ = await areas.area(id: parts[0])
            let result = recording.startRecording(areaId: parts[0], mode: .trail, trailId: parts[1])
            coordinatorLog.notice("debug start: \(String(describing: result), privacy: .public)")
        }
    }
    #endif
}
