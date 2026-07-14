import SwiftUI
import MapKit

/// Three-state camera tracking cycle for the map. Mirrors Apple Maps'
/// own location button — outline (free), filled (follow), filled-with-
/// heading (follow + rotate). Owned by `AreaView` so the rotation
/// button there can both cycle and read the current mode for its icon.
enum MapTrackingMode: Int, CaseIterable {
    /// Camera doesn't follow the user; map stays where the user last
    /// panned / where it was framed on open.
    case free
    /// Camera pans to follow the user; north stays up.
    case follow
    /// Camera follows AND rotates so the user's heading is "up".
    case followHeading

    var next: MapTrackingMode {
        switch self {
        case .free: return .follow
        case .follow: return .followHeading
        case .followHeading: return .free
        }
    }

    var symbol: String {
        switch self {
        case .free: return "location"
        case .follow: return "location.fill"
        case .followHeading: return "location.north.fill"
        }
    }

    var accessibilityLabel: String {
        switch self {
        case .free: return "Map tracking off"
        case .follow: return "Follow user location"
        case .followHeading: return "Follow user location and heading"
        }
    }

    /// Short user-facing label for the toast that pops up when the
    /// rotation cycle button advances. Self-documenting on first use.
    var toastLabel: String {
        switch self {
        case .free: return "Map unlocked"
        case .follow: return "Following your location"
        case .followHeading: return "Following your direction"
        }
    }
}

/// SwiftUI shell that owns the map's state (camera target, tracking
/// mode, halo cache) and hands the actual rendering off to a UIKit
/// `MKMapView` via `MapKitMapView`. The wrapper handles the heavy
/// lifting (overlay reconciliation, custom renderers, viewport
/// culling) — this view just drives camera moves and refreshes the
/// halo cache when a new hike finishes.
///
/// Previously this view rendered overlays directly through SwiftUI's
/// `Map { ... }` content builder, which re-diffed the entire overlay
/// tree on every camera-end change. At 200+ overlays the diff
/// dominated frame time and the Map view would intermittently drop
/// its content under render pressure (the "map disappears" symptom
/// from the build-8 device test). Build 9 moves to `MKMapView`.
struct TrailMapView: View {
    let area: Area
    let activeRecording: ActiveRecording?
    /// Past hikes in this area with timestamps. Cyan halo uses
    /// just the paths; the orange walked-since-completion overlay
    /// (computed for whichever trail is currently selected) needs
    /// the timestamps to filter against `ProgressService.completionDate`.
    let pastHikes: [PastHike]
    let recenterTick: Int
    /// Bump from `AreaView` when the user taps Switch on the retarget
    /// or suggestion banner. Forces a re-fit of the camera around
    /// `selectedTrailId` + the user's current location, even when
    /// `selectedTrailId` is unchanged (the banner shows up because
    /// the user already tapped that trail, so the binding is
    /// already pointing at it and `.onChange(of: selectedTrailId)`
    /// would not fire).
    let centerOnSwitchedTrailTick: Int
    @Binding var selectedTrailId: String?
    /// nil = render every trail. Non-nil = only render trails whose id is
    /// in this set (plus the recording trail and the selected trail, which
    /// always render so the user can see what they tapped or what they're
    /// recording even if a filter would otherwise hide it).
    let visibleTrailIds: Set<String>?
    /// Height (in points) of UI chrome covering the bottom of the map —
    /// recording panel, trail list sheet, etc. Used by `centerOnUser` to
    /// shift the camera south so the user dot lands in the geometric
    /// middle of the *visible* map instead of the full screen.
    let bottomInset: CGFloat
    /// Three-state camera tracking cycle. AreaView owns the state via
    /// `@State`; the rotation button there reads it for the icon and
    /// flips it on tap. TrailMapView reacts via `.onChange` to apply
    /// the new mode (and to update the camera as live location /
    /// heading samples arrive).
    @Binding var trackingMode: MapTrackingMode

    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location

    /// Trail-snapped runs for the cyan lifetime "walked here" halo
    /// (`trailSnappedHaloRuns`), wrapped in a single outer group.
    /// Rebuilt on appear and whenever `pastHikes.count` or the set of
    /// completed trails changes. Passed straight through to
    /// `MapKitMapView`, which renders each run as an `MKPolyline`.
    @State private var cachedHaloSegments: [[[CLLocationCoordinate2D]]] = []
    /// On-trail-filtered segments of the **live** recording's GPS
    /// path. Recomputed at most once per second from
    /// Trail-polyline-snapped runs covered by the in-progress
    /// recording. Each element is a polyline ALONG a trail (not
    /// along the GPS scatter) — same "run of ≥ 2 consecutive
    /// covered nodes at 10m" rule used by the post-completion
    /// orange overlay. Rendered in purple over the trail
    /// polyline so the user sees segments snap to the trail as
    /// they walk them, instead of a jittery line drawn along the
    /// raw GPS path. The raw GPS path is still drawn (also in
    /// purple, slimmer) so off-trail portions remain visible.
    /// Empty when no recording is active.
    @State private var liveHaloSegments: [[CLLocationCoordinate2D]] = []
    @State private var lastLiveHaloRecomputeAt: TimeInterval = 0
    /// Segments of the selected trail's polyline that the user has
    /// walked *since the last completion of that trail*. Rendered
    /// in orange overlaid on the existing blue trail highlight —
    /// blue = "still to do for the next completion," orange =
    /// "already covered this cycle." Recomputed on selection or
    /// when pastHikes changes. Empty when no trail is selected
    /// or when the post-completion path slice doesn't touch the
    /// trail.
    @State private var selectedTrailWalkedSegments: [[CLLocationCoordinate2D]] = []

    /// Where the camera should be. Applied by `MapKitMapView`
    /// whenever `cameraTick` changes — the tick is the "go!" signal,
    /// the target is the payload. This indirection keeps unrelated
    /// view-state updates from accidentally re-framing the map.
    @State private var cameraTarget: MapTarget
    @State private var cameraTick: Int = 0

    init(
        area: Area,
        activeRecording: ActiveRecording?,
        pastHikes: [PastHike],
        recenterTick: Int,
        centerOnSwitchedTrailTick: Int,
        selectedTrailId: Binding<String?>,
        visibleTrailIds: Set<String>? = nil,
        bottomInset: CGFloat = 0,
        trackingMode: Binding<MapTrackingMode>
    ) {
        self.area = area
        self.activeRecording = activeRecording
        self.pastHikes = pastHikes
        self.recenterTick = recenterTick
        self.centerOnSwitchedTrailTick = centerOnSwitchedTrailTick
        self._selectedTrailId = selectedTrailId
        self.visibleTrailIds = visibleTrailIds
        self.bottomInset = bottomInset
        self._trackingMode = trackingMode
        // Compute the initial camera target synchronously so the
        // first frame paints the right region — no flash to a
        // default location before .onAppear fires.
        self._cameraTarget = State(initialValue: Self.regionCoveringArea(
            area: area,
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height
        ))
    }

    /// Developer-mode HUD toggle. Off by default; flipped from
    /// Settings → Developer. When on, an overlay in the top-right
    /// corner shows FPS / overlay count / last update duration /
    /// memory footprint, drawn over the map.
    @AppStorage(StorageKeys.debugHUD) private var showDebugHUD: Bool = false

    var body: some View {
        // ZStack anchored top-LEADING — the HUD goes on the left
        // side because the top-trailing area is occupied by
        // AreaView's favorite-heart button (SwiftUI overlay) AND
        // MapKit's built-in compass (MKMapView UIKit control).
        // Top-leading is clear except for the close-X button which
        // we offset around via padding below.
        ZStack(alignment: .topLeading) {
            MapKitMapView(
                area: area,
                activeRecording: activeRecording,
                haloSegments: cachedHaloSegments,
                liveHaloSegments: liveHaloSegments,
                selectedTrailWalkedSegments: selectedTrailWalkedSegments,
                selectedTrailId: $selectedTrailId,
                visibleTrailIds: visibleTrailIds,
                completedTrailIds: completedTrailIdsForArea,
                cameraTarget: cameraTarget,
                cameraTick: cameraTick,
                showsUserLocation: true,
                // We always pass `.none` here: the bottom-inset shift
                // means we need custom camera math for tracking modes
                // (MKMapView's built-in tracking centers the dot at the
                // geometric middle of the view, which sits behind the
                // recording panel / trail list sheet). The `.onChange`
                // handlers below imperatively re-frame on each location
                // / heading update.
                userTrackingMode: .none,
                demoUserDot: demoUserDot,
                onUserGestureRegionChange: {
                    // User gesture-driven region change (pinch / pan).
                    // In follow modes, snap the camera back to the
                    // user immediately so the dot doesn't drift
                    // off-center waiting for the next GPS update
                    // (which never comes if the user is stationary).
                    // `updateTrackedPosition()` uses .followCenter
                    // which preserves whatever zoom the user just
                    // pinched to. In .free mode, do nothing — the
                    // user's pan / pinch is the new camera state.
                    if trackingMode != .free {
                        updateTrackedPosition()
                    }
                }
            )

            if showDebugHUD {
                // Top-leading, offset past the close-X button which
                // sits at ~(20, 8) above the safe area in AreaView's
                // overlay. 60pt leading gets us clear of the 36×36
                // glass-effect button + a comfortable gap.
                DebugHUDView(diagnostics: MapDiagnostics.shared)
                    .padding(.top, 56)
                    .padding(.leading, 60)
            }
        }
        .onChange(of: showDebugHUD, initial: true) { _, on in
            // The FPS counter runs a CADisplayLink — pause it when
            // the HUD is off so the display-link callback isn't
            // sitting in main's run loop doing nothing useful.
            if on {
                FPSCounter.shared.start()
            } else {
                FPSCounter.shared.stop()
            }
        }
        .onAppear {
            // Snap every past hike's GPS onto the trail network so the
            // cyan "walked here" overlay follows the trail polylines
            // exactly and overlapping passes collapse into one line
            // (see trailSnappedHaloRuns), instead of drawing the raw
            // GPS scatter.
            cachedHaloSegments = [trailSnappedHaloRuns()]
            // If the view was re-entered with a recording already in
            // progress (e.g. app foregrounded after backgrounding
            // mid-hike), populate the live halo immediately so the
            // first frame shows it instead of waiting for the next
            // GPS sample to fire `.onChange(of: liveLocation)`.
            if let path = activeRecording?.path, path.count >= 2 {
                liveHaloSegments = liveTrailSnappedRuns(path: path)
                lastLiveHaloRecomputeAt = Date().timeIntervalSince1970
            }
            // Opening the area with a hike already in progress frames the
            // camera zoomed on where you are on the trail — the same
            // ~1500 m as the recenter button — rather than the whole-park
            // overview. Uses the recording's own last GPS sample, so it
            // works even before a fresh live fix lands.
            if (activeRecording?.path.count ?? 0) >= 2 {
                centerOnActiveRecording()
            } else if let id = selectedTrailId,
                      let trail = area.trails.first(where: { $0.id == id }) {
                // Opened from a trail-search result (or any pre-selected
                // trail): frame the selected trail, not the whole area.
                // The selection is set by AreaView during the loading
                // floor — often BEFORE this view mounts — so the
                // `.onChange(of: selectedTrailId)` below never fires for
                // it and `onAppear` is the only place that can catch it.
                centerOn(trail: trail)
            } else {
                centerOnArea()
            }
        }
        .onChange(of: pastHikes.count) { _, _ in
            // New hike finished and AreaView reloaded pastHikes —
            // re-snap the walked-here overlay to include it.
            cachedHaloSegments = [trailSnappedHaloRuns()]
            // Same trigger — refresh the walked-since-completion
            // overlay so the just-finished hike contributes.
            recomputeWalkedSinceCompletion()
        }
        .onChange(of: completedTrailIdsForArea) { _, _ in
            // A trail just completed (live, mid-hike, or on area open):
            // re-snap so the walked-here overlay stops painting the
            // now-completed trail (trailSnappedHaloRuns excludes it).
            cachedHaloSegments = [trailSnappedHaloRuns()]
        }
        .onChange(of: selectedTrailId) { _, newId in
            // Recompute the orange walked-since-completion overlay
            // for the newly-selected trail. Cheap — one trail at a
            // time. Clears to empty when nothing's selected.
            recomputeWalkedSinceCompletion()
            guard let id = newId,
                  let trail = area.trails.first(where: { $0.id == id }) else {
                // Deselecting (tap on empty map, or a banner clears the
                // selection). When the user is just browsing — not
                // recording, and the camera isn't locked to their
                // location — pull back to the whole-area overview so the
                // map matches "nothing selected." During a recording or
                // an active follow mode we leave the camera where it is:
                // yanking it off the user's position on a deselect (e.g.
                // the retarget banner's dismiss) would be disorienting,
                // which is why an unconditional recenter was avoided
                // before.
                if activeRecording == nil && trackingMode == .free {
                    centerOnArea()
                }
                return
            }
            centerOn(trail: trail)
        }
        .onChange(of: centerOnSwitchedTrailTick) { _, _ in
            // Fired by AreaView when Switch is tapped on the retarget
            // or suggestion banner. Fit the camera around the new
            // active trail PLUS the user's current location so they
            // can see both. Falls back to centerOn(trail:) if we
            // don't have a fresh location fix yet.
            guard let id = selectedTrailId,
                  let trail = area.trails.first(where: { $0.id == id }) else {
                return
            }
            centerOnUserAndTrail(trail)
        }
        .onChange(of: recenterTick) { _, _ in
            // Manual recenter — always a one-shot center on user with
            // no rotation. If the user is currently in a tracking
            // mode, drop back to .free so the camera doesn't
            // immediately re-engage tracking and override the
            // recenter.
            trackingMode = .free
            centerOnUser()
        }
        .onChange(of: trackingMode, initial: false) { _, newMode in
            applyTrackingMode(newMode)
        }
        // While in a tracking mode, push every new GPS sample (and
        // every heading change in followHeading) through the same
        // shifted-center math the recenter button uses, so the user
        // dot lands above the bottom panel — not behind it like
        // MapKit's built-in .userLocation camera does.
        .onChange(of: location.liveLocation) { _, _ in
            if trackingMode != .free { updateTrackedPosition() }
            recomputeLiveHaloIfNeeded()
        }
        .onChange(of: activeRecording?.path.count ?? 0) { _, _ in
            // Backup trigger — `location.liveLocation` updates the
            // path indirectly via RecordingService's polling loop,
            // but if SwiftUI coalesces the location change with the
            // path-append (same render pass) we'd otherwise miss
            // the new sample. Path-count change guarantees a recompute
            // whenever a fresh sample lands.
            recomputeLiveHaloIfNeeded()
        }
        .onChange(of: activeRecording == nil) { _, ended in
            // Clear the live halo when recording stops. The just-
            // finished hike's segments will land in `cachedHaloSegments`
            // on the next `pastPaths.count` change and render in the
            // standard cyan past-hike style.
            if ended {
                liveHaloSegments = []
                lastLiveHaloRecomputeAt = 0
            }
        }
        .onChange(of: location.liveHeading) { _, _ in
            if trackingMode == .followHeading { updateTrackedPosition() }
        }
        .onDisappear {
            // Tear down the FPS sampler when leaving the area so the
            // CADisplayLink isn't sitting in the main run loop on
            // every other screen for no benefit. Idempotent — safe
            // even when the HUD was never enabled.
            FPSCounter.shared.stop()
        }
    }

    /// Set of trail ids in this area that ProgressService considers
    /// complete. Recomputed each body eval — the dict is small (<50
    /// completed trails per area in practice) so the allocation is
    /// negligible, and going through @Observable here means the
    /// MapKitMapView gets a stable Equatable input that detects
    /// toggles without manual plumbing.
    private var completedTrailIdsForArea: Set<String> {
        Set(progress.completedTrails(in: area.id).keys)
    }

    // MARK: - Camera control

    private func applyTrackingMode(_ mode: MapTrackingMode) {
        switch mode {
        case .free:
            location.stopHeadingUpdates()
            centerOnUser()
        case .follow:
            // Ensure live location is pumping (idempotent — no-op if
            // already running, e.g. during a recording). Heading
            // isn't needed for plain follow.
            location.startLiveTracking()
            location.stopHeadingUpdates()
            updateTrackedPosition(resetZoom: true)
        case .followHeading:
            location.startLiveTracking()
            location.startHeadingUpdates()
            updateTrackedPosition(resetZoom: true)
        }
    }

    /// Position the camera on the user with the bottomInset shift
    /// (same math the previous SwiftUI-Map version used). For
    /// followHeading the shift is rotated into the camera's frame so
    /// "above the panel" stays above the panel after rotation, and
    /// the target uses MKMapCamera with heading rather than a region
    /// (regions can't carry a heading).
    ///
    /// `resetZoom`: pass `true` on the initial mode entry to apply the
    /// default 1500m / 6000m framing; pass `false` (default) for the
    /// continuous live-tracking pans triggered by GPS / heading
    /// updates. Live-tracking pans use `.followCenter` which preserves
    /// the user's current pinch-zoom — without that, every GPS sample
    /// would re-apply the default zoom and undo any pinch the user
    /// just performed.
    private func updateTrackedPosition(resetZoom: Bool = false) {
        guard let coord = location.liveLocation ?? location.userLocation else { return }
        let heading: CLLocationDirection = trackingMode == .followHeading
            ? (location.liveHeading ?? 0)
            : 0

        let latMeters = 1500.0
        let b = UIScreen.main.bounds
        let shortDim = min(b.width, b.height)
        let metersPerPoint = latMeters / max(shortDim, 1)
        let shiftMeters = (bottomInset / 2) * metersPerPoint
        // Shift "screen down" relative to the camera. For heading=0
        // (north up) that's south. For arbitrary heading θ, screen-
        // down is the bearing (θ + 180°) measured from north.
        let radians = heading * .pi / 180
        let dLat = -shiftMeters * cos(radians) / 111_000.0
        let cosLat = max(0.0001, cos(coord.latitude * .pi / 180))
        let dLon = -shiftMeters * sin(radians) / (111_000.0 * cosLat)
        let shifted = CLLocationCoordinate2D(
            latitude: coord.latitude + dLat,
            longitude: coord.longitude + dLon
        )

        if resetZoom {
            if trackingMode == .followHeading {
                // distance ~6000 m roughly matches the vertical span of
                // MKCoordinateRegion(latitudinalMeters: 1500) in portrait
                // (region fits the SHORTER axis = width, so vertical span
                // is ~3.3 km on a typical phone).
                //
                // Pass the bottom-inset-shifted center so the dot lands
                // at the VISIBLE center (above the panel), matching the
                // steady-state .followCenter behavior below.
                setCameraTarget(.camera(
                    centerLat: shifted.latitude,
                    centerLon: shifted.longitude,
                    distance: 6000,
                    heading: heading
                ))
            } else {
                let latDelta = latMeters / 111_000.0
                let lonDelta = latMeters / (111_000.0 * cosLat)
                setCameraTarget(.region(
                    centerLat: shifted.latitude,
                    centerLon: shifted.longitude,
                    latDelta: latDelta,
                    lonDelta: lonDelta
                ))
            }
        } else {
            // Continuous live-tracking pan — preserve user's zoom.
            // Pass the RAW user coord and let `applyCameraTarget`
            // compute the bottom-inset shift using the live map
            // view's projection. The pre-shifted `shifted` value
            // above assumes the default 1500m zoom; reusing it here
            // mis-positions the dot once the user has pinched to
            // a different zoom.
            setCameraTarget(.followCenter(
                rawLat: coord.latitude,
                rawLon: coord.longitude,
                heading: trackingMode == .followHeading ? heading : nil,
                bottomInset: bottomInset
            ))
        }
    }

    private func centerOnUser() {
        guard let coord = location.userLocation else {
            centerOnArea()
            return
        }
        // 1500 m square region around the user, fed through fittedRegion
        // so the dot lands in the visible (above-panel) center.
        let latDelta = 1500.0 / 111_000.0
        let cosLat = max(0.0001, cos(coord.latitude * .pi / 180))
        let lonDelta = 1500.0 / (111_000.0 * cosLat)
        setCameraTarget(Self.fittedRegion(
            centerLat: coord.latitude,
            centerLon: coord.longitude,
            latDelta: latDelta,
            lonDelta: lonDelta,
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height
        ))
    }

    private func centerOnArea() {
        setCameraTarget(Self.regionCoveringArea(
            area: area,
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height
        ))
    }

    /// Frame the camera on an in-progress recording's current position
    /// (its last GPS sample), zoomed to the same ~1500 m as the recenter
    /// button. Used on area-open when a hike is already running so you
    /// land looking at where you are on the trail, not the whole park.
    /// Falls back to the area overview if the path has no usable point.
    private func centerOnActiveRecording() {
        guard let last = activeRecording?.path.last(where: { $0.count >= 2 }) else {
            centerOnArea()
            return
        }
        let lat = last[0], lon = last[1]
        let cosLat = max(0.0001, cos(lat * .pi / 180))
        setCameraTarget(Self.fittedRegion(
            centerLat: lat,
            centerLon: lon,
            latDelta: 1500.0 / 111_000.0,
            lonDelta: 1500.0 / (111_000.0 * cosLat),
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height
        ))
    }

    private func centerOn(trail: Trail) {
        let pts = trail.segments.flatMap { $0 }.compactMap { p -> (Double, Double)? in
            guard p.count >= 2 else { return nil }
            return (p[0], p[1])
        }
        guard !pts.isEmpty else { return }
        let lats = pts.map { $0.0 }
        let lons = pts.map { $0.1 }
        let minLat = lats.min()!, maxLat = lats.max()!
        let minLon = lons.min()!, maxLon = lons.max()!
        setCameraTarget(Self.fittedRegion(
            centerLat: (minLat + maxLat) / 2,
            centerLon: (minLon + maxLon) / 2,
            latDelta: max((maxLat - minLat) * 1.4, 0.005),
            lonDelta: max((maxLon - minLon) * 1.4, 0.005),
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height
        ))
    }

    /// Like `centerOn(trail:)` but expands the bbox to also include
    /// the user's current location. Used after a retarget Switch so
    /// the camera frames "you + the new active trail" instead of
    /// just the trail (which can leave the user off-screen if they
    /// were standing well outside the trail's extent). Falls back
    /// to `centerOn(trail:)` if we don't have a location fix yet.
    private func centerOnUserAndTrail(_ trail: Trail) {
        guard let userLoc = location.liveLocation ?? location.userLocation else {
            centerOn(trail: trail)
            return
        }
        var lats: [Double] = []
        var lons: [Double] = []
        for seg in trail.segments {
            for p in seg where p.count >= 2 {
                lats.append(p[0])
                lons.append(p[1])
            }
        }
        lats.append(userLoc.latitude)
        lons.append(userLoc.longitude)
        guard !lats.isEmpty else {
            centerOn(trail: trail)
            return
        }
        let minLat = lats.min()!, maxLat = lats.max()!
        let minLon = lons.min()!, maxLon = lons.max()!
        setCameraTarget(Self.fittedRegion(
            centerLat: (minLat + maxLat) / 2,
            centerLon: (minLon + maxLon) / 2,
            latDelta: max((maxLat - minLat) * 1.4, 0.005),
            lonDelta: max((maxLon - minLon) * 1.4, 0.005),
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height
        ))
    }

    private func setCameraTarget(_ target: MapTarget) {
        cameraTarget = target
        cameraTick &+= 1
    }

    /// Synthetic "you are here" dot for the App Store recording
    /// screenshot, pinned to the demo recording's current position (the
    /// same point `centerOnActiveRecording` frames). The CI simulator's
    /// `simctl privacy grant` doesn't reliably land as authorized before
    /// MKMapView asks CoreLocation for its built-in dot, so the shot
    /// rendered dot-less — this renders one deterministically instead.
    /// Always nil outside DEBUG + `--uitest-recording`, so no production
    /// build can ever show a fake location.
    private var demoUserDot: CLLocationCoordinate2D? {
        #if DEBUG
        guard UITestSupport.isRecordingRequested,
              let last = activeRecording?.path.last(where: { $0.count >= 2 }) else { return nil }
        return CLLocationCoordinate2D(latitude: last[0], longitude: last[1])
        #else
        return nil
        #endif
    }

    // MARK: - Live halo recompute

    /// Throttled rebuild of `liveHaloSegments` from the active
    /// recording's current path. Capped at one recompute per second
    /// so a 1 Hz GPS sample rate does at most one O(N · grid-lookup)
    /// pass per second — N typically <1000 points for the first 30
    /// minutes of a hike. No-op when no recording is active.
    private func recomputeLiveHaloIfNeeded() {
        guard let path = activeRecording?.path, path.count >= 2 else { return }
        let now = Date().timeIntervalSince1970
        guard now - lastLiveHaloRecomputeAt >= 1.0 else { return }
        lastLiveHaloRecomputeAt = now
        liveHaloSegments = liveTrailSnappedRuns(path: path)
    }

    /// Compute trail-polyline-snapped runs covered by the in-progress
    /// recording's GPS path. Runs `trailNodeRuns` (10m buffer, runs of
    /// ≥ 2 consecutive covered nodes) over every trail in the area and
    /// concatenates the results. Returns polylines that follow the
    /// trail geometry precisely, not the GPS scatter — used for the
    /// purple "you're walking this segment" rendering during a
    /// recording.
    private func liveTrailSnappedRuns(path: [GpsPoint]) -> [[CLLocationCoordinate2D]] {
        var gpsGrid = SpatialGrid()
        for p in path where p.count >= 2 {
            gpsGrid.insert(p)
        }
        let sourceTrails = area.rawTrails ?? area.trails
        var all: [[CLLocationCoordinate2D]] = []
        for trail in sourceTrails {
            all.append(contentsOf: trailNodeRuns(coveredBy: gpsGrid, in: trail))
        }
        return all
    }

    // MARK: - Walked-here (lifetime) halo

    /// Trail-polyline-snapped runs for the cyan lifetime "walked here"
    /// halo. Builds ONE GPS grid from every past hike, then walks each
    /// not-yet-completed trail's polyline and emits runs of nodes within
    /// 30 m of any GPS point (the lifetime buffer — looser than the 10 m
    /// since-completion buffer, matching `rebuildCoverageFromHistory` so
    /// the halo and the coverage bar agree on what counts as walked).
    ///
    /// Because it iterates the TRAIL nodes rather than the GPS path, the
    /// result follows the trail exactly and multiple passes over the same
    /// stretch collapse into a single run — no more raw-GPS scatter or
    /// stacked overlapping traces. Completed trails are excluded so their
    /// finished-state styling isn't smeared over.
    private func trailSnappedHaloRuns() -> [[CLLocationCoordinate2D]] {
        var gpsGrid = SpatialGrid()
        for hike in pastHikes {
            for p in hike.path where p.count >= 2 {
                gpsGrid.insert(p)
            }
        }
        let completed = completedTrailIdsForArea
        // Snap onto the DECIMATED render geometry (`area.trails`) — the exact
        // polylines the map draws — NOT the dense `rawTrails`. The two diverge
        // by up to the 5 m decimation epsilon on curves, which is invisible at
        // normal zoom but shows when zoomed in: snapping to raw made the cyan
        // drift OFF the drawn trail line (the "scatter" and the
        // cyan-with-no-line-under-it). Emitting render nodes puts the cyan
        // pixel-on-pixel over the trail; the 30 m buffer keeps detection
        // robust despite the sparser node set.
        let sourceTrails = area.trails
            .filter { !completed.contains($0.id) }
        var all: [[CLLocationCoordinate2D]] = []
        for trail in sourceTrails {
            all.append(contentsOf: trailNodeRuns(coveredBy: gpsGrid, in: trail, bufferM: 30.0))
        }
        return all
    }

    // MARK: - Walked-since-completion overlay

    /// Recompute the orange walked-since-completion segments for
    /// the currently-selected trail.
    ///
    /// "Since last completion" means: filter `pastHikes` to those
    /// ending after the trail's last completion timestamp (or all
    /// hikes when never completed). Then walk THIS trail's
    /// polyline node-by-node, marking runs of consecutive nodes
    /// within 30 m of any post-completion GPS point. Render those
    /// trail-polyline runs in orange.
    ///
    /// Note the inversion: we iterate the trail polyline (not the
    /// GPS path), so the orange line traces the trail exactly
    /// rather than wandering with the user's imperfect walking
    /// path. Single-trail scope keeps the cost down — one walk
    /// over the trail's ~100 nodes against a grid built from
    /// post-completion GPS samples.
    private func recomputeWalkedSinceCompletion() {
        guard let selectedTrailId,
              let trail = area.trails.first(where: { $0.id == selectedTrailId }) else {
            selectedTrailWalkedSegments = []
            return
        }
        let lastCompletion = progress.completionDate(areaId: area.id, trailId: selectedTrailId)
        let relevantPaths: [GpsPoint] = pastHikes
            .filter { hike in
                if let lastCompletion {
                    // Completed trail — every hike that started
                    // AFTER the completion stamp counts toward
                    // the post-completion overlay, regardless of
                    // whether it deliberately targeted this
                    // trail. Tight 10m buffer in `trailNodeRuns`
                    // does the discrimination: drift across the
                    // trail (within 10m of a node) shows orange;
                    // a hike on a far-away trail contributes
                    // nothing. Filter on `startedAt` (not
                    // `endedAt`) so the completing hike — which
                    // has startedAt before and endedAt after the
                    // completion stamp — is excluded.
                    return hike.startedAt > lastCompletion
                }
                // Never completed — all hikes count toward
                // first-completion progress.
                return true
            }
            .flatMap(\.path)
        if relevantPaths.isEmpty {
            selectedTrailWalkedSegments = []
            return
        }
        // Build a GPS-points grid (NOT a trail-nodes grid). The
        // iteration below walks the TRAIL polyline and asks
        // "any GPS point near this node?" — which gives us runs
        // along the trail itself, not segments of the GPS path.
        var gpsGrid = SpatialGrid()
        for p in relevantPaths where p.count >= 2 {
            gpsGrid.insert(p)
        }
        // Walk the trail polyline (use raw geometry when available
        // for the dense node set) and emit on-trail runs.
        let sourceTrails = area.rawTrails ?? area.trails
        let geomTrail = sourceTrails.first(where: { $0.id == trail.id }) ?? trail
        selectedTrailWalkedSegments = trailNodeRuns(coveredBy: gpsGrid, in: geomTrail)
    }

    /// Walk each segment of `trail.segments` node-by-node, emit
    /// runs of consecutive trail nodes that are within 10 m of any
    /// point in `gpsGrid`. The returned polylines are sequences of
    /// TRAIL nodes — so rendering them produces lines that follow
    /// the trail polyline precisely, not the user's GPS scatter.
    /// 10m (tighter than the 30m lifetime buffer) matches the
    /// `sinceCompletionBufferMeters` used by
    /// `rebuildCoverageFromHistory`, so the bar's "% remaining"
    /// and the orange overlay agree on what counts as "drifted
    /// across this trail" post-completion.
    private func trailNodeRuns(coveredBy gpsGrid: SpatialGrid, in trail: Trail,
                               bufferM: Double = 10.0) -> [[CLLocationCoordinate2D]] {
        var runs: [[CLLocationCoordinate2D]] = []
        for seg in trail.segments {
            var current: [CLLocationCoordinate2D] = []
            for node in seg where node.count >= 2 {
                let lat = node[0]
                let lon = node[1]
                if gpsGrid.hasNeighbor(lat: lat, lon: lon, withinMeters: bufferM) {
                    current.append(CLLocationCoordinate2D(latitude: lat, longitude: lon))
                } else if !current.isEmpty {
                    if current.count >= 2 { runs.append(current) }
                    current.removeAll(keepingCapacity: true)
                }
            }
            if current.count >= 2 { runs.append(current) }
        }
        return runs
    }

    // MARK: - Fitted-region math

    /// Frame a target lat/lon bbox in the visible portion of the map
    /// (above the bottom panel). Inflates the latitudinal span so the
    /// bbox fits in the `(1 - p)` fraction of the screen that's not
    /// covered, then shifts the center south so the bbox sits in
    /// that visible top portion. Without this, the bottom of the
    /// framed region was hidden behind the recording panel or trail
    /// list sheet.
    ///
    /// `screenHeight` is plumbed in by the caller rather than read
    /// from `UIScreen.main` inside this function so the math stays
    /// `nonisolated` and unit-testable — `UIScreen.main` is itself
    /// `@MainActor`, and pulling it into a pure helper would force
    /// every caller (including XCTests) onto the main actor for no
    /// real reason. Call sites in TrailMapView already run on the
    /// main actor, so reading `UIScreen.main` at the call site is
    /// free for them.
    nonisolated static func fittedRegion(
        centerLat: Double, centerLon: Double,
        latDelta: Double, lonDelta: Double,
        bottomInset: CGFloat,
        screenHeight: CGFloat
    ) -> MapTarget {
        // Cap p at 0.7 so a worst-case panel-covers-everything state
        // still leaves a sane minimum visible area.
        let p = min(0.7, bottomInset / max(screenHeight, 1))
        let visibleFraction = max(0.3, 1 - p)

        // Inflate the latitudinal span so the requested content fits
        // in the visible (top) portion. Longitudinal needs no
        // inflation — the panel doesn't constrain horizontally.
        let regionLatDelta = max(latDelta / visibleFraction, 0.005)
        let regionLonDelta = max(lonDelta, 0.005)

        // Shift center south by half the extra span we just added,
        // so the visible center of the region matches the requested
        // centerLat.
        let shiftLat = regionLatDelta * p / 2
        return .region(
            centerLat: centerLat - shiftLat,
            centerLon: centerLon,
            latDelta: regionLatDelta,
            lonDelta: regionLonDelta
        )
    }

    nonisolated static func regionCoveringArea(area: Area, bottomInset: CGFloat, screenHeight: CGFloat) -> MapTarget {
        let coords = area.trails
            .flatMap { $0.segments.flatMap { $0 } }
            .compactMap { p -> (lat: Double, lon: Double)? in
                guard p.count >= 2 else { return nil }
                return (p[0], p[1])
            }

        if !coords.isEmpty {
            let lats = coords.map { $0.lat }
            let lons = coords.map { $0.lon }
            let minLat = lats.min()!, maxLat = lats.max()!
            let minLon = lons.min()!, maxLon = lons.max()!
            return fittedRegion(
                centerLat: (minLat + maxLat) / 2,
                centerLon: (minLon + maxLon) / 2,
                // 1.3x padding so trails don't kiss the visible edges.
                latDelta: max((maxLat - minLat) * 1.3, 0.01),
                lonDelta: max((maxLon - minLon) * 1.3, 0.01),
                bottomInset: bottomInset,
                screenHeight: screenHeight
            )
        }

        if let bbox = area.bbox, bbox.count == 4 {
            return fittedRegion(
                centerLat: (bbox[1] + bbox[3]) / 2,
                centerLon: (bbox[0] + bbox[2]) / 2,
                latDelta: max(abs(bbox[3] - bbox[1]) * 1.2, 0.01),
                lonDelta: max(abs(bbox[2] - bbox[0]) * 1.2, 0.01),
                bottomInset: bottomInset,
                screenHeight: screenHeight
            )
        }

        // Fallback: a fixed-distance camera around the area's
        // bundled center. Used only when the area carries no trail
        // geometry AND no bbox — vanishingly rare in practice.
        return .camera(
            centerLat: area.centerLat,
            centerLon: area.centerLon,
            distance: 5000,
            heading: 0
        )
    }
}
