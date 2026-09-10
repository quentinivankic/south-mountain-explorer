import SwiftUI

/// Tab identity for the root TabView's selection binding. Exists so
/// ContentView can observe tab taps — the Browse tab focuses its search
/// field on every tap of the tab-bar icon (see the selection Binding).
enum AppTab: Hashable {
    case explore, browse, stats, settings
}

/// One presentation event for an AreaView opened outside normal Browse
/// navigation. A fresh identity forces SwiftUI to discard any prior area's
/// local state when consecutive notification taps arrive while the cover is
/// already presented.
private struct AreaJumpRoute: Identifiable {
    let id = UUID()
    let areaId: String
    let trailId: String?
    let trailName: String?
}

/// One root presentation route for a successfully saved recording. It owns the
/// exact area context used for stop computation so the summary never has to
/// rehydrate data after the active checkpoint has been cleared.
private enum RootRecordingSummaryRoute: Identifiable {
    case hike(
        id: UUID,
        finished: FinishedRecording,
        areaName: String,
        trails: [Trail]
    )
    case walk(
        id: UUID,
        finished: FinishedRecording,
        areas: [Area]
    )

    var id: UUID {
        switch self {
        case .hike(let id, _, _, _), .walk(let id, _, _): return id
        }
    }
}

struct ContentView: View {
    @Environment(AuthService.self) private var auth
    @Environment(RecordingService.self) private var recording
    @Environment(AreaDataService.self) private var areas
    @Environment(ProgressService.self) private var progress
    @Environment(ActivityService.self) private var activity
    @Environment(\.scenePhase) private var scenePhase

    @AppStorage(StorageKeys.onboarded) private var onboarded = false

    @State private var showStopConfirm = false
    @State private var showDiscardConfirm = false
    @State private var saveFailureMessage: String? = nil
    @State private var recordingSummaryRoute: RootRecordingSummaryRoute? = nil
    /// RecordingService does not enter its own stopping state until after area
    /// hydration. This root guard closes that pre-hydration duplicate-tap gap.
    @State private var isRootStopInFlight = false
    @State private var areaJumpRoute: AreaJumpRoute? = nil
    /// Last activity-log state we emitted for the app — "active"
    /// or "background". Used to de-dupe scene-phase transitions
    /// (.inactive AND .background both map to background, and the
    /// system can fire several of them per share-sheet present).
    @State private var lastLoggedAppState: String? = nil
    @State private var selectedTab: AppTab = .explore
    /// Banner-tap route for an in-progress walk (walks reopen WalkView,
    /// not the primary area's AreaView).
    @State private var showWalkCover = false

    var body: some View {
        // Onboarding is an OVERLAY, not a presentation. It used to be a third
        // `.fullScreenCover` stacked on the same TabView as the walk cover and
        // the jump-to-area cover, and it never appeared on a clean install —
        // reproduced on a fresh simulator by `OnboardingAuditTests`, which
        // logged `onboardingVisible=false` with the app sitting on Explore.
        // SwiftUI arbitrates between presentations attached to one view; a
        // plain conditional does not, so this branch cannot be out-voted.
        ZStack {
            tabs
                .accessibilityHidden(!onboarded)

            if !onboarded {
                OnboardingView(onFinish: { onboarded = true })
                    .background(Color(.systemBackground).ignoresSafeArea())
                    .transition(.opacity)
                    .zIndex(1)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: onboarded)
    }

    private var tabs: some View {
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
        // Dark mode only for now. The app's map, cyan completion colour and
        // silhouette art are all tuned for a dark ground, so the light variant
        // was the weaker half of a choice nobody needed to make. The Settings
        // Theme picker is gone with it; AppTheme + StorageKeys.theme remain so
        // existing stored values decode harmlessly.
        .preferredColorScheme(.dark)
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
                            // Active-recording navigation is area-only; a
                            // notification trail identity must never leak in.
                            areaJumpRoute = AreaJumpRoute(
                                areaId: rec.areaId,
                                trailId: nil,
                                trailName: nil
                            )
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
        .fullScreenCover(item: $areaJumpRoute) { route in
            NavigationStack {
                AreaView(
                    areaId: route.areaId,
                    areaName: areaName(for: route.areaId),
                    initialCelebrationTrailName: route.trailName,
                    initialSelectedTrailId: route.trailId,
                    initialSelectedTrailName: route.trailName
                )
                .id(route.id)
            }
        }
        .sheet(item: $recordingSummaryRoute) { route in
            switch route {
            case let .hike(_, finished, areaName, trails):
                RecordingSummarySheet(
                    finished: finished,
                    areaName: areaName,
                    trails: trails
                )
            case let .walk(_, finished, loadedAreas):
                WalkSummarySheet(finished: finished, walkAreas: loadedAreas)
            }
        }
        .confirmationDialog(
            "Stop this \(activeActivityName)?",
            isPresented: $showStopConfirm,
            titleVisibility: .visible
        ) {
            Button("Stop & Save") {
                Task { await stopActiveRecording() }
            }
            Button("Stop & Discard", role: .destructive) {
                showDiscardConfirm = true
            }
            Button(keepActivityLabel, role: .cancel) { }
        }
        .confirmationDialog(
            "Discard this \(activeActivityName)?",
            isPresented: $showDiscardConfirm,
            titleVisibility: .visible
        ) {
            Button("Discard", role: .destructive) {
                recording.discardRecording()
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("This \(activeActivityName) won't be saved to history and your trail coverage won't update. This can't be undone.")
        }
        .alert(
            activeRecordingIsWalk ? "Couldn't Save Walk" : "Couldn't Save Hike",
            isPresented: Binding(
                get: { saveFailureMessage != nil },
                set: { if !$0 { saveFailureMessage = nil } }
            )
        ) {
            Button("Retry Save") { Task { await stopActiveRecording() } }
            Button(keepActivityLabel, role: .cancel) { }
        } message: {
            Text(saveFailureMessage ?? "Your active recording is still safe and location observation has resumed.")
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
                let trailId = info["trailId"] as? String
            else { return }
            let trailName = info["trailName"] as? String
            // Name is present on current local notifications but optional for
            // older/local callers. Without it the resolver permits only a
            // unique exact-ID match. Every event gets a fresh route identity,
            // so a second tap cannot reuse the prior AreaView's selection.
            areaJumpRoute = AreaJumpRoute(
                areaId: areaId,
                trailId: trailId,
                trailName: trailName
            )
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

    private var activeRecordingIsWalk: Bool {
        recording.activeRecording?.mode == .walk
    }

    private var activeActivityName: String {
        activeRecordingIsWalk ? "walk" : "hike"
    }

    private var keepActivityLabel: String {
        activeRecordingIsWalk ? "Keep Walking" : "Keep Recording"
    }

    private func loadedArea(id: String) async -> Area? {
        if let cached = areas.cachedArea(id: id) { return cached }
        return await areas.area(id: id)
    }

    private func activeSessionMatches(_ captured: ActiveRecording) -> Bool {
        guard let current = recording.activeRecording else { return false }
        return current.recordingId == captured.recordingId
            && current.startedAt == captured.startedAt
            && current.areaId == captured.areaId
            && current.mode == captured.mode
    }

    private func stopActiveRecording() async {
        guard !isRootStopInFlight,
              recordingSummaryRoute == nil,
              let rec = recording.activeRecording,
              !recording.isStopping
        else { return }

        // This must flip before the first area-loading await. RecordingService's
        // own guard starts later, once stopRecording/stopWalk is entered.
        isRootStopInFlight = true
        saveFailureMessage = nil
        defer { isRootStopInFlight = false }

        do {
            if rec.mode == .walk {
                // Stable de-dupe preserves the recording's captured area order.
                // Ensure legacy/incomplete checkpoints still include primary.
                var sourceIds = rec.nearbyAreaIds ?? []
                if sourceIds.isEmpty {
                    sourceIds = [rec.areaId]
                } else if !sourceIds.contains(rec.areaId) {
                    sourceIds.append(rec.areaId)
                }
                var seen: Set<String> = []
                let areaIds = sourceIds.filter { seen.insert($0).inserted }

                var loadedAreas: [Area] = []
                for areaId in areaIds {
                    guard let area = await loadedArea(id: areaId) else {
                        saveFailureMessage = "TrekDex couldn't load all nearby trail data. Check your connection and retry saving your walk."
                        return
                    }
                    loadedAreas.append(area)
                }

                // Hydration suspended above; never let this task stop a session
                // that replaced the one whose confirmation the user accepted.
                guard activeSessionMatches(rec) else {
                    saveFailureMessage = "The active recording changed before TrekDex could save it. Review the current recording and try again."
                    return
                }

                let trailsByArea = Dictionary(
                    loadedAreas.map { ($0.id, $0.rawTrails ?? $0.trails) },
                    uniquingKeysWith: { first, _ in first }
                )
                guard let finished = try await recording.stopWalk(trailsByArea: trailsByArea) else {
                    return
                }

                // Let the confirmation dismissal finish before presenting the
                // summary. This is the same proven delay as WalkView's stop path.
                try? await Task.sleep(for: .milliseconds(400))
                recordingSummaryRoute = .walk(
                    id: UUID(),
                    finished: finished,
                    areas: loadedAreas
                )
                return
            }

            guard let area = await loadedArea(id: rec.areaId) else {
                saveFailureMessage = "TrekDex couldn't load this area's trail data. Check your connection and retry saving your hike."
                return
            }
            guard activeSessionMatches(rec) else {
                saveFailureMessage = "The active recording changed before TrekDex could save it. Review the current recording and try again."
                return
            }
            guard let finished = try await recording.stopRecording(
                trails: area.rawTrails ?? area.trails
            ) else { return }

            try? await Task.sleep(for: .milliseconds(400))
            recordingSummaryRoute = .hike(
                id: UUID(),
                finished: finished,
                areaName: area.name,
                trails: area.trails
            )
        } catch {
            saveFailureMessage = error.localizedDescription
        }
    }
}
