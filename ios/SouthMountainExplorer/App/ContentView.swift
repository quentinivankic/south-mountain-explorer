import SwiftUI

struct ContentView: View {
    @Environment(AuthService.self) private var auth
    @Environment(RecordingService.self) private var recording
    @Environment(AreaDataService.self) private var areas
    @Environment(ProgressService.self) private var progress
    @Environment(ActivityService.self) private var activity
    @Environment(\.scenePhase) private var scenePhase

    @AppStorage(StorageKeys.onboarded) private var onboarded = false
    @AppStorage(StorageKeys.theme) private var theme: AppTheme = .system

    @State private var showStopConfirm = false
    @State private var showDiscardConfirm = false
    @State private var jumpToAreaId: String? = nil
    /// Last activity-log state we emitted for the app — "active"
    /// or "background". Used to de-dupe scene-phase transitions
    /// (.inactive AND .background both map to background, and the
    /// system can fire several of them per share-sheet present).
    @State private var lastLoggedAppState: String? = nil
    /// Set when the user taps a trail-completion push notification. The
    /// AreaView opened by `jumpToAreaId` reads this to play a one-shot
    /// celebration overlay, then clears itself.
    @State private var celebrationTrailName: String? = nil
    /// DEBUG screenshot support: ensures the `--uitest-open-area`
    /// deep-link fires exactly once.
    @State private var didHandleUITestDeepLink = false

    var body: some View {
        TabView {
            Tab("Explore", systemImage: "mountain.2.fill") {
                HomeView()
            }
            Tab("Browse", systemImage: "magnifyingglass") {
                BrowseView()
            }
            Tab("Stats", systemImage: "chart.line.uptrend.xyaxis") {
                StatsView()
            }
            Tab("Settings", systemImage: "gearshape.fill") {
                SettingsView()
            }
        }
        // iOS 26 — tab bar automatically gets Liquid Glass styling
        .tabViewStyle(.sidebarAdaptable)
        .preferredColorScheme(theme.colorScheme)
        .safeAreaInset(edge: .top, spacing: 0) {
            if let rec = recording.activeRecording {
                ActiveRecordingBanner(
                    areaName: areaName(for: rec.areaId),
                    trailName: trailName(forAreaId: rec.areaId, trailId: rec.trailId),
                    distanceMi: rec.distanceMi,
                    startedAt: rec.startedAt,
                    onTap: { jumpToAreaId = rec.areaId },
                    onStop: { showStopConfirm = true }
                )
            }
        }
        .fullScreenCover(isPresented: Binding(
            get: { jumpToAreaId != nil },
            set: { if !$0 { jumpToAreaId = nil; celebrationTrailName = nil } }
        )) {
            if let id = jumpToAreaId {
                NavigationStack {
                    AreaView(
                        areaId: id,
                        areaName: areaName(for: id),
                        initialCelebrationTrailName: celebrationTrailName
                    )
                }
            }
        }
        .confirmationDialog(
            "Stop this hike?",
            isPresented: $showStopConfirm,
            titleVisibility: .visible
        ) {
            Button("Stop & Save", role: .destructive) {
                Task { await stopActiveRecording() }
            }
            Button("Stop & Discard", role: .destructive) {
                showDiscardConfirm = true
            }
            Button("Keep Recording", role: .cancel) { }
        }
        .confirmationDialog(
            "Discard this hike?",
            isPresented: $showDiscardConfirm,
            titleVisibility: .visible
        ) {
            Button("Discard", role: .destructive) {
                recording.discardRecording()
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("This hike won't be saved to history and your trail coverage won't update. This can't be undone.")
        }
        .fullScreenCover(isPresented: Binding(
            // A writable binding so the dismiss() call inside OnboardingView
            // actually flips `onboarded`. .constant(!onboarded) silently
            // ignores writes, leaving the cover stuck open.
            get: { !onboarded },
            set: { stillShowing in onboarded = !stillShowing }
        )) {
            OnboardingView()
        }
        .task {
            #if DEBUG
            if !didHandleUITestDeepLink, let id = UITestSupport.openAreaId {
                didHandleUITestDeepLink = true
                jumpToAreaId = id
            }
            #endif
            await rebuildCompletionsFromHistory()
            // Background prefetch of favorites + recent areas so the
            // user's saved spots are usable offline. Fire-and-forget —
            // the inner Task outlives this .task block so it keeps
            // running if SwiftUI ever decides to cancel the root task.
            // prefetchOffline short-circuits anything fresher than 24 h
            // so this is cheap on warm caches.
            Task {
                await areas.prefetchOffline()
                // Then sweep a 50 mi radius around the user (Wi-Fi only,
                // skipped if we already prefetched within 25 mi of
                // current location). Runs after prefetchOffline so
                // favorites/recents get priority on metered situations
                // where the radius sweep is skipped.
                await areas.runNearbyPrefetchIfAppropriate()
            }
        }
        // Track foreground sessions for engagement telemetry. .active fires
        // on initial launch and on every return from background; .inactive
        // / .background fires when the app loses foreground (incl. when
        // killed). endSession is a no-op if no start has been recorded.
        .onChange(of: scenePhase, initial: true) { _, newPhase in
            // Activity-log de-dupe: only log on real transitions
            // (active ↔ background). `initial: true` fires on
            // cold launch with whatever scene phase we land in,
            // and share-sheet presents bounce through .inactive +
            // .background several times — each transition would
            // otherwise log a redundant entry.
            let nextState: String?
            switch newPhase {
            case .active: nextState = "active"
            case .inactive, .background: nextState = "background"
            @unknown default: nextState = nil
            }
            switch newPhase {
            case .active:
                activity.startSession()
                if nextState != lastLoggedAppState {
                    ActivityLogService.shared.log(category: "app", action: "foreground")
                    lastLoggedAppState = nextState
                }
                // Re-evaluate the nearby prefetch on every foreground
                // entry — covers the "user moved 30+ mi between
                // sessions" case. The orchestrator's movement check
                // makes this a cheap no-op when the user hasn't moved.
                Task { await areas.runNearbyPrefetchIfAppropriate() }
            case .inactive, .background:
                activity.endSession()
                if nextState != lastLoggedAppState {
                    ActivityLogService.shared.log(category: "app", action: "background")
                    lastLoggedAppState = nextState
                }
                // Flush pending log writes so foregrounded entries
                // don't get lost if the app is later killed.
                ActivityLogService.shared.flush()
            @unknown default: break
            }
        }
        // Notification-tap deep-link. Set the celebration name first so
        // AreaView reads it on its first .task, then trigger the cover.
        .onReceive(NotificationCenter.default.publisher(for: NotificationService.celebrateNotification)) { msg in
            guard
                let info = msg.userInfo,
                let areaId = info["areaId"] as? String,
                let trailName = info["trailName"] as? String
            else { return }
            celebrationTrailName = trailName
            jumpToAreaId = areaId
        }
    }

    /// Run once at app launch: scan recorded hike history and re-stamp every
    /// trail completion into ProgressService. Without this, AreaCards on the
    /// Explore tab read 0/N until the user opens the area — only AreaView's
    /// own per-load history scan was populating ProgressService before.
    /// bulkMarkComplete is silent + idempotent, so re-running on every launch
    /// is fine.
    private func rebuildCompletionsFromHistory() async {
        let history = await recording.loadHistory()
        let byArea = Dictionary(grouping: history, by: { $0.areaId })
        for (areaId, hikes) in byArea {
            let trailIds = Set(hikes.flatMap { $0.completedTrailIds })
            progress.bulkMarkComplete(areaId: areaId, trailIds: trailIds)
        }
    }

    /// Best-effort name resolution: cached Area first (has full trails),
    /// then the lighter AreaSummary list, then a generic fallback. Keeps the
    /// banner readable even before AreaDataService has hydrated the cache.
    private func areaName(for id: String) -> String {
        if let cached = areas.cachedArea(id: id)?.name { return cached }
        if let summary = areas.summaries.first(where: { $0.id == id })?.name { return summary }
        return "Hiking"
    }

    /// Resolve the trail name for trail-mode recordings so the banner can
    /// promote it to the primary label. Returns nil when the recording is
    /// in roam mode or the area's trails aren't cached yet.
    private func trailName(forAreaId areaId: String, trailId: String?) -> String? {
        guard let trailId else { return nil }
        return areas.cachedArea(id: areaId)?.trails.first { $0.id == trailId }?.name
    }

    private func stopActiveRecording() async {
        guard let rec = recording.activeRecording else { return }
        // Pull trails from cache so coverage merges still work; fall back to
        // an async fetch if the area hasn't been opened this session.
        // (Split into an if/else because `??` takes an autoclosure that
        // can't host an `await`.)
        let trails: [Trail]
        if let cached = areas.cachedArea(id: rec.areaId) {
            trails = cached.trails
        } else {
            trails = (await areas.area(id: rec.areaId))?.trails ?? []
        }
        _ = await recording.stopRecording(trails: trails)
    }
}
