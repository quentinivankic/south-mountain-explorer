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
    let pastPaths: [[GpsPoint]]
    let recenterTick: Int
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

    /// Pre-built spatial index over every trail node in the area, used
    /// by the halo's `onTrailSegments` filter. Built once on appear so
    /// new hikes don't rebuild a 5000-node grid.
    @State private var cachedTrailGrid = SpatialGrid()
    /// Per-past-hike pre-filtered halo segments (the on-trail subset
    /// of each recorded GPS path). Rebuilt on appear and whenever
    /// `pastPaths.count` changes — i.e. only when a new hike
    /// finishes. Passed straight through to `MapKitMapView`, which
    /// renders each segment as an `MKPolyline` overlay.
    @State private var cachedHaloSegments: [[[CLLocationCoordinate2D]]] = []

    /// Where the camera should be. Applied by `MapKitMapView`
    /// whenever `cameraTick` changes — the tick is the "go!" signal,
    /// the target is the payload. This indirection keeps unrelated
    /// view-state updates from accidentally re-framing the map.
    @State private var cameraTarget: MapTarget
    @State private var cameraTick: Int = 0

    init(
        area: Area,
        activeRecording: ActiveRecording?,
        pastPaths: [[GpsPoint]],
        recenterTick: Int,
        selectedTrailId: Binding<String?>,
        visibleTrailIds: Set<String>? = nil,
        bottomInset: CGFloat = 0,
        trackingMode: Binding<MapTrackingMode>
    ) {
        self.area = area
        self.activeRecording = activeRecording
        self.pastPaths = pastPaths
        self.recenterTick = recenterTick
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
        ZStack(alignment: .topTrailing) {
            MapKitMapView(
                area: area,
                activeRecording: activeRecording,
                haloSegments: cachedHaloSegments,
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
                userTrackingMode: .none
            )

            if showDebugHUD {
                DebugHUDView(diagnostics: MapDiagnostics.shared)
                    .padding(.top, 72)
                    .padding(.trailing, 12)
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
            // Build the spatial grid once on appear from the dense
            // pre-decimation node set (rawTrails when available). The
            // grid feeds the halo on-trail filter — building from
            // decimated nodes leaves 25-30 m gaps that the user's
            // raw GPS path drops through, dropping halo segments.
            let gridTrails = area.rawTrails ?? area.trails
            var grid = SpatialGrid()
            for trail in gridTrails {
                for seg in trail.segments {
                    for node in seg where node.count >= 2 {
                        grid.insert(node)
                    }
                }
            }
            cachedTrailGrid = grid
            cachedHaloSegments = pastPaths.map { onTrailSegments($0, grid: grid) }
            centerOnArea()
        }
        .onChange(of: pastPaths.count) { _, _ in
            // New hike finished and AreaView reloaded pastPaths.
            // Refresh the halo cache against the existing grid.
            cachedHaloSegments = pastPaths.map { onTrailSegments($0) }
        }
        .onChange(of: selectedTrailId) { _, newId in
            guard let id = newId,
                  let trail = area.trails.first(where: { $0.id == id }) else {
                centerOnArea()
                return
            }
            centerOn(trail: trail)
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
            updateTrackedPosition()
        case .followHeading:
            location.startLiveTracking()
            location.startHeadingUpdates()
            updateTrackedPosition()
        }
    }

    /// Position the camera on the user with the bottomInset shift
    /// (same math the previous SwiftUI-Map version used). For
    /// followHeading the shift is rotated into the camera's frame so
    /// "above the panel" stays above the panel after rotation, and
    /// the target uses MKMapCamera with heading rather than a region
    /// (regions can't carry a heading).
    private func updateTrackedPosition() {
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

        if trackingMode == .followHeading {
            // distance ~6000 m roughly matches the vertical span of
            // MKCoordinateRegion(latitudinalMeters: 1500) in portrait
            // (region fits the SHORTER axis = width, so vertical span
            // is ~3.3 km on a typical phone).
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

    private func setCameraTarget(_ target: MapTarget) {
        cameraTarget = target
        cameraTick &+= 1
    }

    // MARK: - Halo segment filter

    /// Split a recorded GPS path into runs of consecutive points that
    /// lie within `bufferM` of any trail node. Off-trail runs
    /// (e.g. commuting from home to the trailhead) drop out so the
    /// cyan halo only paints actual trail coverage. The grid is
    /// usually pulled from `cachedTrailGrid`, but `.onAppear` passes
    /// the freshly-built grid explicitly because @State writes within
    /// the same closure aren't guaranteed to be visible to subsequent
    /// reads in that closure.
    private func onTrailSegments(
        _ path: [GpsPoint],
        grid: SpatialGrid? = nil
    ) -> [[CLLocationCoordinate2D]] {
        let g = grid ?? cachedTrailGrid
        let bufferM = 30.0
        var segments: [[CLLocationCoordinate2D]] = []
        var current: [CLLocationCoordinate2D] = []
        for p in path {
            guard p.count >= 2 else { continue }
            if g.hasNeighbor(lat: p[0], lon: p[1], withinMeters: bufferM) {
                current.append(CLLocationCoordinate2D(latitude: p[0], longitude: p[1]))
            } else if !current.isEmpty {
                if current.count >= 2 { segments.append(current) }
                current.removeAll(keepingCapacity: true)
            }
        }
        if current.count >= 2 { segments.append(current) }
        return segments
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
