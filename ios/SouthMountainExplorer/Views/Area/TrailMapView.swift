import SwiftUI
import MapKit

struct TrailMapView: View {
    let area: Area
    let activeRecording: ActiveRecording?

    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location

    @State private var position: MapCameraPosition = .automatic

    var body: some View {
        Map(position: $position) {
            // Trail polylines
            ForEach(area.trails) { trail in
                let isComplete = progress.isComplete(areaId: area.id, trailId: trail.id)
                ForEach(Array(trail.segments.enumerated()), id: \.offset) { _, segment in
                    let coords = segment.compactMap { node -> CLLocationCoordinate2D? in
                        guard node.count >= 2 else { return nil }
                        return CLLocationCoordinate2D(latitude: node[0], longitude: node[1])
                    }
                    MapPolyline(coordinates: coords)
                        .stroke(
                            isComplete ? Color.green : difficultyColor(trail.difficulty),
                            style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round)
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

            // User location
            UserAnnotation()
        }
        .mapStyle(.standard(elevation: .realistic, pointsOfInterest: .excludingAll))
        .mapControls {
            MapUserLocationButton()
            MapCompass()
            MapScaleView()
        }
        .onAppear { centerOnArea() }
    }

    private func centerOnArea() {
        if let bbox = area.bbox, bbox.count == 4 {
            // bbox: [minLon, minLat, maxLon, maxLat]
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
            position = .region(region)
        } else {
            position = .camera(MapCamera(
                centerCoordinate: CLLocationCoordinate2D(
                    latitude: area.centerLat,
                    longitude: area.centerLon
                ),
                distance: 5000
            ))
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
