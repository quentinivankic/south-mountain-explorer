import SwiftUI
import MapKit

struct AllAreasMapView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(\.dismiss) private var dismiss

    @State private var position: MapCameraPosition = .automatic
    @State private var selectedArea: AreaSummary? = nil

    var body: some View {
        NavigationStack {
            Map(position: $position) {
                ForEach(areas.summaries) { area in
                    Annotation(
                        area.name,
                        coordinate: CLLocationCoordinate2D(latitude: area.centerLat, longitude: area.centerLon)
                    ) {
                        Button {
                            selectedArea = area
                        } label: {
                            Image(systemName: "mountain.2.fill")
                                .font(.body.weight(.semibold))
                                .foregroundStyle(.white)
                                .padding(8)
                                .background(tint(for: area), in: Circle())
                                .shadow(radius: 2, y: 1)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .mapStyle(.standard(elevation: .realistic, pointsOfInterest: .excludingAll))
            .navigationTitle("All Areas")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear { fitToAllAreas() }
        }
        .sheet(item: $selectedArea) { area in
            AreaView(areaId: area.id, areaName: area.name)
        }
    }

    private func tint(for area: AreaSummary) -> Color {
        // Quick visual differentiation; only two states today (AZ + Denmark).
        switch area.subtitle {
        case "Denmark": return .blue
        default:        return .orange
        }
    }

    private func fitToAllAreas() {
        let lats = areas.summaries.map { $0.centerLat }
        let lons = areas.summaries.map { $0.centerLon }
        guard let minLat = lats.min(), let maxLat = lats.max(),
              let minLon = lons.min(), let maxLon = lons.max() else { return }
        let region = MKCoordinateRegion(
            center: CLLocationCoordinate2D(
                latitude: (minLat + maxLat) / 2,
                longitude: (minLon + maxLon) / 2
            ),
            span: MKCoordinateSpan(
                latitudeDelta: max((maxLat - minLat) * 1.4, 0.05),
                longitudeDelta: max((maxLon - minLon) * 1.4, 0.05)
            )
        )
        position = .region(region)
    }
}
