import SwiftUI

/// Tab identity for the root TabView's selection binding. Exists so
/// ContentView can observe tab taps — the Browse tab focuses its search
/// field on every tap of the tab-bar icon (see the selection Binding).
enum AppTab: Hashable {
    case explore, browse, stats, settings
}

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
    @State private var selectedTab: AppTab = .explore
    /// Banner-tap route for an in-progress walk (walks reopen WalkView,
    /// not the primary area's AreaView).
    @State private var showWalkCover = false

    var body: some View {
        // Custom selection binding so we see EVERY tap on a tab icon —
        // including re-taps of the already-selected tab, which write the
        // same value through the setter. Tapping the Browse (search) icon
        // should always pop the keyboard, whether it switches tabs or not.
        TabView(selection: Binding(
            get: { selectedTab },
            set: { tab in
                if tab == .browse {
                    NotificationCenter.default.post(name: .browseSearchTabTapped, object: nil)
                }
                selectedTab = tab
            }
        )) {
            Tab("Explore", systemImage: "mountain.2.fill", value: AppTab.explore) {
                HomeView()
            }
            Tab("Browse", systemImage: "magnifyingglass", value: AppTab.browse) {
                BrowseView()
            }
            Tab("Stats", systemImage: "chart.line.uptrend.xyaxis", value: AppTab.stats) {
                StatsView()
            }
            Tab("Settings", systemImage: "gearshape.fill", value: AppTab.settings) {
                SettingsView()
            }
        }
        // iOS 26 — tab bar automatically gets Liquid Glass styling
        .tabViewStyle(.sidebarAdaptable)
        .preferredColorScheme(theme.colorScheme)
        .safeAreaInset(edge: .top, spacing: 0) {
            if let rec = recording.activeRecording {
                ActiveRecordingBanner(
                    // Walks aren't "in" an area — title the banner Walk
                    // and put the primary area in the subtitle slot.
                    areaName: rec.mode == .walk ? "Walk" : areaName(for: rec.areaId),
                    trailName: trailName(forAreaId: rec.areaId, trailId: rec.trailId),
                    distanceMi: rec.distanceMi,
                    startedAt: rec.startedAt,
                    onTap: {
                        if rec.mode == .walk {
                            showWalkCover = true
                        } else {
                            jumpToAreaId = rec.areaId
                        }
                    },
                    onStop: { showStopConfirm = true }
                )
            }
        }
        // Warm the trail-shape thumbnails in the background at launch (while
        // the user is in onboarding / browsing), off the search critical path.
        .task { await TrailShapeService.shared.loadIfNeeded() }
        // Same for the global parking pool (0.33 MB, ETag-revalidated). Warmed at
        // the TAB level, not in AreaView, because WalkView draws parking too and a
        // user can reach a walk without opening an area first.
        .task { await ParkingPoolService.shared.loadIfNeeded() }
        // Banner tap for an in-progress WALK reopens the walk screen
        // (which restores from the recording's own nearby-area list)
        // instead of the primary area's AreaView.
        .fullScreenCover(isPresented: $showWalkCover) {
            WalkView()
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
            // Auto-upload the backup bundle to the private tailnet endpoint on
            // foreground when the Developer toggle is on. Self-gating: a no-op
            // unless this is a TestFlight build AND the toggle is set (see
            // DebugDiagSync), so it never runs in an App Store production install.
            if newPhase == .active { DebugDiagSync.uploadIfEnabled() }
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
        // Out-of-region "look around" — the WaitlistCard jumps the user
        // into the served parks list (Browse) so the app isn't empty for them.
        .onReceive(NotificationCenter.default.publisher(for: .showBrowseTab)) { _ in
            selectedTab = .browse
        }
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
        // Walk-aware: a walk's per-area credits live in
        // multiAreaCompletions (its flat completedTrailIds only mirror
        // the primary area), so accumulate per (record, touched area)
        // via the walk-aware accessor. Regular hikes resolve to their
        // single areaId exactly as before.
        var byArea: [String: Set<String>] = [:]
        for hike in history {
            for areaId in hike.touchedAreaIds {
                let ids = hike.completedTrailIds(in: areaId)
                if !ids.isEmpty {
                    byArea[areaId, default: []].formUnion(ids)
                }
            }
        }
        for (areaId, trailIds) in byArea {
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
        // Walks stop through the multi-area path: gather every nearby
        // area's dense geometry so each one gets its coverage credit.
        if rec.mode == .walk {
            var trailsByArea: [String: [Trail]] = [:]
            for areaId in rec.nearbyAreaIds ?? [rec.areaId] {
                // if/else, not `??` — its autoclosure can't host an await.
                let area: Area?
                if let cached = areas.cachedArea(id: areaId) {
                    area = cached
                } else {
                    area = await areas.area(id: areaId)
                }
                if let area {
                    trailsByArea[areaId] = area.rawTrails ?? area.trails
                }
            }
            _ = await recording.stopWalk(trailsByArea: trailsByArea)
            return
        }
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
