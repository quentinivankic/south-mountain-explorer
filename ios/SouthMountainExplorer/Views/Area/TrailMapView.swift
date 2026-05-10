import SwiftUI
import MapKit

struct TrailMapView: View {
    let area: Area
    let activeRecording: ActiveRecording?
    let pastPaths: [[GpsPoint]]
    let recenterTick: Int
    @Binding var selectedTrailId: String?

    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location

    @State private var position: MapCameraPosition

    init(
        area: Area,
        activeRecording: ActiveRecording?,
        pastPaths: [[GpsPoint]],
        recenterTick: Int,
        selectedTrailId: Binding<String?>
    ) {
        self.area = area
        self.activeRecording = activeRecording
        self.pastPaths = pastPaths
        self.recenterTick = recenterTick
        self._selectedTrailId = selectedTrailId
        // Compute the initial camera position synchronously so MapKit's
        // own .automatic frame can't briefly render a fragment of the area
        // before .onAppear fires.
        self._position = State(initialValue: Self.regionCovering(area: area))
    }

    var body: some View {
        Map(position: $position) {
            // Cumulative-coverage halo: every past hike's GPS path drawn in
            // cyan beneath the trail polylines so walked sections glow
            // through. Wider stroke + reduced opacity makes it read as a
            // background highlight, not a competing line.
            ForEach(Array(pastPaths.enumerated()), id: \.offset) { _, path in
                let coords = path.compactMap { node -> CLLocationCoordinate2D? in
                    guard node.count >= 2 else { return nil }
                    return CLLocationCoordinate2D(latitude: node[0], longitude: node[1])
                }
                if coords.count > 1 {
                    MapPolyline(coordinates: coords)
                        .stroke(
                            .cyan.opacity(0.55),
                            style: StrokeStyle(lineWidth: 7, lineCap: .round, lineJoin: .round)
                        )
                }
            }

            // Trails. The recording-mode highlight is folded INTO this loop
            // (rather than as a separate overlay layer) so it uses the same
            // code path as tap-to-highlight, which is known to render. The
            // recording trail gets a distinct purple stroke at lineWidth 10
            // — purple sits outside the existing palette (green easy /
            // orange moderate / red hard / cyan completed+halo / blue
            // live-GPS) so it can't be confused for any of those.
            let recordingTrailId = activeRecording?.trailId
            let highlightedTrailId = recordingTrailId ?? selectedTrailId
            ForEach(area.trails) { trail in
                let isRecordingThis = trail.id == recordingTrailId
                let isSelected = trail.id == selectedTrailId
                let isHighlighted = isRecordingThis || isSelected
                let isComplete = progress.isComplete(areaId: area.id, trailId: trail.id)

                let baseColor: Color = {
                    if isRecordingThis { return .purple }
                    if isComplete { return .cyan }
                    return difficultyColor(trail.difficulty)
                }()
                let dimmed = (highlightedTrailId != nil && !isHighlighted)
                let strokeColor = baseColor.opacity(dimmed ? 0.25 : 1.0)
                let lineWidth: CGFloat = isRecordingThis ? 10 : (isSelected ? 6 : 3)

                ForEach(Array(trail.segments.enumerated()), id: \.offset) { _, segment in
                    let coords = segment.compactMap { node -> CLLocationCoordinate2D? in
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

            // Recorded GPS path
            if let rec = activeRecording, rec.path.count > 1 {
                let pathCoords = rec.path.map {
                    CLLocationCoordinate2D(latitude: $0[0], longitude: $0[1])
                }
                MapPolyline(coordinates: pathCoords)
                    .stroke(.blue, style: StrokeStyle(lineWidth: 4, lineCap: .round, lineJoin: .round))
            }

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
        .onChange(of: recenterTick) { _, _ in centerOnUser() }
    }

    private func centerOnUser() {
        guard let coord = location.userLocation else {
            centerOnArea()
            return
        }
        let region = MKCoordinateRegion(
            center: coord,
            latitudinalMeters: 1500,
            longitudinalMeters: 1500
        )
        withAnimation { position = .region(region) }
    }

    private func centerOnArea() {
        withAnimation { position = Self.regionCovering(area: area) }
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
