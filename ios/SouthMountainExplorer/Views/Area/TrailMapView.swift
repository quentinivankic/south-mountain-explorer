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
        // before .onAppear fires.
        self._position = State(initialValue: Self.regionCovering(area: area))
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
        .onAppear { centerOnArea() }
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
        // MapKit's built-in .userLocation camera does.
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
    /// through. Off-trail portions (commute to/from trailhead) drop
    /// out via `onTrailSegments`.
    @MapContentBuilder
    private var haloPolylines: some MapContent {
        ForEach(Array(pastPaths.enumerated()), id: \.offset) { _, path in
            let segments = onTrailSegments(path)
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
        // Drop trails filtered out by the trail-list filter, but
        // always keep the currently-recording trail and the currently-
        // selected trail visible so the user can see what's happening.
        let drawableTrails: [Trail] = visibleTrailIds.map { allowed in
            area.trails.filter { trail in
                allowed.contains(trail.id)
                    || trail.id == recordingTrailId
                    || trail.id == selectedTrailId
            }
        } ?? area.trails
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
        ForEach(Array(trail.segments.enumerated()), id: \.offset) { _, segment in
            let coords: [CLLocationCoordinate2D] = segment.compactMap { node in
                guard node.count >= 2 else { return nil }
                return CLLocationCoordinate2D(latitude: node[0], longitude: node[1])
            }
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
        switch mode {
        case .free:
            // Break out of any active tracking by snapping to a static
            // region centered on the user (1500 m, north up). Heading
            // updates aren't needed here, so cut them off to save the
            // magnetometer.
            location.stopHeadingUpdates()
            let region = location.userLocation.map {
                MKCoordinateRegion(center: $0, latitudinalMeters: 1500, longitudinalMeters: 1500)
            }
            withAnimation {
                position = region.map { .region($0) } ?? Self.regionCovering(area: area)
            }
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
            // MapCamera distance ~2500 m approximates the visual zoom
            // of MKCoordinateRegion(latitudinalMeters: 1500) at our
            // typical screen aspect. Tune if the zoom feels off vs
            // the plain follow mode.
            position = .camera(MapCamera(
                centerCoordinate: shifted,
                distance: 2500,
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
        // Shift the region center south by half the bottom inset (in
        // meters at the current zoom) so the user dot lands in the
        // geometric middle of the *visible* map. The bottom is
        // occluded by the controlBar + recording panel + trail list
        // sheet; without this the dot reads as below-center.
        let latMeters = 1500.0
        let center: CLLocationCoordinate2D
        if bottomInset > 0 {
            // MKCoordinateRegion with a square latitudinalMeters /
            // longitudinalMeters gets fit to the SHORTER screen axis
            // — in portrait that's width, not height. So m/pt is
            // latMeters / min(width, height), not / height. (Using
            // height in portrait gave a ~2× under-shift that read
            // visually as "still centered on the phone screen".)
            let b = UIScreen.main.bounds
            let shortDim = min(b.width, b.height)
            let metersPerPoint = latMeters / max(shortDim, 1)
            let shiftMeters = (bottomInset / 2) * metersPerPoint
            let shiftDegrees = shiftMeters / 111_000.0
            center = CLLocationCoordinate2D(
                latitude: coord.latitude - shiftDegrees,
                longitude: coord.longitude
            )
        } else {
            center = coord
        }
        let region = MKCoordinateRegion(
            center: center,
            latitudinalMeters: latMeters,
            longitudinalMeters: latMeters
        )
        withAnimation { position = .region(region) }
    }

    private func centerOnArea() {
        withAnimation { position = Self.regionCovering(area: area) }
    }

    /// Split a recorded GPS path into runs of consecutive points that lie
    /// within `bufferM` of any trail node. Off-trail runs (e.g. commuting
    /// from home to the trailhead) drop out so the cyan halo only paints
    /// actual trail coverage.
    private func onTrailSegments(_ path: [GpsPoint]) -> [[CLLocationCoordinate2D]] {
        let bufferM = 30.0
        var grid = SpatialGrid()
        for trail in area.trails {
            for seg in trail.segments {
                for node in seg { grid.insert(node) }
            }
        }
        var segments: [[CLLocationCoordinate2D]] = []
        var current: [CLLocationCoordinate2D] = []
        for p in path {
            guard p.count >= 2 else { continue }
            if grid.hasNeighbor(lat: p[0], lon: p[1], withinMeters: bufferM) {
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
    private static func regionCovering(area: Area) -> MapCameraPosition {
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
            let region = MKCoordinateRegion(
                center: CLLocationCoordinate2D(
                    latitude: (minLat + maxLat) / 2,
                    longitude: (minLon + maxLon) / 2
                ),
                span: MKCoordinateSpan(
                    // Slight padding so trails don't kiss the edges, and a
                    // floor so single-point edge cases don't render
                    // street-level zoom.
                    latitudeDelta: max((maxLat - minLat) * 1.3, 0.01),
                    longitudeDelta: max((maxLon - minLon) * 1.3, 0.01)
                )
            )
            return .region(region)
        }

        if let bbox = area.bbox, bbox.count == 4 {
            let region = MKCoordinateRegion(
                center: CLLocationCoordinate2D(
                    latitude: (bbox[1] + bbox[3]) / 2,
                    longitude: (bbox[0] + bbox[2]) / 2
                ),
                span: MKCoordinateSpan(
                    latitudeDelta: max(abs(bbox[3] - bbox[1]) * 1.2, 0.01),
                    longitudeDelta: max(abs(bbox[2] - bbox[0]) * 1.2, 0.01)
                )
            )
            return .region(region)
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
        let region = MKCoordinateRegion(
            center: CLLocationCoordinate2D(
                latitude: (minLat + maxLat) / 2,
                longitude: (minLon + maxLon) / 2
            ),
            span: MKCoordinateSpan(
                latitudeDelta: max((maxLat - minLat) * 1.4, 0.005),
                longitudeDelta: max((maxLon - minLon) * 1.4, 0.005)
            )
        )
        withAnimation { position = .region(region) }
    }

    private func difficultyColor(_ difficulty: Difficulty) -> Color {
        switch difficulty {
        case .easy: return .green
        case .moderate: return .orange
        case .hard: return .red
        }
    }
}
