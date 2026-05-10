import SwiftUI

private let farFromAreaThresholdMi = 5.0

struct AreaView: View {
    let areaId: String
    let areaName: String
    /// When set on init, the view plays a one-shot trail-completion
    /// celebration overlay on first appear. Used by the notification-tap
    /// deep-link from ContentView so a user opening the "Trail Complete!"
    /// notification gets a celebratory beat instead of a silent jump in.
    var initialCelebrationTrailName: String? = nil

    @Environment(AreaDataService.self) private var areas
    @Environment(AreaSilhouetteService.self) private var silhouettes
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
    /// Captured by tryStartRecording when a confirmation dialog interrupts
    /// the start. The dialog's "proceed" button reads this so a trail-mode
    /// request survives the round-trip — without it, "Start Anyway" /
    /// "Stop & Start Here" silently downgraded to .roam mode and the
    /// recording-trail highlight never engaged.
    @State private var pendingRecordTrailId: String? = nil
    /// Name of the trail to celebrate over the map. Auto-clears after a
    /// short delay so the overlay doesn't sit forever.
    @State private var celebrationTrailName: String? = nil

    // Trail-list filters live up here so the map and the list share the
    // same source of truth — flipping a filter hides the corresponding
    // polylines from the map too, not just the list rows.
    @State private var statusFilter: TrailStatusFilter = .all
    @State private var difficultyFilter: TrailDifficultyFilter = .all
    @State private var lengthFilter: TrailLengthFilter = .all

    private let defaultListHeight: CGFloat = 340

    private var isRecording: Bool {
        recording.activeRecording?.areaId == areaId
    }

    /// Trail set after applying the user's filters. Single source of
    /// truth shared between TrailListView (which renders the rows) and
    /// TrailMapView (which renders the polylines).
    private func computeFilteredTrails(_ area: Area) -> [Trail] {
        area.trails.filter { trail in
            let isComplete = progress.isComplete(areaId: areaId, trailId: trail.id)
            switch statusFilter {
            case .all: break
            case .incomplete: if isComplete { return false }
            case .complete:   if !isComplete { return false }
            }
            if !difficultyFilter.matches(trail.difficulty) { return false }
            if !lengthFilter.matches(trail.distanceMi) { return false }
            return true
        }
    }

    /// Whether any non-default filter is active. When false we pass
    /// `nil` to TrailMapView so it skips the per-trail filter check
    /// entirely, since the unfiltered render is the common case.
    private var hasActiveFilter: Bool {
        statusFilter != .all || difficultyFilter != .all || lengthFilter != .all
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            if let area {
                let filtered = computeFilteredTrails(area)
                let visibleTrailIds: Set<String>? = hasActiveFilter
                    ? Set(filtered.map(\.id)) : nil
                // Full-screen map
                TrailMapView(
                    area: area,
                    activeRecording: isRecording ? recording.activeRecording : nil,
                    pastPaths: pastPaths,
                    recenterTick: recenterTick,
                    selectedTrailId: $selectedTrailId,
                    visibleTrailIds: visibleTrailIds
                )
                .ignoresSafeArea()

                // Trail list sheet
                if showTrailList {
                    trailListSheet(area: area, filtered: filtered)
                }

                // Bottom controls — controlBar (map toggle, recenter,
                // record) sits above the RecordingPanel so the location/
                // recenter buttons stay reachable above the recording bar
                // instead of being buried under it. The trail-list panel
                // ignores the bottom safe area to extend under the home
                // indicator, so we have to subtract that inset from the
                // padding here — otherwise the REC bar would still float
                // ~34pt above the visible top of the trail list panel.
                GeometryReader { proxy in
                    VStack(spacing: 4) {
                        controlBar(area: area)
                        if isRecording {
                            RecordingPanel(area: area) { finished in
                                finishedRecording = finished
                                showSummary = finished != nil
                                // Refresh the cyan coverage halo with the
                                // just-finished hike's path.
                                Task { await loadPastPaths() }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                    .padding(.bottom, showTrailList
                        ? max(0, currentListHeight - proxy.safeAreaInsets.bottom)
                        : 0)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                .allowsHitTesting(true)

            } else if isLoading {
                loadingState
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
            // Pop the celebration overlay if the view was opened via a
            // trail-complete push notification. Done after the area loads
            // so the overlay sits over the map, not a spinner.
            if let name = initialCelebrationTrailName {
                showCelebration(name: name)
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
            "You're \(String(format: "%.1f", farDistanceMi)) mi from \(areaName)",
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
        .overlay {
            if let name = celebrationTrailName {
                trailCompletionOverlay(name: name)
                    .transition(.scale(scale: 0.8).combined(with: .opacity))
            }
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

    private var currentListHeight: CGFloat { trailListHeight }

    /// Loading state. Paints the bundled silhouette behind a soft "Loading…"
    /// pill so the wait feels like the screen has already arrived. Falls
    /// back to a plain spinner when no silhouette is bundled for this area.
    @ViewBuilder
    private var loadingState: some View {
        ZStack {
            Color(.secondarySystemBackground)
                .ignoresSafeArea()
            if let silhouette = silhouettes.silhouette(for: areaId) {
                LoadingSilhouetteCanvas(silhouette: silhouette)
                    .ignoresSafeArea()
            }
            VStack(spacing: 12) {
                ProgressView()
                Text("Loading \(areaName)…")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 14)
            .background(.regularMaterial, in: Capsule())
        }
    }

    /// Show the trail-completion celebration overlay for `name` and auto-
    /// dismiss after 3.5s. Tapping the overlay dismisses it sooner.
    private func showCelebration(name: String) {
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
        guard let area else { return 0 }
        return progress.completionCount(in: areaId, validTrailIds: Set(area.trails.map(\.id)))
    }

    private func loadPastPaths() async {
        let history = await recording.loadHistory()
        pastPaths = history
            .filter { $0.areaId == areaId }
            .map { $0.path }
    }

    /// Pull recorded hike history once and use it for both:
    ///   - the cyan coverage halo (`pastPaths`)
    ///   - canonical completions, replayed from saved GPS paths against the
    ///     current trails. This self-heals after a re-fetch that changed
    ///     trail IDs: even if `completedTrailIds` in history points at a
    ///     stale id, replaying the path against the new trails reproduces
    ///     the right coverage and re-marks completion under the new id.
    /// Manual toggles via the trail-row checkbox still live in ProgressService
    /// and union with history-derived completions.
    private func loadHistoryDerivedState() async {
        let history = await recording.loadHistory()
        let local = history.filter { $0.areaId == areaId }
        pastPaths = local.map { $0.path }
        // Carry forward any completedTrailIds whose ids still match — cheap
        // path that doesn't need to walk the GPS grid. The path-replay below
        // covers the case where ids changed.
        let stillValid = Set(area?.trails.map(\.id) ?? [])
        let completed = Set(local.flatMap { $0.completedTrailIds }).intersection(stillValid)
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
    /// purple stroke). The trailId is preserved through the conflict /
    /// far-warning dialogs via `pendingRecordTrailId`.
    private func tryStartRecording(trailId: String? = nil) {
        guard let area else { return }
        if !location.isAuthorized {
            location.requestPermission()
            return
        }

        // Item 1 — concurrent recording prevention.
        if let active = recording.activeRecording, active.areaId != areaId {
            pendingRecordTrailId = trailId
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
        // Split out of `??` because `??` takes an autoclosure that can't
        // host an `await`.
        let trails: [Trail]
        if let cached = areas.cachedArea(id: active.areaId) {
            trails = cached.trails
        } else {
            trails = (await areas.area(id: active.areaId))?.trails ?? []
        }
        _ = await recording.stopRecording(trails: trails)
        startRecordingNow(trailId: trailId)
    }

    private func trailListSheet(area: Area, filtered: [Trail]) -> some View {
        // Pass 5 on the panel layout. Each previous attempt fixed one
        // axis of the problem and broke another:
        //   - .offset trick was smooth but ScrollView bounds wrong
        //   - .frame trick had right bounds but glitchy drag
        // This pass keeps the .frame approach but adds .geometryGroup()
        // to isolate the panel's layout from the parent. Without it,
        // every trailListHeight tick during a drag invalidates layout
        // up the parent chain (TrailMapView, the bottom controls VStack,
        // etc.) which is what was producing the visible stutter.
        // Combined with .interactiveSpring (designed for direct
        // manipulation) for the release snap.
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
                    statusFilter: $statusFilter,
                    difficultyFilter: $difficultyFilter,
                    lengthFilter: $lengthFilter,
                    filteredTrails: filtered,
                    onRecordTrail: { trail in tryStartRecording(trailId: trail.id) }
                )
            }
            .frame(height: trailListHeight)
            .background(
                UnevenRoundedRectangle(
                    topLeadingRadius: 20,
                    bottomLeadingRadius: 0,
                    bottomTrailingRadius: 0,
                    topTrailingRadius: 20,
                    style: .continuous
                )
                .fill(.regularMaterial)
            )
            .frame(maxHeight: .infinity, alignment: .bottom)
            .geometryGroup()
            .animation(nil, value: trailListHeight)
        }
        .ignoresSafeArea(edges: .bottom)
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
                // .interactiveSpring is tuned for drag-driven animations —
                // shorter response, lower bounce than the regular spring,
                // so the snap feels like a natural continuation of the
                // user's gesture instead of a separate animation event.
                withAnimation(.interactiveSpring(response: 0.28, dampingFraction: 0.85)) {
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

/// Full-screen silhouette behind the AreaView loading state. Trails light
/// up one-by-one driven by elapsed time so the wait reads as the area
/// arriving instead of dead air. Long trails are drawn first (they carry
/// the most visual weight); then medium, then short. Total animation is
/// capped so a 200-trail area still finishes in ~2.5s.
private struct LoadingSilhouetteCanvas: View {
    let silhouette: AreaSilhouette

    @Environment(\.colorScheme) private var colorScheme
    @State private var startDate = Date()

    private var orderedLines: [SilhouetteLine] {
        // Longest segments first → big spines reveal early, capillaries
        // fill in afterwards.
        silhouette.l.sorted { $0.p.count > $1.p.count }
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

                let baseOpacity: Double = colorScheme == .dark ? 0.55 : 0.35
                let totalAnimation: TimeInterval = 2.5
                let perLineDuration: TimeInterval = 0.45
                // Stagger each line's start so the whole reveal completes
                // within totalAnimation regardless of trail count.
                let stagger = max(0.018, (totalAnimation - perLineDuration) / Double(max(1, lines.count - 1)))
                let elapsed = context.date.timeIntervalSince(startDate)

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
                        let y = yOffset + canvasH - (lat - bbox.s) * scale
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
                    // Slight pop while a line is in-flight (progress < 1)
                    // so the leading edge feels brighter than the settled
                    // body of already-revealed trails.
                    let opacity = progress < 1 ? min(1.0, baseOpacity + 0.25) : baseOpacity
                    ctx.stroke(
                        trimmed,
                        with: .color(color.opacity(opacity)),
                        style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round)
                    )
                }
            }
        }
        .onAppear { startDate = Date() }
    }
}
