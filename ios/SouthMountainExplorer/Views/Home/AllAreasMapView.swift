import SwiftUI
import MapKit

/// Bucket of areas that share the same on-screen tile at the current
/// zoom level. Single-area clusters render as the regular pin; multi-
/// area clusters render as a count badge that, when tapped, zooms into
/// the bucket.
private struct AreaCluster: Identifiable {
    let id: String
    let areas: [AreaSummary]
    let coord: CLLocationCoordinate2D
    let span: MKCoordinateSpan
}

struct AllAreasMapView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(\.dismiss) private var dismiss

    @State private var position: MapCameraPosition = .automatic
    @State private var selectedArea: AreaSummary? = nil
    @State private var currentRegion: MKCoordinateRegion? = nil

    /// Areas grouped into screen-space buckets. Bucket size is derived
    /// from the current camera span so a low-zoom view collapses
    /// neighbouring areas into one cluster pin, and a high-zoom view
    /// renders every area individually. Without this, 100+ areas at
    /// the AZ-state zoom level all overlap into a brown smear.
    private var clusters: [AreaCluster] {
        let span = currentRegion?.span ?? MKCoordinateSpan(latitudeDelta: 5, longitudeDelta: 5)
        // Aim for ~12 buckets across the visible span. Larger spans →
        // bigger buckets (more clustering); smaller spans → smaller
        // buckets (fewer clusters, eventually one per area).
        let bucketLat = max(span.latitudeDelta / 12, 0.005)
        let bucketLon = max(span.longitudeDelta / 12, 0.005)

        var byKey: [String: [AreaSummary]] = [:]
        for area in areas.summaries {
            let row = Int((area.centerLat / bucketLat).rounded())
            let col = Int((area.centerLon / bucketLon).rounded())
            byKey["\(row):\(col)", default: []].append(area)
        }
        return byKey.map { key, group in
            // Centroid of the bucket so the cluster pin sits at the
            // visual centre of the areas it represents.
            let lat = group.map(\.centerLat).reduce(0, +) / Double(group.count)
            let lon = group.map(\.centerLon).reduce(0, +) / Double(group.count)
            // Tighter span if the bucket has only one area so tapping it
            // doesn't over-zoom out from a wilderness's actual extent.
            let lats = group.map(\.centerLat)
            let lons = group.map(\.centerLon)
            let extentLat = (lats.max() ?? lat) - (lats.min() ?? lat)
            let extentLon = (lons.max() ?? lon) - (lons.min() ?? lon)
            return AreaCluster(
                id: key,
                areas: group,
                coord: CLLocationCoordinate2D(latitude: lat, longitude: lon),
                span: MKCoordinateSpan(
                    latitudeDelta: max(extentLat * 1.6, 0.3),
                    longitudeDelta: max(extentLon * 1.6, 0.3)
                )
            )
        }
    }

    var body: some View {
        NavigationStack {
            Map(position: $position) {
                ForEach(clusters) { cluster in
                    if cluster.areas.count == 1, let area = cluster.areas.first {
                        Annotation(
                            area.name,
                            coordinate: CLLocationCoordinate2D(
                                latitude: area.centerLat, longitude: area.centerLon
                            )
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
                    } else {
                        Annotation(
                            "\(cluster.areas.count) areas",
                            coordinate: cluster.coord
                        ) {
                            Button {
                                zoom(to: cluster)
                            } label: {
                                Text("\(cluster.areas.count)")
                                    .font(.callout.weight(.bold))
                                    .foregroundStyle(.white)
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 8)
                                    .background(.orange, in: Capsule())
                                    .overlay(Capsule().stroke(.white, lineWidth: 2))
                                    .shadow(radius: 3, y: 1)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .mapStyle(.standard(elevation: .realistic, pointsOfInterest: .excludingAll))
            .onMapCameraChange(frequency: .continuous) { context in
                currentRegion = context.region
            }
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

    private func zoom(to cluster: AreaCluster) {
        withAnimation {
            position = .region(MKCoordinateRegion(
                center: cluster.coord,
                span: cluster.span
            ))
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
