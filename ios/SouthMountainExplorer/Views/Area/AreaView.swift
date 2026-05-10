import SwiftUI

private let farFromAreaThresholdMi = 5.0

struct AreaView: View {
    let areaId: String
    let areaName: String

    @Environment(AreaDataService.self) private var areas
    @Environment(RecordingService.self) private var recording
    @Environment(FavoritesService.self) private var favorites
    @Environment(LocationService.self) private var location
    @Environment(ProgressService.self) private var progress
    @Environment(ActivityService.self) private var activity
    @Environment(\.dismiss) private var dismiss

    @State private var area: Area? = nil
    @State private var isLoading = true
    @State private var loadError: String? = nil
    @State private var showTrailList = true
    @State private var trailListHeight: CGFloat = 340
    // Captured at gesture start so onChanged can compute an absolute
    // height instead of accumulating per-frame translations against a
    // moving target — the latter was the source of the drag jank.
    @State private var dragStartHeight: CGFloat? = nil
    @State private var selectedTrailId: String? = nil
    @State private var finishedRecording: FinishedRecording? = nil
    @State private var showSummary = false
    @State private var showAreaComplete = false
    @State private var pastPaths: [[GpsPoint]] = []
    @State private var recenterTick: Int = 0

    // Pre-flight checks before kicking off a recording.
    @State private var showConflictAlert = false
    @State private var conflictAreaName: String = ""
    @State private var showFarWarning = false
    @State private var farDistanceMi: Double = 0

    private let defaultListHeight: CGFloat = 340

    private var isRecording: Bool {
        recording.activeRecording?.areaId == areaId
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            if let area {
                // Full-screen map
                TrailMapView(
                    area: area,
                    activeRecording: isRecording ? recording.activeRecording : nil,
                    pastPaths: pastPaths,
                    recenterTick: recenterTick,
                    selectedTrailId: $selectedTrailId
                )
                .ignoresSafeArea()

                // Trail list sheet
                if showTrailList {
                    trailListSheet(area: area)
                }

                // Bottom controls — RecordingPanel stacks above the
                // controlBar so the user can still toggle the trail list
                // (and tap rows to highlight trails on the map) while a
                // hike is being recorded.
                VStack(spacing: 12) {
                    if isRecording {
                        RecordingPanel(area: area) { finished in
                            finishedRecording = finished
                            showSummary = finished != nil
                            // Refresh the cyan coverage halo with the
                            // just-finished hike's path.
                            Task { await loadPastPaths() }
                        }
                    }
                    controlBar(area: area)
                }
                .padding(.bottom, (showTrailList ? currentListHeight : 0) + 20)
                .transition(.move(edge: .bottom).combined(with: .opacity))

            } else if isLoading {
                ProgressView("Loading \(areaName)...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("Area Unavailable",
                    systemImage: "xmark.octagon",
                    description: Text(loadError ?? "Could not load trail data. Check your connection."))
            }
        }
        .navigationBarHidden(true)
        .overlay(alignment: .top) {
            HStack {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .glassEffect(in: .circle)
                }
                Spacer()
                Button {
                    Task { await favorites.toggle(areaId: areaId) }
                } label: {
                    Image(systemName: favorites.isFavorite(areaId) ? "heart.fill" : "heart")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .glassEffect(in: .circle)
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
            let result = await areas.areaWithError(id: areaId)
            area = result.area
            loadError = result.error
            isLoading = false
            await loadHistoryDerivedState()
        }
        .task(id: isRecording) {
            // While a recording is active for this area, recompute coverage
            // every 30s so partial progress visibly fills in (trail-list
            // progress bars tick up, trails crossing 90% turn cyan live).
            guard isRecording else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled, isRecording, let area else { break }
                await recording.applyLiveCoverage(trails: area.trails)
            }
        }
        .sheet(isPresented: $showSummary) {
            if let finished = finishedRecording {
                RecordingSummarySheet(
                    finished: finished,
                    areaName: areaName,
                    trails: area?.trails ?? []
                )
            }
        }
        .sheet(isPresented: $showAreaComplete) {
            if let area {
                AreaCompletionView(area: area)
                    .presentationDetents([.large])
            }
        }
        .confirmationDialog(
            "You're already recording at \(conflictAreaName)",
            isPresented: $showConflictAlert,
            titleVisibility: .visible
        ) {
            Button("Stop That Hike & Start Here", role: .destructive) {
                Task { await stopOtherRecordingThenStart() }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Starting a new hike here will save and end your hike at \(conflictAreaName).")
        }
        .confirmationDialog(
            "You're \(String(format: "%.1f", farDistanceMi)) mi from \(areaName)",
            isPresented: $showFarWarning,
            titleVisibility: .visible
        ) {
            Button("Start Anyway", role: .destructive) {
                recording.startRecording(areaId: areaId, mode: .roam)
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Recording from this far away will track GPS but won't update trail coverage in this area.")
        }
        .onChange(of: progress.completionCount(in: areaId)) { old, new in
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

    private var currentListHeight: CGFloat { trailListHeight }

    private func loadPastPaths() async {
        let history = await recording.loadHistory()
        pastPaths = history
            .filter { $0.areaId == areaId }
            .map { $0.path }
    }

    /// Pull recorded hike history once and use it for both:
    ///   - the cyan coverage halo (`pastPaths`)
    ///   - the canonical "trails completed" set (any trail listed in any
    ///     SavedRecording.completedTrailIds for this area)
    /// Treating history as the source of truth for completions means a
    /// Refresh Trail Data call can never silently lose progress: if the
    /// trail's ID still matches, the completion gets rebuilt on next open.
    /// Manual toggles via the trail row continue to live in ProgressService
    /// and remain the union with history-derived completions.
    private func loadHistoryDerivedState() async {
        let history = await recording.loadHistory()
        let local = history.filter { $0.areaId == areaId }
        pastPaths = local.map { $0.path }
        let completed = Set(local.flatMap { $0.completedTrailIds })
        progress.bulkMarkComplete(areaId: areaId, trailIds: completed)
    }

    /// Pre-flight gate before kicking off a hike. Walks through the
    /// permission, conflict, and distance checks in order and either
    /// starts immediately or surfaces the appropriate confirmation.
    /// Pass a trailId to start in `.trail` mode (history will label the
    /// hike with that trail's name and TrailMapView lights it up as a
    /// purple stroke).
    private func tryStartRecording(trailId: String? = nil) {
        guard let area else { return }
        if !location.isAuthorized {
            location.requestPermission()
            return
        }

        // Item 1 — concurrent recording prevention.
        if let active = recording.activeRecording, active.areaId != areaId {
            conflictAreaName = areas.cachedArea(id: active.areaId)?.name
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
                farDistanceMi = distMi
                showFarWarning = true
                return
            }
        }

        let mode: RecordingMode = trailId == nil ? .roam : .trail
        recording.startRecording(areaId: areaId, mode: mode, trailId: trailId)
        // Mirror the trail-row tap flow exactly: direct assignment, no
        // withAnimation wrapper. Earlier the wrapper was eating the state
        // change in some context-menu transitions so the highlight never
        // showed. TrailMapView's render loop now also uses
        // activeRecording.trailId directly for the purple recording
        // highlight, so this is belt + suspenders.
        if let trailId {
            selectedTrailId = trailId
        }
    }

    private func stopOtherRecordingThenStart() async {
        guard let active = recording.activeRecording else { return }
        // Split out of `??` because `??` takes an autoclosure that can't
        // host an `await`.
        let trails: [Trail]
        if let cached = areas.cachedArea(id: active.areaId) {
            trails = cached.trails
        } else {
            trails = (await areas.area(id: active.areaId))?.trails ?? []
        }
        _ = await recording.stopRecording(trails: trails)
        recording.startRecording(areaId: areaId, mode: .roam)
    }

    private func trailListSheet(area: Area) -> some View {
        // Pass 3 on the panel layout. The offset trick (keep panel sized at
        // tallHeight, slide via .offset) was smooth but the ScrollView's
        // *frame* stayed tallHeight tall — so when collapsed, scrolling
        // landed at the frame bottom which was geometrically below the
        // visible area, hiding the last few rows.
        // Switching back to direct frame-height sizing so the ScrollView's
        // bounds match what the user can see. .animation(nil, value:)
        // keeps the drag 1:1 (no implicit resize animation); the spring in
        // onEnded is the only animated transition.
        GeometryReader { geo in
            let tallHeight = max(geo.size.height - 100, defaultListHeight)
            VStack(spacing: 0) {
                // Drag handle — extended hit area for the gesture
                VStack(spacing: 0) {
                    Capsule()
                        .fill(Color(.tertiaryLabel))
                        .frame(width: 36, height: 4)
                        .padding(.top, 10)
                        .padding(.bottom, 6)

                    Text(areaName)
                        .font(.headline)
                        .padding(.bottom, 4)
                }
                .frame(maxWidth: .infinity)
                .contentShape(Rectangle())
                .gesture(dragGesture(tallHeight: tallHeight))

                TrailListView(
                    area: area,
                    selectedTrailId: $selectedTrailId,
                    onRecordTrail: { trail in tryStartRecording(trailId: trail.id) }
                )
            }
            .frame(height: trailListHeight)
            .background(
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(.regularMaterial)
            )
            .frame(maxHeight: .infinity, alignment: .bottom)
            .animation(nil, value: trailListHeight)
        }
    }

    private func dragGesture(tallHeight: CGFloat) -> some Gesture {
        DragGesture()
            .onChanged { value in
                let start = dragStartHeight ?? trailListHeight
                if dragStartHeight == nil { dragStartHeight = trailListHeight }
                // Translate UP (negative dy) → grow the panel.
                let proposed = start - value.translation.height
                trailListHeight = min(max(proposed, 180), tallHeight)
            }
            .onEnded { _ in
                let snapPoint = (defaultListHeight + tallHeight) / 2
                let target: CGFloat = trailListHeight > snapPoint ? tallHeight : defaultListHeight
                withAnimation(.spring(response: 0.32, dampingFraction: 0.86)) {
                    trailListHeight = target
                }
                dragStartHeight = nil
            }
    }

    private func controlBar(area: Area) -> some View {
        HStack(spacing: 14) {
            // Map/List toggle — always visible so the user can show the
            // trail list during a recording and tap a row to highlight
            // the trail on the map.
            Button {
                withAnimation(.spring()) { showTrailList.toggle() }
            } label: {
                Image(systemName: showTrailList ? "map.fill" : "list.bullet")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .glassEffect(in: .circle)
            }

            // Recenter on user — replaces the removed MapUserLocationButton
            // (which MapKit placed on top of the favorite/close buttons).
            Button {
                if !location.isAuthorized { location.requestPermission(); return }
                recenterTick &+= 1
            } label: {
                Image(systemName: "location.fill")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .glassEffect(in: .circle)
            }

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
                    .glassEffect(.regular.interactive(), in: .capsule)
                }
            }
        }
        .padding(.horizontal, 20)
    }
}
