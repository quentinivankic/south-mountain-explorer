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

/// Lat/lon bounding box for a trail. Used by the viewport-cull pass
/// in `trailPolylines` so SwiftUI doesn't have to diff a thousand
/// MapPolyline children when only a few hundred are on-screen.
struct DegreeBBox: Equatable {
    let minLat: Double
    let maxLat: Double
    let minLon: Double
    let maxLon: Double

    /// Does this bbox intersect a (padded) `MKCoordinateRegion`?
    /// `padding` of 1.25 means the cull keeps trails up to 25% of a
    /// span past the visible edge, so small pans don't pop trails
    /// in/out at the screen edges.
    func intersects(_ region: MKCoordinateRegion, padding: Double = 1.25) -> Bool {
        let halfLat = region.span.latitudeDelta * padding / 2
        let halfLon = region.span.longitudeDelta * padding / 2
        let rMinLat = region.center.latitude - halfLat
        let rMaxLat = region.center.latitude + halfLat
        let rMinLon = region.center.longitude - halfLon
        let rMaxLon = region.center.longitude + halfLon
        return !(maxLat < rMinLat || minLat > rMaxLat ||
                 maxLon < rMinLon || minLon > rMaxLon)
    }
}

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
    /// flips it on tap. TrailMapView reacts via `.onChange` to swap the
    /// `MapCameraPosition` between a static region and a userLocation-
    /// following one.
    @Binding var trackingMode: MapTrackingMode

    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location

    @State private var position: MapCameraPosition
    /// Pre-built spatial index over every trail node in the area, used
    /// by the halo's `onTrailSegments` filter. Built once on appear so
    /// the per-render hot path doesn't rebuild a 5000-node grid.
    @State private var cachedTrailGrid = SpatialGrid()
    /// Per-trail pre-converted polyline coords. Built once on appear
    /// so `trailPolylineLayer` doesn't `compactMap` every segment on
    /// every Map render — that allocation pressure (25k+ per render
    /// on hundred-trail areas) is what made the map glitchy whenever
    /// `position` updated frequently.
    @State private var cachedTrailCoords: [String: [[CLLocationCoordinate2D]]] = [:]
    /// Per-past-hike pre-filtered halo segments (the on-trail subset
    /// of each recorded GPS path). Rebuilt on appear and whenever
    /// `pastPaths.count` changes — i.e. only when a new hike
    /// finishes — instead of per-render.
    @State private var cachedHaloSegments: [[[CLLocationCoordinate2D]]] = []
    /// Per-trail lat/lon bounding box. Drives the viewport cull in
    /// `trailPolylines` so 200+ trail areas only build MapPolyline
    /// children for trails currently on (or near) the screen.
    @State private var cachedTrailBboxes: [String: DegreeBBox] = [:]
    /// Last reported visible camera region from `.onMapCameraChange`.
    /// `nil` until the first camera-change fires — until then we
    /// render every trail (no cull) so the first frame isn't blank.
    @State private var visibleRegion: MKCoordinateRegion? = nil

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
        // Compute the initial camera position synchronously so MapKit's
        // own .automatic frame can't briefly render a fragment of the area
        // before .onAppear fires. Pass bottomInset so the framing
        // accounts for the panel from the very first frame.
        self._position = State(initialValue: Self.regionCovering(area: area, bottomInset: bottomInset))
    }

    var body: some View {
        Map(position: $position) {
            haloPolylines
            trailPolylines
            recordingPathPolyline
            UserAnnotation()
        }
        .mapStyle(.standard(elevation: .realistic, pointsOfInterest: .excludingAll))
        .mapControls {
            // MapUserLocationButton is intentionally omitted — MapKit places it
            // at the top-right where AreaView's favorite/close buttons live, and
            // taps were leaking through to it.
            MapCompass()
            MapScaleView()
        }
        .onAppear {
            // Build all four caches (trail grid, per-trail polyline
            // coords, per-trail bbox, per-hike halo segments) in a
            // single pass over the area's trails. Per-render Map
            // work then collapses to dictionary lookups + bbox
            // intersection + MapPolyline construction.
            var grid = SpatialGrid()
            var coords: [String: [[CLLocationCoordinate2D]]] = [:]
            var bboxes: [String: DegreeBBox] = [:]
            for trail in area.trails {
                var segs: [[CLLocationCoordinate2D]] = []
                var minLat = Double.infinity, maxLat = -Double.infinity
                var minLon = Double.infinity, maxLon = -Double.infinity
                for seg in trail.segments {
                    for node in seg {
                        grid.insert(node)
                        guard node.count >= 2 else { continue }
                        if node[0] < minLat { minLat = node[0] }
                        if node[0] > maxLat { maxLat = node[0] }
                        if node[1] < minLon { minLon = node[1] }
                        if node[1] > maxLon { maxLon = node[1] }
                    }
                    let segCoords: [CLLocationCoordinate2D] = seg.compactMap { node in
                        guard node.count >= 2 else { return nil }
                        return CLLocationCoordinate2D(latitude: node[0], longitude: node[1])
                    }
                    segs.append(segCoords)
                }
                coords[trail.id] = segs
                if minLat.isFinite {
                    bboxes[trail.id] = DegreeBBox(
                        minLat: minLat, maxLat: maxLat,
                        minLon: minLon, maxLon: maxLon
                    )
                }
            }
            cachedTrailGrid = grid
            cachedTrailCoords = coords
            cachedTrailBboxes = bboxes
            // Halo cache uses the freshly-built grid (passed
            // explicitly so we don't depend on @State commit timing).
            cachedHaloSegments = pastPaths.map { onTrailSegments($0, grid: grid) }
            centerOnArea()
        }
        // Track the visible camera region so `trailPolylines` can
        // viewport-cull. `.onEnd` fires once per gesture / animation
        // settle — not per frame — which is what we want: a single
        // re-cull per pan / zoom / tracking-mode camera step.
        .onMapCameraChange(frequency: .onEnd) { context in
            visibleRegion = context.region
        }
        .onChange(of: pastPaths.count) { _, _ in
            // New hike finished and AreaView reloaded pastPaths — refresh
            // the halo cache against the (already-cached) trail grid so
            // the new path lights up without re-filtering on every render.
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
            // no rotation. If the user is currently in a tracking mode,
            // drop back to .free so the camera doesn't immediately
            // re-engage tracking and override the recenter.
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
        // MapKit's built-in .userLocation camera does. Snap (no
        // withAnimation) — wrapping these in .linear(0.3) made
        // SwiftUI re-render the whole MapContent on every animation
        // frame at 60 Hz, which thrashed the UI thread on hundred-
        // trail areas during a rotation. Discrete 2° heading steps
        // are visible but cheap; the precomputed coord caches keep
        // the per-render work small.
        .onChange(of: location.liveLocation) { _, _ in
            if trackingMode != .free { updateTrackedPosition() }
        }
        .onChange(of: location.liveHeading) { _, _ in
            if trackingMode == .followHeading { updateTrackedPosition() }
        }
    }

    // MARK: - Map content (split out so the type-checker can keep up)

    /// Cumulative-coverage halo: every past hike's GPS path drawn in
    /// cyan beneath the trail polylines so walked sections glow
    /// through. Iterates the precomputed `cachedHaloSegments`
    /// directly — no per-render spatial filtering against the trail
    /// grid.
    @MapContentBuilder
    private var haloPolylines: some MapContent {
        ForEach(Array(cachedHaloSegments.enumerated()), id: \.offset) { _, segments in
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                MapPolyline(coordinates: segment)
                    .stroke(
                        .cyan.opacity(0.55),
                        style: StrokeStyle(lineWidth: 7, lineCap: .round, lineJoin: .round)
                    )
            }
        }
    }

    /// Trail polylines themselves, with the recording-mode purple
    /// highlight folded in alongside the tap-to-highlight cyan/colored
    /// strokes (same code path as ordinary rendering, just different
    /// styling).
    @MapContentBuilder
    private var trailPolylines: some MapContent {
        let recordingTrailId = activeRecording?.trailId
        let highlightedTrailId = recordingTrailId ?? selectedTrailId
        // Two filters layered. (1) Trail-list filter (visibleTrailIds),
        // unchanged from before. (2) Viewport cull: drop trails whose
        // bbox doesn't intersect the (padded) visible camera region,
        // so 200+ trail areas only build MapPolyline children for
        // what's on screen. Both filters exempt the recording and
        // selected trails — they must stay rendered even when the
        // camera has panned away.
        let drawableTrails: [Trail] = area.trails.filter { trail in
            let isExempt = trail.id == recordingTrailId
                || trail.id == selectedTrailId
            if let allowed = visibleTrailIds,
               !allowed.contains(trail.id),
               !isExempt {
                return false
            }
            if isExempt { return true }
            if let region = visibleRegion,
               let bbox = cachedTrailBboxes[trail.id] {
                return bbox.intersects(region)
            }
            // No region reported yet (initial frame, before the first
            // .onMapCameraChange fires) — render everything so the
            // first paint isn't blank.
            return true
        }
        ForEach(drawableTrails) { trail in
            trailPolylineLayer(
                trail: trail,
                recordingTrailId: recordingTrailId,
                highlightedTrailId: highlightedTrailId
            )
        }
    }

    @MapContentBuilder
    private func trailPolylineLayer(
        trail: Trail,
        recordingTrailId: String?,
        highlightedTrailId: String?
    ) -> some MapContent {
        let isRecordingThis = trail.id == recordingTrailId
        let isSelected = trail.id == selectedTrailId
        let isHighlighted = isRecordingThis || isSelected
        let isComplete = progress.isComplete(areaId: area.id, trailId: trail.id)
        let baseColor: Color = isRecordingThis
            ? .purple
            : (isComplete ? .cyan : difficultyColor(trail.difficulty))
        let dimmed = (highlightedTrailId != nil && !isHighlighted)
        // 0.5 (vs the earlier 0.25) keeps non-active trails legible
        // next to the bold purple recording stroke without competing
        // for attention.
        let strokeColor = baseColor.opacity(dimmed ? 0.5 : 1.0)
        let lineWidth: CGFloat = isRecordingThis ? 10 : (isSelected ? 6 : 3)
        // Pre-converted CLLocationCoordinate2D arrays from the
        // .onAppear cache. Empty array if the trail isn't in the
        // cache (shouldn't happen in practice — every area trail is
        // cached on appear).
        let segCoords = cachedTrailCoords[trail.id] ?? []
        ForEach(Array(segCoords.enumerated()), id: \.offset) { _, coords in
            MapPolyline(coordinates: coords)
                .stroke(
                    strokeColor,
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
                )
        }
    }

    /// Live recording GPS path drawn over the trails so the user can
    /// see exactly where they've walked in this session.
    @MapContentBuilder
    private var recordingPathPolyline: some MapContent {
        if let rec = activeRecording, rec.path.count > 1 {
            let pathCoords = rec.path.map {
                CLLocationCoordinate2D(latitude: $0[0], longitude: $0[1])
            }
            MapPolyline(coordinates: pathCoords)
                .stroke(.blue, style: StrokeStyle(lineWidth: 4, lineCap: .round, lineJoin: .round))
        }
    }

    private func applyTrackingMode(_ mode: MapTrackingMode) {
        // One-shot mode entry uses a brief easeInOut so the camera
        // glides to its new framing. Continuous tracking updates
        // (.onChange handlers below) use a faster linear animation
        // so the camera flows with each GPS / heading sample without
        // queuing up.
        switch mode {
        case .free:
            location.stopHeadingUpdates()
            withAnimation(.easeInOut(duration: 0.3)) { centerOnUser() }
        case .follow:
            // Ensure live location is pumping (idempotent — no-op if
            // already running, e.g. during a recording). Heading
            // isn't needed for plain follow.
            location.startLiveTracking()
            location.stopHeadingUpdates()
            withAnimation(.easeInOut(duration: 0.3)) { updateTrackedPosition() }
        case .followHeading:
            location.startLiveTracking()
            location.startHeadingUpdates()
            withAnimation(.easeInOut(duration: 0.3)) { updateTrackedPosition() }
        }
    }

    /// Position the camera on the user with the bottomInset shift
    /// (same method PR #48 used for the one-shot recenter). For
    /// followHeading the shift is rotated into the camera's frame so
    /// "above the panel" stays above the panel after rotation, and
    /// the position is set via MapCamera (only camera mode supports
    /// heading; .region doesn't).
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
            // is ~3.3 km on a typical phone). distance 2500 was ~2×
            // zoomed in, which made the shift proportionally too big
            // and put the dot at the top of the visible area.
            position = .camera(MapCamera(
                centerCoordinate: shifted,
                distance: 6000,
                heading: heading,
                pitch: 0
            ))
        } else {
            position = .region(MKCoordinateRegion(
                center: shifted,
                latitudinalMeters: latMeters,
                longitudinalMeters: latMeters
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
        withAnimation {
            position = Self.fittedRegion(
                centerLat: coord.latitude,
                centerLon: coord.longitude,
                latDelta: latDelta,
                lonDelta: lonDelta,
                bottomInset: bottomInset
            )
        }
    }

    private func centerOnArea() {
        withAnimation { position = Self.regionCovering(area: area, bottomInset: bottomInset) }
    }

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

    /// Frame the camera around the union of all trail segments. Uses the
    /// actual trail geometry (not just `area.bbox`, which is sometimes nil
    /// or imprecise in the bundled data) so the user always opens to a view
    /// of the whole area rather than a random subregion.
    /// Returns a MapCameraPosition that frames a target bbox in the
    /// *visible* portion of the map (above the bottom panel).
    /// Inflates the latitudinal span so the bbox fits in the
    /// `(1 - p)` fraction of the screen that's not covered, then
    /// shifts the center south so the bbox sits in that visible top
    /// portion. Without this, the bottom of the framed region was
    /// hidden behind the recording panel / trail list sheet.
    private static func fittedRegion(
        centerLat: Double, centerLon: Double,
        latDelta: Double, lonDelta: Double,
        bottomInset: CGFloat
    ) -> MapCameraPosition {
        let screenH = UIScreen.main.bounds.height
        // Cap p at 0.7 so a worst-case panel-covers-everything state
        // still leaves a sane minimum visible area.
        let p = min(0.7, bottomInset / max(screenH, 1))
        let visibleFraction = max(0.3, 1 - p)

        // Inflate the latitudinal span so the requested content fits
        // in the visible (top) portion. Longitudinal needs no inflation
        // — the panel doesn't constrain horizontally.
        let regionLatDelta = max(latDelta / visibleFraction, 0.005)
        let regionLonDelta = max(lonDelta, 0.005)

        // Shift center south by half the extra span we just added, so
        // the visible center of the region matches the requested
        // centerLat.
        let shiftLat = regionLatDelta * p / 2
        let center = CLLocationCoordinate2D(
            latitude: centerLat - shiftLat,
            longitude: centerLon
        )
        return .region(MKCoordinateRegion(
            center: center,
            span: MKCoordinateSpan(latitudeDelta: regionLatDelta, longitudeDelta: regionLonDelta)
        ))
    }

    private static func regionCovering(area: Area, bottomInset: CGFloat) -> MapCameraPosition {
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
                bottomInset: bottomInset
            )
        }

        if let bbox = area.bbox, bbox.count == 4 {
            return fittedRegion(
                centerLat: (bbox[1] + bbox[3]) / 2,
                centerLon: (bbox[0] + bbox[2]) / 2,
                latDelta: max(abs(bbox[3] - bbox[1]) * 1.2, 0.01),
                lonDelta: max(abs(bbox[2] - bbox[0]) * 1.2, 0.01),
                bottomInset: bottomInset
            )
        }

        return .camera(MapCamera(
            centerCoordinate: CLLocationCoordinate2D(
                latitude: area.centerLat,
                longitude: area.centerLon
            ),
            distance: 5000
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
        withAnimation {
            position = Self.fittedRegion(
                centerLat: (minLat + maxLat) / 2,
                centerLon: (minLon + maxLon) / 2,
                latDelta: max((maxLat - minLat) * 1.4, 0.005),
                lonDelta: max((maxLon - minLon) * 1.4, 0.005),
                bottomInset: bottomInset
            )
        }
    }

    private func difficultyColor(_ difficulty: Difficulty) -> Color {
        switch difficulty {
        case .easy: return .green
        case .moderate: return .orange
        case .hard: return .red
        }
    }
}
