import SwiftUI
import OSLog

/// Logger for `AreaView` lifecycle and decision events — area
/// loads, suggestion-banner mounts, trail-completion celebrations.
/// Lands in the Send Diagnostics bundle so a field report carries
/// the sequence of UI events the user saw, not just the recording
/// state.
private let log = Logger(subsystem: "com.trekdex.app", category: "area")

private let farFromAreaThresholdMi = 5.0

/// Segments of the area sheet. `trails` is the original trail
/// list + map controls; `dex` is the achievements grid.
private enum AreaSheetTab: Hashable {
    case trails
    case dex
}

struct AreaView: View {
    let areaId: String
    let areaName: String
    /// When set on init, the view plays a one-shot trail-completion
    /// celebration overlay on first appear. Used by the notification-tap
    /// deep-link from ContentView so a user opening the "Trail Complete!"
    /// notification gets a celebratory beat instead of a silent jump in.
    var initialCelebrationTrailName: String? = nil
    /// Set when the view is opened from a trail search result — the
    /// trail is pre-selected once the area loads, so the map highlights
    /// it and the trail list scrolls it into view.
    var initialSelectedTrailId: String? = nil

    @Environment(AreaDataService.self) private var areas
    @Environment(AreaSilhouetteService.self) private var silhouettes
    @Environment(RecordingService.self) private var recording
    @Environment(FavoritesService.self) private var favorites
    @Environment(LocationService.self) private var location
    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(ActivityService.self) private var activity
    @Environment(\.dismiss) private var dismiss
    /// Map style binding lives on AreaView so the user can flip
    /// it from the per-map "•••" menu rather than digging into
    /// Settings. Same `@AppStorage` key MapKitMapView reads, so
    /// changes propagate immediately to the open map.
    @AppStorage(StorageKeys.mapStyle) private var mapStyle: MapStylePreference = .standard
    @AppStorage(StorageKeys.showAllParking) private var showAllParking = false
    /// Units preference so the header's area total renders in mi/km and
    /// reacts to the Settings toggle. Was previously hardcoded to miles.
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var area: Area? = nil
    @State private var isLoading = true
    @State private var loadError: String? = nil
    /// Loading view always plays for at least 1.5 s so the silhouette
    /// reveal animation finishes even when real data lands in under
    /// half a second. Flipped by a task timer that starts on
    /// `.task(id: areaId)` and ends 1.5 s later.
    @State private var minLoadingTimeElapsed = false

    /// Trail-list sheet detents — TWO stops, deliberately.
    ///   - medium: the default. A device-relative fraction so it shows a
    ///     comparable number of trail rows on a small iPhone SE and a
    ///     Pro Max, rather than a fixed 340pt that's "half the list"
    ///     on one and "three rows" on the other.
    ///   - large: system `.large` (~almost full screen).
    ///
    /// A third 150pt "peek" detent used to sit below these. It was removed
    /// because a third stop made the sheet land somewhere unintended mid-drag
    /// and took two gestures to get between list and map.
    static let mediumDetent: PresentationDetent = .fraction(0.5)

    /// Currently-active detent of the trail-list sheet. Drives
    /// `effectiveBottomInset` so the map's user-dot shift compensates
    /// for whatever portion of the screen the sheet covers.
    ///
    /// Replaces the previous custom drag implementation (showTrailList
    /// + trailListHeight + DragGesture + per-frame height @State) that
    /// was burning frames on every drag tick. The native sheet drags
    /// in UIKit, so SwiftUI's body never re-evaluates for the gesture
    /// itself — only when the detent SETTLES (at most once per
    /// release).
    @State private var sheetDetent: PresentationDetent = AreaView.mediumDetent
    /// Which segment of the area sheet is showing — trail list or Dex.
    @State private var sheetTab: AreaSheetTab = .trails
    @State private var selectedTrailId: String? = nil
    /// Trail being reported via the overflow menu — drives the report sheet.
    @State private var reportingTrail: Trail? = nil
    /// Per-recording-session set of trail ids the user has
    /// dismissed from the suggestion banner. Prevents the same
    /// "Add Bajada Trail" pill from re-appearing five seconds
    /// after the user × it. Cleared whenever the active recording
    /// goes nil (a new recording starts fresh).
    @State private var dismissedSuggestionIds: Set<String> = []
    @State private var finishedRecording: FinishedRecording? = nil
    @State private var showSummary = false
    @State private var showAreaComplete = false
    /// Past hikes in this area, with timestamps so TrailMapView can
    /// filter "walked since last completion" for the orange overlay.
    /// Previously this was just `pastPaths: [[GpsPoint]]`; the
    /// halo render only needs paths but the overlay needs dates.
    @State private var pastHikes: [PastHike] = []
    @State private var recenterTick: Int = 0
    /// Bumped when the user taps Switch on the retarget or
    /// suggestion banner. Tells `TrailMapView` to re-fit the camera
    /// around the new active trail PLUS the user's current
    /// location. We need a separate signal from `selectedTrailId`
    /// because the banner shows up precisely because the user
    /// already tapped a different trail — i.e. `selectedTrailId`
    /// is ALREADY pointing at the new trail by the time Switch is
    /// tapped, so SwiftUI's `.onChange(of:)` would not fire.
    @State private var centerOnSwitchedTrailTick: Int = 0
    /// Owns the camera tracking cycle for the map. The rotation button
    /// in `controlBar` cycles this; TrailMapView observes via Binding
    /// and swaps `MapCameraPosition` accordingly. Tapping the recenter
    /// button forces this back to `.free` so a one-shot recenter isn't
    /// immediately undone by re-engaged tracking.
    @State private var trackingMode: MapTrackingMode = .free
    /// Ephemeral hint that pops above the controlBar when the user
    /// taps the rotation cycle button. Set to the new mode's
    /// `toastLabel`; auto-clears after ~2 s. Self-documents the
    /// otherwise-cryptic three-state cycle.
    @State private var trackingModeToast: String? = nil
    @State private var trackingModeToastTask: Task<Void, Never>? = nil

    // Pre-flight checks before kicking off a recording.
    @State private var showConflictAlert = false
    @State private var conflictAreaName: String = ""
    @State private var showFarWarning = false
    @State private var farDistanceMi: Double = 0
    /// Captured by tryStartRecording when a confirmation dialog interrupts
    /// the start. The dialog's "proceed" button reads this so a trail-mode
    /// request survives the round-trip — without it, "Start Anyway" /
    /// "Stop & Start Here" silently downgraded to .roam mode and the
    /// recording-trail highlight never engaged.
    @State private var pendingRecordTrailId: String? = nil
    /// Name of the trail to celebrate over the map. Auto-clears after a
    /// short delay so the overlay doesn't sit forever.
    @State private var celebrationTrailName: String? = nil
    /// Half-sheet that presents the area-wide multi-track GPX
    /// share. Wraps URL because URL itself isn't Identifiable.
    @State private var areaGpxShareURL: IdentifiedURL? = nil

    // Trail-list filters live up here so the map and the list share the
    // same source of truth — flipping a filter hides the corresponding
    // polylines from the map too, not just the list rows.
    @State private var statusFilter: TrailStatusFilter = .all
    @State private var difficultyFilter: TrailDifficultyFilter = .all
    @State private var lengthFilter: TrailLengthFilter = .all
    @State private var routeFilter: TrailRouteFilter = .all
    /// Free-text search over trail names. Lives alongside the
    /// existing filter state so the filtered-trails computed
    /// property can fold it into a single pass.
    @State private var trailSearchQuery: String = ""

    private var isRecording: Bool {
        // Walks are excluded even when their primary area is this one:
        // AreaView's RecordingPanel stops through the single-area path,
        // which would save the walk as a plain hike and throw away its
        // multi-area credits. Walks render their own panel in WalkView.
        guard let rec = recording.activeRecording else { return false }
        return rec.areaId == areaId && rec.mode != .walk
    }

    /// Trail set after applying the user's filters. Single source of
    /// truth shared between TrailListView (which renders the rows) and
    /// TrailMapView (which renders the polylines).
    private func computeFilteredTrails(_ area: Area) -> [Trail] {
        area.trails.filter { trail in
            let isComplete = progress.isComplete(trail, areaId: areaId)
            switch statusFilter {
            case .all: break
            case .incomplete: if isComplete { return false }
            case .complete:   if !isComplete { return false }
            }
            if !difficultyFilter.matches(trail.difficulty) { return false }
            if !lengthFilter.matches(trail.distanceMi) { return false }
            if !routeFilter.matches(trail.routeType) { return false }
            let q = trailSearchQuery.trimmingCharacters(in: .whitespaces)
            if !q.isEmpty && !trail.name.localizedCaseInsensitiveContains(q) {
                return false
            }
            return true
        }
    }

    /// Cached output of `computeFilteredTrails` so the trail-list
    /// drag (which thrashes `trailListHeight` at the display's
    /// frame rate and forces this body to re-evaluate) doesn't
    /// re-run the O(N trails) filter on every frame. Recomputed
    /// only on the actual inputs via `.onChange(of: filterKey)`
    /// below. Same story for `visibleTrailIds`, which would also
    /// allocate a fresh Set every body eval otherwise — and that
    /// Set drives a `lastVisibleTrailIds != visibleTrailIds` check
    /// inside MapKitMapView.updateUIView, so a fresh instance each
    /// frame triggered repeated Set comparisons on the map side too.
    @State private var filtered: [Trail] = []
    @State private var visibleTrailIds: Set<String>? = nil

    /// Cached `Set` of valid trail IDs for the loaded area. Used by
    /// `filteredCompletedCount`, which previously allocated this Set
    /// inline every body eval — and was fed into a `.onChange` that
    /// SwiftUI evaluates on every body pass, so it allocated 60-120 ×/sec
    /// during the trail-list drag. Recomputed only when the area's
    /// trail set actually changes (rare — only on initial load).
    @State private var areaTrailIds: Set<String> = []

    /// Inputs that change `filtered`. Bundled into a single Equatable
    /// value so a single `.onChange` covers all of them. Per-area
    /// completion count is observed separately (it's an `Int` so the
    /// comparison stays O(1) per body eval, vs. comparing the full
    /// completions dictionary which would be O(N)).
    private struct FilterKey: Equatable {
        let areaId: String
        let statusFilter: TrailStatusFilter
        let difficultyFilter: TrailDifficultyFilter
        let lengthFilter: TrailLengthFilter
        let routeFilter: TrailRouteFilter
        let searchQuery: String
    }

    private var filterKey: FilterKey {
        FilterKey(
            areaId: area?.id ?? "",
            statusFilter: statusFilter,
            difficultyFilter: difficultyFilter,
            lengthFilter: lengthFilter,
            routeFilter: routeFilter,
            searchQuery: trailSearchQuery
        )
    }

    private var areaCompletionsCount: Int {
        progress.completions[areaId]?.count ?? 0
    }

    private func recomputeFiltered() {
        guard let area else {
            filtered = []
            visibleTrailIds = nil
            areaTrailIds = []
            return
        }
        filtered = computeFilteredTrails(area)
        visibleTrailIds = hasActiveFilter ? Set(filtered.map(\.id)) : nil
        // Same area? Skip the Set rebuild. Trail list within an area
        // is stable for the area's lifetime.
        if areaTrailIds.isEmpty || areaTrailIds.count != area.trails.count {
            areaTrailIds = Set(area.trails.map(\.id))
        }
    }

    /// Whether any non-default filter is active. When false we pass
    /// `nil` to TrailMapView so it skips the per-trail filter check
    /// entirely, since the unfiltered render is the common case.
    private var hasActiveFilter: Bool {
        statusFilter != .all
            || difficultyFilter != .all
            || lengthFilter != .all
            || routeFilter != .all
            || !trailSearchQuery.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            if let area, minLoadingTimeElapsed {
                // Full-screen map. The trail-list sheet (presented
                // via .sheet below) covers the bottom portion and
                // is system-native, so we hand TrailMapView the
                // current sheet detent's height as `bottomInset` and
                // it shifts the user dot upward to clear the visible
                // sheet area.
                TrailMapView(
                    area: area,
                    activeRecording: isRecording ? recording.activeRecording : nil,
                    pastHikes: pastHikes,
                    recenterTick: recenterTick,
                    centerOnSwitchedTrailTick: centerOnSwitchedTrailTick,
                    selectedTrailId: $selectedTrailId,
                    visibleTrailIds: visibleTrailIds,
                    bottomInset: effectiveBottomInset,
                    trackingMode: $trackingMode
                )
                .ignoresSafeArea()

            } else if isLoading || (area != nil && !minLoadingTimeElapsed) {
                loadingState
            } else {
                ContentUnavailableView("Area Unavailable",
                    systemImage: "xmark.octagon",
                    description: Text(loadError ?? "Could not load trail data. Check your connection."))
            }
        }
        .sheet(isPresented: trailSheetPresented) {
            // Trail-list sheet. Presented as soon as the area has
            // loaded (so we never flash an empty sheet during the
            // loading silhouette animation) and never dismissed
            // afterward — `interactiveDismissDisabled` blocks the
            // swipe-to-dismiss, so the user can't accidentally
            // close it. They can drag down to the small peek detent for a
            // near-full-map view instead.
            //
            // This replaces the previous hand-rolled bottom panel
            // (DragGesture + per-frame @State + Material backdrop
            // re-rendering at varying size) — that custom path was
            // dropping frames every drag because the resize cascaded
            // into AreaView.body re-evals, MapKitMapView.updateUIView
            // calls, and Material blur rerenders. UISheetPresentation-
            // Controller handles all of that natively in UIKit /
            // Core Animation; SwiftUI only re-renders when the detent
            // SETTLES (at most once per release), not per drag tick.
            if let area {
                sheetContent(area: area)
                    // TWO detents only — the default half-height and full
                    // screen. The 150pt peek detent was removed: it added a
                    // third stop the sheet could land on mid-drag, so getting
                    // between "list" and "map" took two gestures and often
                    // settled somewhere the user didn't want.
                    .presentationDetents(
                        [Self.mediumDetent, .large],
                        selection: $sheetDetent
                    )
                    .presentationDragIndicator(.visible)
                    .presentationBackgroundInteraction(.enabled(upThrough: Self.mediumDetent))
                    .presentationContentInteraction(.scrolls)
                    .presentationCornerRadius(20)
                    // Opaque system background at EVERY detent. By
                    // default the sheet is translucent (glass) at the
                    // small / medium detents and only goes opaque at
                    // .large — which is why the controls read as
                    // "glass on glass" until you expand it. Forcing
                    // the solid background everywhere removes the
                    // sheet's own glass layer entirely, so the inner
                    // controls sit on a plain surface (the look the
                    // user wanted at all heights, not just full-screen).
                    .presentationBackground(Color(.systemBackground))
                    .interactiveDismissDisabled()
            }
        }
        .navigationBarHidden(true)
        .overlay(alignment: .top) {
            HStack {
                Button {
                    ActivityLogService.shared.log(
                        category: "area",
                        action: "closed",
                        context: ["areaId": areaId]
                    )
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .compatibleGlass(in: .circle)
                }
                .accessibilityIdentifier("area-close-button")
                Spacer()
                // Area-level overflow menu. Currently hosts only
                // "Export All Trails as GPX" but the chrome is
                // sized to grow as more area-wide actions land
                // (Stats / heatmap export, area download, etc.).
                Menu {
                    Picker("Map Style", selection: $mapStyle) {
                        ForEach(MapStylePreference.allCases) { style in
                            Text(style.label).tag(style)
                        }
                    }
                    // Parking is normally gated to the selected trail (#429) so
                    // a busy map stays readable. That gate also makes an area's
                    // parking undiscoverable: Helena-Lewis and Clark NF has 31
                    // lots, but only 16% of its trails have one within the 805 m
                    // endpoint radius, so browsing trail by trail reads as "no
                    // parking here". This answers the area-level question
                    // without loosening the per-trail association.
                    Toggle(isOn: $showAllParking) {
                        Label("Show All Parking", systemImage: "parkingsign.circle")
                    }
                    .disabled((area?.parking?.isEmpty ?? true))
                    Divider()
                    // Selection-aware GPX export: with a trail selected in the
                    // list, export just that one trail (small, what people
                    // actually want); otherwise export the whole area as a
                    // multi-track GPX (the bulk/backup path).
                    if let area, let tid = selectedTrailId,
                       let trail = area.trails.first(where: { $0.id == tid }) {
                        Button {
                            exportTrailGpx(trail)
                        } label: {
                            Label("Export \u{201C}\(trail.name)\u{201D} as GPX",
                                  systemImage: "square.and.arrow.up")
                        }
                        Button {
                            reportingTrail = trail
                        } label: {
                            Label("Report a problem with this trail",
                                  systemImage: "exclamationmark.bubble")
                        }
                    } else {
                        Button {
                            exportAreaGpx()
                        } label: {
                            Label("Export All Trails as GPX", systemImage: "square.and.arrow.up")
                        }
                        .disabled(area == nil)
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .compatibleGlass(in: .circle)
                }
                Button {
                    Task { await favorites.toggle(areaId: areaId) }
                } label: {
                    Image(systemName: favorites.isFavorite(areaId) ? "heart.fill" : "heart")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .compatibleGlass(in: .circle)
                        .foregroundStyle(favorites.isFavorite(areaId) ? .red : .primary)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 8)
            .safeAreaPadding(.top)
        }
        .task {
            // Telemetry: log "user opened this area" so we can later
            // surface "you haven't visited X in a while" reminders.
            activity.recordAreaOpened(areaId)
            ActivityLogService.shared.log(
                category: "area",
                action: "opened",
                context: ["areaId": areaId]
            )
            AnalyticsService.shared.capture(.areaOpened(areaId: areaId))
            let result = await areas.areaWithError(id: areaId)
            area = result.area
            loadError = result.error
            isLoading = false
            if let loadedArea = result.area {
                // Backfill the completion fingerprint index from this area's
                // geometry, so progress recorded here credits a duplicate-area
                // twin, and a twin's progress credits here. Cheap + idempotent.
                progress.indexArea(areaId: areaId, trails: loadedArea.trails)
                log.notice("areaOpened areaId=\(self.areaId, privacy: .public) trails=\(loadedArea.trails.count) rawTrails=\(loadedArea.rawTrails?.count ?? 0) parking=\(loadedArea.parking?.count ?? -1)")
            } else if let err = result.error {
                log.error("areaOpenFailed areaId=\(self.areaId, privacy: .public) error=\(err, privacy: .public)")
            }
            await loadHistoryDerivedState()
            // Pop the celebration overlay if the view was opened via a
            // trail-complete push notification. Done after the area loads
            // so the overlay sits over the map, not a spinner.
            if let name = initialCelebrationTrailName {
                showCelebration(name: name)
            }
            // Trail-search deep link: highlight the searched trail once
            // the trail data is in. Only on first load (selection nil)
            // so a user's own subsequent selection isn't overridden.
            if let tid = initialSelectedTrailId, selectedTrailId == nil,
               result.area?.trails.contains(where: { $0.id == tid }) == true {
                selectedTrailId = tid
            }
        }
        .task(id: areaId) {
            // Floor the loading view at 1.5 s so the silhouette reveal
            // animation always completes — even when the area's data is
            // already on disk and lands in microseconds.
            minLoadingTimeElapsed = false
            try? await Task.sleep(for: .seconds(1.5))
            minLoadingTimeElapsed = true
        }
        .onChange(of: isRecording) { _, recordingNow in
            // Each new recording session starts with a clean slate of
            // suggestion dismissals — the user's "× this" from a
            // previous hike shouldn't suppress the same trail forever.
            if !recordingNow {
                dismissedSuggestionIds.removeAll()
            }
        }
        .task(id: isRecording) {
            // While a recording is active for this area, recompute coverage
            // every 30s so partial progress visibly fills in (trail-list
            // progress bars tick up, trails crossing 90% turn cyan live).
            guard isRecording else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled, isRecording, let area else { break }
                // Coverage measurement uses the raw (pre-decimation)
                // trail node set when available — decimation drops the
                // node-count denominator in the fraction calc and
                // inflates coverage, so prefer rawTrails here.
                await recording.applyLiveCoverage(trails: area.rawTrails ?? area.trails)
            }
        }
        .overlay {
            if let name = celebrationTrailName {
                trailCompletionOverlay(name: name)
                    .transition(.scale(scale: 0.8).combined(with: .opacity))
            }
        }
        .onChange(of: filterKey, initial: true) { _, _ in
            // filterKey embeds `area?.id`, so a nil→loaded area
            // transition recomputes too — no separate onChange needed
            // for the load.
            recomputeFiltered()
        }
        .onChange(of: areaCompletionsCount) { _, _ in
            // Status filter depends on per-trail completion; refresh
            // when the user completes a trail in this area.
            recomputeFiltered()
        }
        .onChange(of: filteredCompletedCount) { old, new in
            // Trigger the celebration when the area transitions into 100%.
            // Suppress while the recording summary is up — the trophy state
            // there is enough acknowledgement, and stacking sheets is messy.
            guard let area, area.resolvedTrailCount > 0 else { return }
            let total = area.resolvedTrailCount
            if old < total && new >= total && !showSummary {
                showAreaComplete = true
            }
        }
    }

    /// Binding that gates the trail-list sheet's presentation on the
    /// area being loaded. Read-only — `interactiveDismissDisabled`
    /// blocks user-initiated dismissal so the setter is a no-op.
    private var trailSheetPresented: Binding<Bool> {
        Binding(
            get: { self.area != nil && self.minLoadingTimeElapsed },
            set: { _ in }
        )
    }

    /// The `bottomInset` we pass to TrailMapView. Computed from the
    /// trail-list sheet's currently-settled detent, so the map's
    /// user-dot shift always clears the visible sheet area. Detent
    /// transitions are coarse (one event per release), so this only
    /// changes a handful of times per session — no per-frame thrash.
    ///
    /// Heights resolved against UIScreen rather than threading a
    /// GeometryReader value up. iPad multitasking would skew this,
    /// but the app's iPhone-only, so close enough.
    private var effectiveBottomInset: CGFloat {
        let screenH = UIScreen.main.bounds.height
        let sheetHeight: CGFloat
        if sheetDetent == .large {
            sheetHeight = screenH * 0.9
        } else if sheetDetent == Self.mediumDetent {
            sheetHeight = screenH * 0.5  // mirrors .fraction(0.5)
        } else {
            sheetHeight = 150  // small / peek detent
        }
        return sheetHeight
    }

    /// Loading state. Paints the bundled silhouette so the wait feels
    /// like the screen has already arrived. After the 2 s reveal
    /// completes, the trails wave gently in place until real area data
    /// lands. Plain spinner fallback only when no silhouette is bundled.
    @ViewBuilder
    private var loadingState: some View {
        ZStack {
            Color(.secondarySystemBackground)
                .ignoresSafeArea()
            if let silhouette = silhouettes.cachedSilhouette(for: areaId) {
                LoadingSilhouetteCanvas(silhouette: silhouette)
                    .ignoresSafeArea()
            } else {
                ProgressView()
            }
        }
        // Kick the R2 fetch so the loading-state silhouette
        // shows up if we don't already have it cached. Most of
        // the time HomeView.prefetchVisibleAreas + AreaCard's
        // own `.task` will have populated this before the user
        // navigates in, but this is the safety net.
        .task(id: areaId) {
            await silhouettes.silhouette(for: areaId)
        }
    }

    /// Show the trail-completion celebration overlay for `name` and auto-
    /// dismiss after 3.5s. Tapping the overlay dismisses it sooner.
    private func showCelebration(name: String) {
        log.notice("trailCompletion areaId=\(self.areaId, privacy: .public) trail=\(name, privacy: .public)")
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        withAnimation(.spring(response: 0.45, dampingFraction: 0.7)) {
            celebrationTrailName = name
        }
        Task {
            try? await Task.sleep(for: .seconds(3.5))
            await MainActor.run {
                withAnimation(.easeOut(duration: 0.25)) {
                    if celebrationTrailName == name { celebrationTrailName = nil }
                }
            }
        }
    }

    private func trailCompletionOverlay(name: String) -> some View {
        ZStack {
            Color.black.opacity(0.35).ignoresSafeArea()
            VStack(spacing: 14) {
                Image(systemName: "checkmark.seal.fill")
                    .font(.system(size: 84))
                    .foregroundStyle(.cyan)
                    .symbolEffect(.bounce, options: .repeat(2))
                Text("Trail Complete!")
                    .font(.title.bold())
                Text(name)
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(36)
            .frame(maxWidth: 320)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .padding(24)
        }
        .contentShape(Rectangle())
        .onTapGesture {
            withAnimation(.easeOut(duration: 0.2)) { celebrationTrailName = nil }
        }
    }

    /// Completion count restricted to the current area's trail IDs so orphan
    /// completions (from before the trail-id determinism fix) don't inflate
    /// the celebration trigger or any header counters that reference it.
    private var filteredCompletedCount: Int {
        guard area != nil else { return 0 }
        // areaTrailIds is the cached Set from `recomputeFiltered()`;
        // see `@State areaTrailIds` for why this isn't built inline.
        return progress.completionCount(in: areaId, trails: area?.trails ?? [])
    }

    private func loadPastPaths() async {
        let history = await recording.loadHistory()
        pastHikes = makePastHikes(from: history)
    }

    /// Build the in-memory `PastHike` list from on-disk recordings,
    /// scoped to this area. Shared by `loadPastPaths` (halo-only
    /// refresh after a recording finishes) and
    /// `loadHistoryDerivedState` (full coverage replay on area open).
    private func makePastHikes(from history: [SavedRecording]) -> [PastHike] {
        // touchedAreaIds: a walk that credited trails here renders its
        // GPS track as a cyan halo just like a hike recorded in-area.
        // areaAndTwins: a hike recorded under a now-hidden duplicate twin
        // (docs/adr/0002) draws its trace on this canonical area too.
        let areas = AreaDataService.shared.areaAndTwins(areaId)
        return history
            .filter { !$0.touchedAreaIds.isDisjoint(with: areas) }
            .map { PastHike(path: $0.path, startedAt: $0.startedAt) }
    }

    /// Trail the retarget banner should offer to switch to, or nil
    /// if no banner should render. Nil when there's no recording,
    /// no selected trail, the selected trail IS the recording
    /// trail (no-op), or the selection points at a trail not in
    /// this area's list (defensive — shouldn't happen, but cheap
    /// to guard).
    ///
    /// Roam-mode recordings DO get the banner: tapping a trail on
    /// the map while recording roam-style offers to convert the
    /// recording to a trail-mode recording targeted at the tapped
    /// trail. Build 12's `RecordingService.retargeted` already
    /// handles the roam→trail conversion path; this used to be
    /// gated on `mode == .trail` here, which was the build-12
    /// device-test bug.
    private func retargetCandidate(area: Area) -> Trail? {
        guard let activeRec = recording.activeRecording,
              let selectedId = selectedTrailId,
              selectedId != activeRec.trailId
        else { return nil }
        return area.trails.first(where: { $0.id == selectedId })
    }

    /// Top suggestion from `TrailSuggestionEngine`, filtered by
    /// the user's per-session dismissals, or nil if no candidate
    /// qualifies. Recomputed each body eval — the engine runs in
    /// O(trail count × ~100 nodes) which is cheap at the scales
    /// the app sees (~200 trails per area max).
    ///
    /// Uses the raw (pre-decimation) trail set when available so
    /// the projection math sees the same dense node geometry the
    /// halo / coverage paths use. Decimated trails would over-
    /// estimate the detour distance by up to the decimation
    /// epsilon (5 m).
    private func suggestionCandidate(area: Area) -> TrailSuggestion? {
        guard let activeRec = recording.activeRecording,
              let coord = location.liveLocation ?? location.userLocation
        else { return nil }
        let trails = area.rawTrails ?? area.trails
        let coverageByTrailId = coverage.coverage(for: area.id)
        let pace = recording.smoothedPaceMetersPerSec()
        let candidates = TrailSuggestionEngine.candidates(
            userLocation: coord,
            currentTrailId: activeRec.trailId,
            trails: trails,
            coverageByTrailId: coverageByTrailId,
            paceMetersPerSec: pace,
            maxResults: 5  // request a few so we can skip dismissed ones
        )
        return candidates.first(where: { !dismissedSuggestionIds.contains($0.trail.id) })
    }

    /// Pull recorded hike history once and use it for both:
    ///   - the cyan coverage halo (`pastHikes` → path slice)
    ///   - canonical completions, replayed from saved GPS paths against the
    ///     current trails. This self-heals after a re-fetch that changed
    ///     trail IDs: even if `completedTrailIds` in history points at a
    ///     stale id, replaying the path against the new trails reproduces
    ///     the right coverage and re-marks completion under the new id.
    /// Manual toggles via the trail-row checkbox still live in ProgressService
    /// and union with history-derived completions.
    private func loadHistoryDerivedState() async {
        let history = await recording.loadHistory()
        // touchedAreaIds + the walk-aware accessor: walks credited in
        // this area count exactly like local hikes.
        let local = history.filter { $0.touchedAreaIds.contains(areaId) }
        pastHikes = makePastHikes(from: history)
        // Carry forward any completedTrailIds whose ids still match — cheap
        // path that doesn't need to walk the GPS grid. The path-replay below
        // covers the case where ids changed.
        let stillValid = Set(area?.trails.map(\.id) ?? [])
        let completed = Set(local.flatMap { $0.completedTrailIds(in: areaId) }).intersection(stillValid)
        progress.bulkMarkComplete(areaId: areaId, trailIds: completed)
        if let trails = area?.trails {
            await recording.rebuildCoverageFromHistory(areaId: areaId, trails: trails)
        }
    }

    /// Pre-flight gate before kicking off a hike. Walks through the
    /// permission, conflict, and distance checks in order and either
    /// starts immediately or surfaces the appropriate confirmation.
    /// Pass a trailId to start in `.trail` mode (history will label the
    /// hike with that trail's name and TrailMapView lights it up as a
    /// Build a single multi-track GPX of every trail in the area
    /// and present the share sheet. Loaded into Garmin Connect
    /// the file produces one course per trail — useful for
    /// planning a multi-trail visit. No-op when the area hasn't
    /// finished loading.
    private func exportAreaGpx() {
        guard let area else { return }
        ActivityLogService.shared.log(
            category: "trail",
            action: "exportAreaGpx",
            context: ["areaId": areaId]
        )
        do {
            let url = try GpxExport.temporaryFile(area: area)
            areaGpxShareURL = IdentifiedURL(url: url)
        } catch {
            // Silent — share sheet won't appear; user can retry.
        }
    }

    /// Single-trail GPX (the selected trail) — small, one clean track, the
    /// common "load this hike onto my watch" case. Shares the same share-sheet
    /// state as the area export.
    private func exportTrailGpx(_ trail: Trail) {
        ActivityLogService.shared.log(
            category: "trail",
            action: "exportTrailGpx",
            context: ["areaId": areaId, "trailId": trail.id]
        )
        do {
            let url = try GpxExport.temporaryFile(trail: trail, areaName: area?.name)
            areaGpxShareURL = IdentifiedURL(url: url)
        } catch {
            // Silent — share sheet won't appear; user can retry.
        }
    }

    /// purple stroke). The trailId is preserved through the conflict /
    /// far-warning dialogs via `pendingRecordTrailId`.
    private func tryStartRecording(trailId: String? = nil) {
        guard let area else { return }
        if !location.isAuthorized {
            location.requestPermission()
            return
        }

        // Item 1 — concurrent recording prevention. An active WALK
        // always conflicts, even when its primary area happens to be
        // this one: starting an area recording here would silently
        // replace the walk and throw away its multi-area credit scope.
        if let active = recording.activeRecording,
           active.areaId != areaId || active.mode == .walk {
            pendingRecordTrailId = trailId
            conflictAreaName = active.mode == .walk
                ? "your walk"
                : areas.cachedArea(id: active.areaId)?.name
                    ?? areas.summaries.first { $0.id == active.areaId }?.name
                    ?? "another area"
            showConflictAlert = true
            return
        }

        // Item 3 — far-from-area warning. We only check when we actually
        // have a fresh user location; otherwise let the user proceed and
        // the recording will pick up coords once GPS catches up.
        if let userLoc = location.userLocation {
            let distMi = haversineDistanceMi(
                lat1: userLoc.latitude, lon1: userLoc.longitude,
                lat2: area.centerLat,    lon2: area.centerLon
            )
            if distMi > farFromAreaThresholdMi {
                pendingRecordTrailId = trailId
                farDistanceMi = distMi
                showFarWarning = true
                return
            }
        }

        startRecordingNow(trailId: trailId)
    }

    /// Actually start the recording. Used both directly from
    /// `tryStartRecording` (no preflight conflicts) and from the dialog
    /// "proceed" buttons after preflight resolves.
    private func startRecordingNow(trailId: String?) {
        let mode: RecordingMode = trailId == nil ? .roam : .trail
        recording.startRecording(areaId: areaId, mode: mode, trailId: trailId)
        // Mirror the trail-row tap flow exactly (direct assignment, no
        // withAnimation wrapper) so TrailMapView's existing selected-trail
        // styling kicks in on top of the purple recording-trail render.
        if let trailId {
            selectedTrailId = trailId
        }
    }

    private func stopOtherRecordingThenStart(trailId: String?) async {
        guard let active = recording.activeRecording else { return }
        // A walk stops through the multi-area path so every nearby area
        // gets its coverage credit before this area's recording starts.
        if active.mode == .walk {
            var trailsByArea: [String: [Trail]] = [:]
            for aid in active.nearbyAreaIds ?? [active.areaId] {
                // if/else, not `??` — its autoclosure can't host an await.
                let a: Area?
                if let cached = areas.cachedArea(id: aid) {
                    a = cached
                } else {
                    a = await areas.area(id: aid)
                }
                if let a {
                    trailsByArea[aid] = a.rawTrails ?? a.trails
                }
            }
            _ = await recording.stopWalk(trailsByArea: trailsByArea)
            startRecordingNow(trailId: trailId)
            return
        }
        // Split out of `??` because `??` takes an autoclosure that can't
        // host an `await`.
        let trails: [Trail]
        // Prefer raw trails for stopRecording so coverage finalization
        // uses the dense node set — see the live-coverage call above.
        if let cached = areas.cachedArea(id: active.areaId) {
            trails = cached.rawTrails ?? cached.trails
        } else {
            let loaded = await areas.area(id: active.areaId)
            trails = loaded?.rawTrails ?? loaded?.trails ?? []
        }
        _ = await recording.stopRecording(trails: trails)
        startRecordingNow(trailId: trailId)
    }

    /// Content of the trail-list sheet (always-presented, native
    /// `UISheetPresentationController` via `.sheet` / `.presentation
    /// Detents`). Top-to-bottom:
    ///
    ///   1. (system drag indicator — rendered by SwiftUI at the top
    ///      edge of the sheet via `.presentationDragIndicator(.visible)`)
    ///   2. Area-name headline
    ///   3. Tracking-mode toast capsule (transient)
    ///   4. Control bar (map-style picker, recenter, tracking cycle)
    ///   5. Retarget / suggestion banner (only while recording)
    ///   6. RecordingPanel (only while recording)
    ///   7. TrailListView (fills remaining; scrolls within at the
    ///      `.large` detent)
    ///
    /// All nested modal flows (GPX share, recording summary, area-
    /// completion celebration) live INSIDE this content so SwiftUI
    /// can present them over the always-on trail-list sheet — you
    /// can only have one `.sheet` modifier active per ancestor view,
    /// so attaching them at AreaView's body would conflict with the
    /// trail-list sheet itself.
    /// One-line area summary for the sheet header: trails · total
    /// distance (honors the units toggle via UnitFormatter) · completion.
    private func areaSummaryLine(area: Area, completed: Int) -> String {
        var parts = [
            "\(area.resolvedTrailCount) trails",
            UnitFormatter.distance(miles: area.resolvedTotalMi, units: units),
        ]
        if area.resolvedTrailCount > 0 {
            parts.append("\(completed) of \(area.resolvedTrailCount) completed")
        }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func sheetContent(area: Area) -> some View {
        VStack(spacing: 0) {
            // Title + summary block — centered under the drag
            // indicator, reads as one header. The trail count /
            // completion line used to live in TrailListView's own
            // summary header; with the area name now sitting above
            // it, the two read as redundant stacked headers — moved
            // up here so the user gets the whole "where am I, what's
            // here" pitch in one block.
            VStack(spacing: 4) {
                Text(areaName)
                    .font(.title3.weight(.semibold))
                    .lineLimit(1)

                // Single summary line: trails · total distance (unit-aware)
                // · completion. `areaTrailIds` is the cached Set from
                // recomputeFiltered() — avoids a per-eval O(N) rebuild.
                // Whole line turns green at 100% as an area-complete cue.
                let completed = progress.completionCount(in: area.id, trails: area.trails)
                let areaComplete = area.resolvedTrailCount > 0 && completed >= area.resolvedTrailCount
                Text(areaSummaryLine(area: area, completed: completed))
                    .font(.subheadline)
                    .foregroundStyle(areaComplete ? .green : .secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)

                // OSM attribution is NOT repeated here — the required
                // "© OpenStreetMap contributors" credit lives in
                // Settings → About (with the licence link), which
                // satisfies the ODbL. Keeping the header uncluttered.
            }
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.horizontal, 20)
            .padding(.top, 10)
            .padding(.bottom, 12)

            // Trails | Dex segment selector. Always visible so the
            // user can flip between the trail list and the achievements
            // grid regardless of which is showing.
            Picker("View", selection: $sheetTab) {
                Text("Trails").tag(AreaSheetTab.trails)
                Text("Dex").tag(AreaSheetTab.dex)
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("area-view-picker")
            .padding(.horizontal, 20)
            .padding(.bottom, 12)
            .onChange(of: sheetTab) { _, tab in
                if tab == .dex {
                    AnalyticsService.shared.capture(.dexOpened(areaId: area.id))
                }
            }

            switch sheetTab {
            case .trails:
                controlBar(area: area)
                    .padding(.bottom, 12)

                // Tracking-mode toast. A subtle solid capsule, NOT glass —
                // it floats inside the glass sheet, so a material backdrop
                // would be the same glass-on-glass muddiness we removed
                // from the icon buttons. Tinted with the accent so it
                // reads as a transient status note.
                if let toast = trackingModeToast {
                    Text(toast)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 7)
                        .background(Color.accentColor, in: Capsule())
                        .transition(.opacity.combined(with: .scale(scale: 0.9)))
                        .padding(.bottom, 10)
                }

                if isRecording {
                    recordingBanners(area: area)
                    RecordingPanel(area: area) { finished in
                        finishedRecording = finished
                        showSummary = finished != nil
                        // Refresh the cyan coverage halo with the
                        // just-finished hike's path.
                        Task { await loadPastPaths() }
                    }
                    .padding(.bottom, 4)
                }

                TrailListView(
                    area: area,
                    selectedTrailId: $selectedTrailId,
                    statusFilter: $statusFilter,
                    difficultyFilter: $difficultyFilter,
                    lengthFilter: $lengthFilter,
                    routeFilter: $routeFilter,
                    searchQuery: $trailSearchQuery,
                    filteredTrails: filtered,
                    onRecordTrail: { trail in tryStartRecording(trailId: trail.id) }
                )

            case .dex:
                DexView(area: area)
            }
        }
        // Nested modal sheets — must live inside the always-on trail-
        // list sheet so SwiftUI lets them present on top instead of
        // conflicting with each other.
        .sheet(item: $areaGpxShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
        .sheet(item: $reportingTrail) { trail in
            ReportTrailView(trail: trail, areaId: areaId, areaName: areaName)
        }
        .sheet(isPresented: $showSummary) {
            if let finished = finishedRecording {
                RecordingSummarySheet(
                    finished: finished,
                    areaName: areaName,
                    trails: area.trails
                )
            }
        }
        .sheet(isPresented: $showAreaComplete) {
            AreaCompletionView(area: area)
                .presentationDetents([.large])
        }
        // Confirmation dialogs ALSO nest inside the sheet — same
        // one-presentation-per-ancestor rule that put the modal sheets
        // here. With them attached to AreaView's body, tapping "Record
        // Hike" set `showFarWarning = true`, SwiftUI tried to present
        // the dialog from AreaView, found the trail-list sheet already
        // owning that slot, and bounced — dismissing the sheet to
        // present the dialog, then re-presenting the sheet (because
        // its binding stays true), which clobbered the dialog. Net:
        // dialog flashed for ~0.1s then vanished, and recording could
        // never start.
        .confirmationDialog(
            "You're already recording at \(conflictAreaName)",
            isPresented: $showConflictAlert,
            titleVisibility: .visible
        ) {
            Button("Stop That Hike & Start Here", role: .destructive) {
                let trailId = pendingRecordTrailId
                pendingRecordTrailId = nil
                Task { await stopOtherRecordingThenStart(trailId: trailId) }
            }
            Button("Cancel", role: .cancel) {
                pendingRecordTrailId = nil
            }
        } message: {
            Text("Starting a new hike here will save and end your hike at \(conflictAreaName).")
        }
        .confirmationDialog(
            "You're \(UnitFormatter.distance(miles: farDistanceMi, units: units)) from \(areaName)",
            isPresented: $showFarWarning,
            titleVisibility: .visible
        ) {
            Button("Start Anyway", role: .destructive) {
                let trailId = pendingRecordTrailId
                pendingRecordTrailId = nil
                startRecordingNow(trailId: trailId)
            }
            Button("Cancel", role: .cancel) {
                pendingRecordTrailId = nil
            }
        } message: {
            Text("Recording from this far away will track GPS but won't update trail coverage in this area.")
        }
    }

    /// Retarget vs suggestion banner, only shown while a recording is
    /// active. Extracted so `sheetContent` reads cleanly — the
    /// inline form has ~70 lines of logging / dismiss closures that
    /// dwarf the rest of the sheet layout.
    @ViewBuilder
    private func recordingBanners(area: Area) -> some View {
        // Retarget banner takes priority: the user has manually
        // tapped a trail different from the one the recording is
        // targeted at, a stronger signal than a heuristic suggestion.
        if let retargetTrail = retargetCandidate(area: area) {
            RetargetTrailBanner(
                selectedTrail: retargetTrail,
                onSwitch: {
                    ActivityLogService.shared.log(
                        category: "recording",
                        action: "retarget",
                        context: ["source": "retargetBanner", "trailId": retargetTrail.id]
                    )
                    recording.retargetTrail(retargetTrail.id)
                    // Re-assigning selectedTrailId to its current
                    // value is a SwiftUI no-op, so bump
                    // centerOnSwitchedTrailTick separately to force
                    // TrailMapView to re-fit the camera around the
                    // user + the new active trail.
                    selectedTrailId = retargetTrail.id
                    centerOnSwitchedTrailTick &+= 1
                },
                onDismiss: { selectedTrailId = nil }
            )
        } else if let suggestion = suggestionCandidate(area: area) {
            SuggestionBanner(
                suggestion: suggestion,
                onSwitch: {
                    ActivityLogService.shared.log(
                        category: "recording",
                        action: "retarget",
                        context: ["source": "suggestionBanner", "trailId": suggestion.trail.id]
                    )
                    recording.retargetTrail(suggestion.trail.id)
                    selectedTrailId = suggestion.trail.id
                    centerOnSwitchedTrailTick &+= 1
                },
                onDismiss: {
                    log.notice("suggestion dismiss trail=\(suggestion.trail.name, privacy: .public)")
                    dismissedSuggestionIds.insert(suggestion.trail.id)
                }
            )
            // Diagnostics: log when the banner mounts and unmounts
            // so a Send Diagnostics bundle can explain why the user
            // did or didn't see it at a given moment. SwiftUI calls
            // .onAppear exactly once per identity, and the banner's
            // identity is the suggestion trail id (it remounts when
            // the candidate changes).
            .onAppear {
                log.notice("suggestion mount trail=\(suggestion.trail.name, privacy: .public) detour=\(Int(suggestion.detourMeters))m remaining=\(Int(suggestion.remainingMeters))m extraSec=\(Int(suggestion.extraSeconds))")
            }
            .onDisappear {
                log.notice("suggestion unmount trail=\(suggestion.trail.name, privacy: .public)")
            }
            .id(suggestion.trail.id)
        }
    }

    /// Show a brief "Following your direction" / similar pill above
    /// the controlBar so users learn what each tracking-mode icon
    /// means without us cluttering the UI with permanent labels.
    /// Cancels any in-flight dismiss timer so rapid cycle-taps don't
    /// fight each other.
    private func showTrackingModeToast(_ label: String) {
        trackingModeToastTask?.cancel()
        withAnimation(.spring(response: 0.3, dampingFraction: 0.75)) {
            trackingModeToast = label
        }
        trackingModeToastTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            await MainActor.run {
                withAnimation(.easeOut(duration: 0.3)) {
                    trackingModeToast = nil
                }
            }
        }
    }

    private func controlBar(area: Area) -> some View {
        HStack(spacing: 14) {
            // Note: the previous "map.fill / list.bullet" toggle was
            // removed when the trail list moved to a native sheet —
            // dragging the sheet down to the small peek detent now serves
            // the same "show me more map" affordance, matching how
            // Apple Maps and other system-sheet UIs handle it.

            // Camera tracking cycle — Apple Maps style. Cycles
            // free → follow → follow-with-heading → free. Icon
            // reflects the current mode. Toast under the controlBar
            // (rendered in body) names the new mode for ~2 s so users
            // learn the cycle without permanent on-screen labels.
            //
            // No glass on these in-sheet icon buttons: the sheet
            // itself is the glass surface, and glass-on-glass reads
            // muddy (Apple's HIG calls this out — Liquid Glass is a
            // single layer between content and surface). A subtle
            // adaptive fill gives a tappable affordance without
            // competing with the sheet material. The record button
            // below keeps its glass because it's the primary CTA and
            // is meant to stand proud.
            Button {
                if !location.isAuthorized { location.requestPermission(); return }
                trackingMode = trackingMode.next
                showTrackingModeToast(trackingMode.toastLabel)
            } label: {
                Image(systemName: trackingMode.symbol)
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .background(Color(.tertiarySystemFill), in: Circle())
            }
            .accessibilityLabel(trackingMode.accessibilityLabel)

            // Recenter on user — one-shot center. Doesn't engage
            // tracking; cycle button above is the way to opt into
            // continuous follow. Distinct viewfinder icon so it
            // doesn't collide with the cycle's `location.fill` state.
            Button {
                if !location.isAuthorized { location.requestPermission(); return }
                recenterTick &+= 1
            } label: {
                Image(systemName: "location.fill.viewfinder")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .background(Color(.tertiarySystemFill), in: Circle())
            }
            .accessibilityLabel("Recenter on my location")
            .accessibilityIdentifier("area-recenter-button")

            Spacer()

            // Record button — hidden during recording (RecordingPanel
            // owns the stop/save flow there).
            if !isRecording {
                Button {
                    tryStartRecording()
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "record.circle")
                            .font(.body.weight(.semibold))
                        Text("Record Hike")
                            .fontWeight(.semibold)
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 12)
                    // Flat fill to match the tracking / recenter
                    // control buttons — no glass. On the now-opaque
                    // sheet, the glass capsule was the last lingering
                    // glass-on-opaque element and read inconsistently
                    // next to the flat icon buttons.
                    .background(Color(.tertiarySystemFill), in: Capsule())
                }
            }
        }
        .padding(.horizontal, 20)
    }
}

/// Full-screen silhouette behind the AreaView loading state. Trails light
/// up one-by-one driven by elapsed time so the wait reads as the area
/// arriving instead of dead air. Long trails are drawn first (they carry
/// the most visual weight); then medium, then short. Total animation is
/// capped so a 200-trail area still finishes in ~2.5s.
private struct LoadingSilhouetteCanvas: View {
    let silhouette: AreaSilhouette

    @Environment(\.colorScheme) private var colorScheme
    @State private var startDate = Date()

    /// Draw order is difficulty priority so where trails cross, the harder
    /// color wins (red over orange over green) instead of two translucent
    /// strokes blending. Within a difficulty, longest spines lead so the reveal
    /// still fills big-to-small.
    private static func priority(_ d: String) -> Int { d == "h" ? 2 : (d == "m" ? 1 : 0) }
    private var orderedLines: [SilhouetteLine] {
        silhouette.l.filter { $0.p.count >= 2 }.sorted {
            let pa = Self.priority($0.d), pb = Self.priority($1.d)
            return pa == pb ? $0.p.count > $1.p.count : pa < pb
        }
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60.0, paused: false)) { context in
            Canvas { ctx, size in
                guard let bbox = silhouette.bbox else { return }
                let lines = orderedLines
                guard !lines.isEmpty else { return }

                let pad: CGFloat = 24
                let drawW = size.width - 2 * pad
                let drawH = size.height - 2 * pad
                guard drawW > 0, drawH > 0 else { return }

                let centerLat = (bbox.s + bbox.n) / 2
                let lonScale = cos(centerLat * .pi / 180)
                let xRange = max((bbox.e - bbox.w) * lonScale, .leastNonzeroMagnitude)
                let yRange = max(bbox.n - bbox.s, .leastNonzeroMagnitude)
                let scale = min(drawW / xRange, drawH / yRange)
                let canvasW = xRange * scale
                let canvasH = yRange * scale
                let xOffset = pad + (drawW - canvasW) / 2
                let yOffset = pad + (drawH - canvasH) / 2

                let totalAnimation: TimeInterval = 1.0
                // Single-line silhouettes get the full duration to
                // themselves so a tiny area doesn't snap-in in 0.4 s and
                // look broken; everything else uses 0.4 s per line with
                // the stagger sized to land the last line exactly at
                // totalAnimation. AreaView holds the loading view for
                // 1.5 s total — the reveal lands at 1.0 s, leaving
                // ~0.5 s of "all trails visible + gentle wave" before
                // the loaded view takes over. That settled window is
                // what stops the eye from registering trails as
                // "cut off mid-reveal" on hundred-trail areas.
                let perLineDuration: TimeInterval = lines.count == 1 ? totalAnimation : 0.4
                let stagger: TimeInterval = lines.count > 1
                    ? (totalAnimation - perLineDuration) / Double(lines.count - 1)
                    : 0
                let elapsed = context.date.timeIntervalSince(startDate)

                // Post-reveal subtle wave: once the reveal is done, displace
                // each path point vertically by a small sine of its x
                // position + time so the silhouette feels alive instead of
                // frozen while we wait for trail data. Ramped in over 0.6 s
                // so it doesn't pop on the moment reveal completes.
                let waveActive = max(0, elapsed - totalAnimation)
                let waveRamp = min(1.0, waveActive / 0.6)
                let waveAmp = 2.0 * waveRamp
                let waveOmegaT = waveActive * 1.4

                for (i, line) in lines.enumerated() {
                    guard line.p.count >= 2 else { continue }
                    let lineStart = Double(i) * stagger
                    let raw = (elapsed - lineStart) / perLineDuration
                    let progress = max(0, min(1, raw))
                    if progress <= 0 { continue }

                    var path = Path()
                    for (j, pt) in line.p.enumerated() {
                        guard pt.count >= 2 else { continue }
                        let lat = pt[0], lon = pt[1]
                        let x = xOffset + (lon - bbox.w) * lonScale * scale
                        let yBase = yOffset + canvasH - (lat - bbox.s) * scale
                        let y = yBase + waveAmp * sin(x / 60.0 + waveOmegaT)
                        let p = CGPoint(x: x, y: y)
                        if j == 0 { path.move(to: p) } else { path.addLine(to: p) }
                    }
                    let trimmed = progress >= 1 ? path : path.trimmedPath(from: 0, to: progress)

                    let color: Color
                    switch line.d {
                    case "e": color = .green
                    case "m": color = .orange
                    case "h": color = .red
                    default:  color = .gray
                    }
                    // Opaque: overlapping trails must NOT accumulate alpha — that
                    // additive blend is what pushed a dimmed orange up into gold.
                    // The faint-backdrop wash is applied once to the whole Canvas
                    // via `.opacity` below, AFTER overlaps resolve by priority, so
                    // orange stays orange instead of collapsing to gold.
                    ctx.stroke(
                        trimmed,
                        with: .color(color),
                        style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round)
                    )
                }
            }
            .opacity(colorScheme == .dark ? 0.9 : 0.82)
        }
        .onAppear { startDate = Date() }
    }
}
