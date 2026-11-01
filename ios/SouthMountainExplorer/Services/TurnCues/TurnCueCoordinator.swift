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

    @ObservationIgnored private let recording: RecordingService
    @ObservationIgnored private let location: LocationService
    @ObservationIgnored private let areas: AreaDataService
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let speaker: TurnCueSpeaker
    @ObservationIgnored private let haptics: TurnCueHaptics
    @ObservationIgnored private let notifier: TurnCueNotifier
    @ObservationIgnored private let callObserver = CXCallObserver()
    @ObservationIgnored private var started = false
    @ObservationIgnored private var currentKey: Key?
    @ObservationIgnored private var session: Task<Void, Never>?
    @ObservationIgnored private var engine: TurnCueEngine?
    @ObservationIgnored private var lastFixTimestamp: Date?
    @ObservationIgnored private var routeCache: [String: TurnCueRoute] = [:]

    private struct Key: Equatable {
        let recordingId: String
        let areaId: String
        let trailId: String
    }

    init(
        recording: RecordingService,
        location: LocationService,
        areas: AreaDataService,
        defaults: UserDefaults = .standard,
        speaker: TurnCueSpeaker? = nil,
        haptics: TurnCueHaptics? = nil,
        notificationCenter: (any TurnCueNotificationCenter)? = nil
    ) {
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
        let key: Key?
        if let rec = recording.activeRecording, rec.mode == .trail, let trailId = rec.trailId {
            key = Key(recordingId: rec.recordingId ?? "", areaId: rec.areaId, trailId: trailId)
        } else {
            key = nil
        }
        guard key != currentKey else { return }
        teardown()
        currentKey = key
        guard let key else { return }
        session = Task { [weak self] in await self?.run(key) }
    }

    private func teardown() {
        if let ended = currentKey { notifier.recordingEnded(recordingId: ended.recordingId) }
        session?.cancel()
        session = nil
        engine = nil
        lastFixTimestamp = nil
        bannerCue = nil
        speaker.cancelAll()
    }

    private func run(_ key: Key) async {
        guard let route = await loadRoute(key), !Task.isCancelled, currentKey == key else { return }
        var e = TurnCueEngine(route: route, config: TurnCueConfig(units: currentUnits()))
        // Start from "now" so a fix cached before the route loaded is replayed once.
        lastFixTimestamp = nil
        while !Task.isCancelled {
            poll(&e)
            engine = e
            try? await Task.sleep(for: .seconds(1))
        }
    }

    private func loadRoute(_ key: Key) async -> TurnCueRoute? {
        let cacheKey = "\(key.areaId)|\(key.trailId)"
        if let cached = routeCache[cacheKey] { return cached }
        let area: Area?
        if let cachedArea = areas.cachedArea(id: key.areaId) {
            area = cachedArea
        } else {
            area = await areas.area(id: key.areaId)
        }
        guard let area else {
            coordinatorLog.notice("turn cues idle: area unavailable")
            return nil
        }
        let trails = area.rawTrails ?? area.trails
        guard let selected = trails.first(where: { $0.id == key.trailId }) else {
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
        guard let cue = bannerCue, let engine else { return nil }
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
                speaker.speak(text, priority: priority) { [weak self] in self?.engine?.isStale(event) ?? true }
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
