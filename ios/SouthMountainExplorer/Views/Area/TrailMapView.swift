import SwiftUI
import MapKit

struct TrailMapView: View {
    let area: Area
    let activeRecording: ActiveRecording?
    @Binding var selectedTrailId: String?

    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location

    @State private var position: MapCameraPosition = .automatic

    var body: some View {
        Map(position: $position) {
            ForEach(area.trails) { trail in
                let isSelected = trail.id == selectedTrailId
                let isComplete = progress.isComplete(areaId: area.id, trailId: trail.id)
                // Completed trails use cyan to avoid colliding with "easy" (.green).
                let baseColor: Color = isComplete ? .cyan : difficultyColor(trail.difficulty)
                let dimmed = (selectedTrailId != nil && !isSelected)
                let strokeColor = baseColor.opacity(dimmed ? 0.25 : 1.0)
                let lineWidth: CGFloat = isSelected ? 6 : 3

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
    }

    private func centerOnArea() {
        if let bbox = area.bbox, bbox.count == 4 {
            let region = MKCoordinateRegion(
                center: CLLocationCoordinate2D(
                    latitude: (bbox[1] + bbox[3]) / 2,
                    longitude: (bbox[0] + bbox[2]) / 2
                ),
                span: MKCoordinateSpan(
                    latitudeDelta: abs(bbox[3] - bbox[1]) * 1.2,
                    longitudeDelta: abs(bbox[2] - bbox[0]) * 1.2
                )
            )
            withAnimation { position = .region(region) }
        } else {
            withAnimation {
                position = .camera(MapCamera(
                    centerCoordinate: CLLocationCoordinate2D(
                        latitude: area.centerLat,
                        longitude: area.centerLon
                    ),
                    distance: 5000
                ))
            }
        }
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
