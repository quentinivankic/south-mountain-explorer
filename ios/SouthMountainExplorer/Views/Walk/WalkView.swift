import SwiftUI
import MapKit
import CoreLocation

/// Area-less "start anywhere" walk. Full-screen map of every trail from
/// the ~12 nearest areas (within 20 mi); one Start button; at stop time
/// `RecordingService.stopWalk` credits trail coverage/completions to
/// EVERY loaded area the GPS path touched. No area selection, no
/// fumbling — built for "I'm wandering a city and its parks today."
///
/// The map reuses `MapKitMapView` (which is area-agnostic where it
/// matters: completions arrive as a flat id set) fed with a SYNTHETIC
/// merged Area that exists only in this view — it is never handed to
/// any service. Trails are deduped by id across areas: ids embed
/// globally-unique OSM way ids, so a duplicate means the same physical
/// trail is inside two overlapping areas, and duplicates would orphan
/// unstyleable overlays in the map layer.
struct WalkView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AreaDataService.self) private var areas
    @Environment(LocationService.self) private var location
    @Environment(RecordingService.self) private var recording
    @Environment(ProgressService.self) private var progress

    @State private var loadedAreas: [Area] = []
    @State private var loadState: LoadState = .locating
    @State private var mergedArea: Area? = nil
    @State private var selectedTrailId: String? = nil
    @State private var cameraTarget: MapTarget = .region(
        centerLat: 39.5, centerLon: -98.35, latDelta: 30, lonDelta: 30
    )
    @State private var cameraTick = 0
    @State private var showSummary = false
    @State private var finishedWalk: FinishedRecording? = nil

    private enum LoadState: Equatable {
        case locating
        case loading(done: Int, total: Int)
        case ready
        case noLocation
        case noAreas
    }

    /// Credit radius. Areas whose CENTER is within this many miles of
    /// the user when the walk screen opens are loaded; capped at
    /// `maxAreas` nearest because dense regions (the Bay Area) can have
    /// dozens in range and each one costs a geometry fetch + map
    /// overlays.
    private static let radiusMi = 20.0
    private static let maxAreas = 12

    private var isWalking: Bool { recording.activeRecording?.mode == .walk }

    var body: some View {
        ZStack(alignment: .bottom) {
            if let merged = mergedArea {
                MapKitMapView(
                    area: merged,
                    activeRecording: isWalking ? recording.activeRecording : nil,
                    haloSegments: [],
                    liveHaloSegments: [],
                    selectedTrailWalkedSegments: [],
                    selectedTrailId: $selectedTrailId,
                    visibleTrailIds: nil,
                    completedTrailIds: completedTrailIds,
                    cameraTarget: cameraTarget,
                    cameraTick: cameraTick,
                    showsUserLocation: true,
                    userTrackingMode: .none,
                    onUserGestureRegionChange: nil
                )
                .ignoresSafeArea()
            } else {
                Color(.systemBackground).ignoresSafeArea()
            }

            statusOverlay

            VStack(spacing: 12) {
                if isWalking {
                    WalkRecordingPanel(walkAreas: loadedAreas) { finished in
                        finishedWalk = finished
                        if finished != nil {
                            showSummary = true
                        } else {
                            dismiss()
                        }
                    }
                } else if loadState == .ready {
                    startButton
                }
            }
            .padding(.bottom, 24)
        }
        .overlay(alignment: .topLeading) { closeButton }
        .overlay(alignment: .topTrailing) {
            if mergedArea != nil { recenterButton }
        }
        .task { await load() }
        .sheet(isPresented: $showSummary, onDismiss: { dismiss() }) {
            if let finished = finishedWalk {
                WalkSummarySheet(finished: finished, walkAreas: loadedAreas)
            }
        }
    }

    // MARK: - Chrome

    private var closeButton: some View {
        Button {
            // Dismissing while walking keeps the recording alive — the
            // app-wide banner stays up and taps back into this screen.
            dismiss()
        } label: {
            Image(systemName: "xmark")
                .font(.body.weight(.semibold))
                .frame(width: 36, height: 36)
                .compatibleGlass(in: .circle)
        }
        .padding(.top, 8)
        .padding(.leading, 16)
    }

    private var recenterButton: some View {
        Button {
            centerOnUser()
        } label: {
            Image(systemName: "location.fill")
                .font(.body.weight(.semibold))
                .frame(width: 36, height: 36)
                .compatibleGlass(in: .circle)
        }
        .accessibilityLabel("Recenter on my location")
        .padding(.top, 8)
        .padding(.trailing, 16)
    }

    private var startButton: some View {
        Button {
            startWalk()
        } label: {
            Label("Start Walk", systemImage: "figure.walk")
                .font(.headline)
                .foregroundStyle(.white)
                .padding(.horizontal, 28)
                .padding(.vertical, 14)
                .background(Color.accentColor, in: Capsule())
        }
    }

    @ViewBuilder
    private var statusOverlay: some View {
        switch loadState {
        case .locating:
            statusCard("Finding you…", detail: nil, spinner: true)
        case .loading(let done, let total):
            statusCard("Loading nearby trails…", detail: "\(done) of \(total) areas", spinner: true)
        case .noLocation:
            statusCard(
                "Location needed",
                detail: "A walk records where you go, so TrekDex needs your location. Enable it in Settings → Privacy → Location Services.",
                spinner: false
            )
        case .noAreas:
            statusCard(
                "No trails nearby",
                detail: "No trail areas within \(Int(Self.radiusMi)) miles. You can still record inside any area from its page.",
                spinner: false
            )
        case .ready:
            EmptyView()
        }
    }

    private func statusCard(_ title: String, detail: String?, spinner: Bool) -> some View {
        VStack(spacing: 10) {
            if spinner { ProgressView() }
            Text(title).font(.headline)
            if let detail {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(24)
        .frame(maxWidth: 300)
        .compatibleGlass(in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        .allowsHitTesting(false)
    }

    // MARK: - Data

    /// Flat union of completed trail ids across every loaded area —
    /// exactly what MapKitMapView wants for mint styling.
    private var completedTrailIds: Set<String> {
        var ids: Set<String> = []
        for area in loadedAreas {
            ids.formUnion(progress.completedTrails(in: area.id).keys)
        }
        return ids
    }

    private func load() async {
        if location.isAuthorized {
            location.startLiveTracking()
        }

        // Resuming an in-progress walk (banner tap / app relaunch):
        // rebuild from the recording's own area list, not a fresh
        // nearby query — the user may be miles from the start point.
        let resumeIds = (recording.activeRecording?.mode == .walk)
            ? recording.activeRecording?.nearbyAreaIds
            : nil

        var areaIds: [String] = resumeIds ?? []
        var center: CLLocationCoordinate2D? = location.userLocation ?? location.liveLocation

        if areaIds.isEmpty {
            // Give a cold GPS a few seconds to produce a fix.
            var attempts = 0
            while center == nil, attempts < 10 {
                try? await Task.sleep(for: .milliseconds(700))
                center = location.userLocation ?? location.liveLocation
                attempts += 1
            }
            guard let loc = center else {
                loadState = .noLocation
                return
            }
            areaIds = areas.nearby(lat: loc.latitude, lon: loc.longitude, limit: 40)
                .filter { summary in
                    MapMath.haversineMeters(
                        lat1: loc.latitude, lon1: loc.longitude,
                        lat2: summary.centerLat, lon2: summary.centerLon
                    ) / 1609.344 <= Self.radiusMi
                }
                .prefix(Self.maxAreas)
                .map(\.id)
        }

        guard !areaIds.isEmpty else {
            loadState = .noAreas
            return
        }

        // Fetch geometries sequentially with progress — each decode
        // runs on the main actor, so a tight 12-way fan-out would jank.
        // Warm caches (favorites / previously opened) return instantly.
        var loaded: [Area] = []
        for (i, id) in areaIds.enumerated() {
            loadState = .loading(done: i, total: areaIds.count)
            if let area = await areas.area(id: id) {
                loaded.append(area)
            }
        }
        guard !loaded.isEmpty else {
            loadState = .noAreas
            return
        }
        loadedAreas = loaded
        mergedArea = Self.merged(from: loaded)
        loadState = .ready
        centerOnUser()
    }

    /// One synthetic Area for the map layer only. Stable id — the map
    /// tears down and rebuilds every overlay when `area.id` changes, so
    /// it must not vary across body evaluations.
    private static func merged(from areas: [Area]) -> Area {
        var seen: Set<String> = []
        var trails: [Trail] = []
        for area in areas {
            for trail in area.trails where seen.insert(trail.id).inserted {
                trails.append(trail)
            }
        }
        let first = areas[0]
        return Area(
            id: "walk-session",
            name: "Your Walk",
            subtitle: first.subtitle,
            centerLat: first.centerLat,
            centerLon: first.centerLon,
            zoom: 13,
            bbox: nil,
            trails: trails,
            trailCount: trails.count,
            totalMi: nil,
            cachedAt: nil
        )
    }

    private func centerOnUser() {
        guard let loc = location.userLocation ?? location.liveLocation else { return }
        // ~2.5 mi window — enough context to see surrounding trails
        // without rendering the whole 20 mi radius at once.
        cameraTarget = .region(
            centerLat: loc.latitude,
            centerLon: loc.longitude,
            latDelta: 0.036,
            lonDelta: 0.036
        )
        cameraTick += 1
    }

    private func startWalk() {
        guard let nearest = loadedAreas.first else { return }
        // loadedAreas preserves nearby()'s distance ordering, so the
        // first loaded area is the primary the walk files under.
        recording.startWalk(
            primaryAreaId: nearest.id,
            nearbyAreaIds: loadedAreas.map(\.id)
        )
    }
}

/// Lean recording panel for walks: live elevation strip + distance /
/// duration / pace + stop. No ETA (walks have no target trail), no
/// suggestions. Stop & Save runs the multi-area `stopWalk` against
/// every loaded area's dense geometry.
struct WalkRecordingPanel: View {
    let walkAreas: [Area]
    let onStop: (FinishedRecording?) -> Void

    @Environment(RecordingService.self) private var recording
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var elapsed: TimeInterval = 0
    @State private var timer: Timer? = nil
    @State private var isStopping = false
    @State private var showStopConfirm = false
    @State private var showDiscardConfirm = false

    private var rec: ActiveRecording? { recording.activeRecording }

    var body: some View {
        VStack(spacing: 12) {
            if let rec, let stats = elevationStats(path: rec.path) {
                ElevationProfileView(
                    stats: stats,
                    totalDistanceMeters: rec.distanceMi * 1609.344
                )
                .frame(height: 70)
                .transition(.opacity)
            }

            HStack(spacing: 12) {
                VStack(spacing: 2) {
                    Image(systemName: "record.circle.fill")
                        .foregroundStyle(.red)
                        .font(.title2)
                        .symbolEffect(.pulse)
                    Text("REC")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.red)
                }

                Divider().frame(height: 40)

                statColumn(label: "Distance", value: UnitFormatter.distance(miles: rec?.distanceMi ?? 0, units: units))
                statColumn(label: "Duration", value: formattedElapsed)
                statColumn(label: "Pace",
                           value: UnitFormatter.pace(metersPerSecond: recording.smoothedPaceMetersPerSec() ?? 0,
                                                     units: units))

                Button {
                    showStopConfirm = true
                } label: {
                    if isStopping {
                        ProgressView()
                            .frame(width: 56, height: 56)
                    } else {
                        Image(systemName: "stop.circle.fill")
                            .font(.system(size: 44))
                            .foregroundStyle(.red)
                    }
                }
                .disabled(isStopping)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
        .compatibleGlass(in: .rect(cornerRadius: 24))
        .padding(.horizontal, 16)
        .onAppear { startTimer() }
        .onDisappear { timer?.invalidate() }
        .confirmationDialog(
            "Stop this walk?",
            isPresented: $showStopConfirm,
            titleVisibility: .visible
        ) {
            Button("Stop & Save", role: .destructive) { stopWalk() }
            Button("Stop & Discard", role: .destructive) { showDiscardConfirm = true }
            Button("Keep Walking", role: .cancel) { }
        } message: {
            Text(stopMessage)
        }
        .confirmationDialog(
            "Discard this walk?",
            isPresented: $showDiscardConfirm,
            titleVisibility: .visible
        ) {
            Button("Discard", role: .destructive) { discardWalk() }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("This walk won't be saved to history and your trail coverage won't update. This can't be undone.")
        }
    }

    private var stopMessage: String {
        let dist = UnitFormatter.distance(miles: rec?.distanceMi ?? 0, units: units)
        return "\(dist) recorded so far. Save adds it to history and credits any trails you covered — in every area you touched."
    }

    private func statColumn(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.headline.monospacedDigit())
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity)
    }

    private var formattedElapsed: String {
        let h = Int(elapsed) / 3600
        let m = (Int(elapsed) % 3600) / 60
        let s = Int(elapsed) % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%02d:%02d", m, s)
    }

    private func startTimer() {
        if let rec {
            elapsed = Date().timeIntervalSince(rec.startedAt)
        }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            Task { @MainActor in
                if let rec = recording.activeRecording {
                    elapsed = Date().timeIntervalSince(rec.startedAt)
                }
            }
        }
    }

    private func stopWalk() {
        isStopping = true
        timer?.invalidate()
        Task {
            // Dense geometry per area so the completion gate's fraction
            // denominator is the raw node count (same reason
            // RecordingPanel feeds rawTrails to stopRecording).
            let trailsByArea = Dictionary(
                uniqueKeysWithValues: walkAreas.map { ($0.id, $0.rawTrails ?? $0.trails) }
            )
            let finished = await recording.stopWalk(trailsByArea: trailsByArea)
            onStop(finished)
            isStopping = false
        }
    }

    private func discardWalk() {
        timer?.invalidate()
        recording.discardRecording()
        onStop(nil)
    }
}

/// Post-walk summary: totals + a per-area breakdown of completions and
/// revisits, with trail names resolved from the loaded areas.
struct WalkSummarySheet: View {
    let finished: FinishedRecording
    let walkAreas: [Area]

    @Environment(\.dismiss) private var dismiss
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    private struct AreaResult: Identifiable {
        let id: String
        let name: String
        let completed: [String]
        let revisited: [String]
    }

    private var areaResults: [AreaResult] {
        let completions = finished.multiAreaCompletions ?? [:]
        let revisits = finished.multiAreaRevisited ?? [:]
        var results: [AreaResult] = []
        for area in walkAreas {
            let completed = completions[area.id] ?? []
            let revisited = revisits[area.id] ?? []
            guard !completed.isEmpty || !revisited.isEmpty else { continue }
            results.append(AreaResult(
                id: area.id,
                name: area.name,
                completed: completed.map { trailName($0, in: area) }.sorted(),
                revisited: revisited.map { trailName($0, in: area) }.sorted()
            ))
        }
        return results
    }

    private func trailName(_ trailId: String, in area: Area) -> String {
        (area.rawTrails ?? area.trails).first { $0.id == trailId }?.name ?? trailId
    }

    private var totalCompleted: Int {
        (finished.multiAreaCompletions ?? [:]).values.map(\.count).reduce(0, +)
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 20) {
                        summaryStat(label: "Distance", value: UnitFormatter.distance(miles: finished.distanceMi, units: units))
                        summaryStat(label: "Duration", value: durationString)
                        summaryStat(label: "Pace", value: paceString)
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }

                if areaResults.isEmpty {
                    Section {
                        Label("No trail completions this time — the walk still counts in your history and coverage.",
                              systemImage: "figure.walk")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(areaResults) { result in
                        Section(result.name) {
                            ForEach(result.completed, id: \.self) { name in
                                Label(name, systemImage: "checkmark.seal.fill")
                                    .foregroundStyle(.green)
                            }
                            ForEach(result.revisited, id: \.self) { name in
                                Label(name, systemImage: "arrow.clockwise.circle.fill")
                                    .foregroundStyle(.cyan)
                            }
                        }
                    }
                }
            }
            .navigationTitle(totalCompleted > 0 ? "\(totalCompleted) trails completed!" : "Walk Saved")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func summaryStat(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.headline.monospacedDigit())
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private var durationString: String {
        let h = finished.durationSeconds / 3600
        let m = (finished.durationSeconds % 3600) / 60
        if h > 0 { return "\(h)h \(m)m" }
        return "\(m)m"
    }

    private var paceString: String {
        guard finished.durationSeconds > 0, finished.distanceMi > 0 else { return "—" }
        let mps = finished.distanceMi * 1609.344 / Double(finished.durationSeconds)
        return UnitFormatter.pace(metersPerSecond: mps, units: units)
    }
}
