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
    /// Map(selection:) tag of the tapped marker — "area:<id>" or
    /// "cluster:<key>". Consumed (and reset) by the onChange below.
    @State private var selectedMarkerTag: String? = nil
    /// Cached bucketing of the area set into screen-space clusters.
    /// Cached (not computed) because at full-US scale we have 3000+
    /// areas, and recomputing the O(N) bucket pass on every
    /// camera-change frame caused multi-second lag during pan/zoom.
    /// Updated only when the camera STOPS moving — see
    /// `onMapCameraChange(frequency: .onEnd)`.
    @State private var clusters: [AreaCluster] = []

    /// Compute screen-space clusters from `areas.summaries` at the
    /// current camera span. Aim for ~12 buckets across the visible
    /// span so a low-zoom view collapses neighbouring areas into one
    /// pin and a high-zoom view renders every area individually.
    /// Without clustering, 100+ areas at the AZ-state zoom all overlap
    /// into a brown smear.
    private func rebuildClusters() {
        let span = currentRegion?.span ?? MKCoordinateSpan(
            latitudeDelta: 5, longitudeDelta: 5
        )
        let bucketLat = max(span.latitudeDelta / 12, 0.005)
        let bucketLon = max(span.longitudeDelta / 12, 0.005)

        var byKey: [String: [AreaSummary]] = [:]
        for area in areas.summaries {
            let row = Int((area.centerLat / bucketLat).rounded())
            let col = Int((area.centerLon / bucketLon).rounded())
            byKey["\(row):\(col)", default: []].append(area)
        }
        clusters = byKey.map { key, group in
            let lat = group.map(\.centerLat).reduce(0, +) / Double(group.count)
            let lon = group.map(\.centerLon).reduce(0, +) / Double(group.count)
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
            // Native `Marker`s instead of custom SwiftUI `Annotation`
            // views. The old Buttons (each with padding, background
            // shape, and a `.shadow` — an offscreen render pass per
            // pin) made pan/zoom unusably laggy with a hundred-plus
            // annotations on screen. Markers are rendered by MapKit
            // itself, so the map stays at native scroll performance
            // regardless of pin count. Taps come back through
            // `Map(selection:)` + per-marker `.tag`.
            Map(position: $position, selection: $selectedMarkerTag) {
                ForEach(clusters) { cluster in
                    if cluster.areas.count == 1, let area = cluster.areas.first {
                        Marker(
                            area.name,
                            systemImage: "mountain.2.fill",
                            coordinate: CLLocationCoordinate2D(
                                latitude: area.centerLat, longitude: area.centerLon
                            )
                        )
                        .tint(tint(for: area))
                        .tag("area:\(area.id)")
                    } else {
                        Marker(
                            "\(cluster.areas.count) areas",
                            monogram: Text("\(cluster.areas.count)"),
                            coordinate: cluster.coord
                        )
                        .tint(.orange)
                        .tag("cluster:\(cluster.id)")
                    }
                }
            }
            // Flat elevation — `.realistic` renders 3D terrain for the
            // whole continent on this view and was the other half of
            // the lag. Nothing on this screen needs terrain relief.
            .mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
            .onChange(of: selectedMarkerTag) { _, tag in
                guard let tag else { return }
                // Reset immediately so the same marker can be tapped
                // again after the sheet dismisses (selection only fires
                // onChange when the value actually changes).
                selectedMarkerTag = nil
                if tag.hasPrefix("area:") {
                    let id = String(tag.dropFirst("area:".count))
                    selectedArea = areas.summaries.first { $0.id == id }
                } else if tag.hasPrefix("cluster:") {
                    let key = String(tag.dropFirst("cluster:".count))
                    if let cluster = clusters.first(where: { $0.id == key }) {
                        zoom(to: cluster)
                    }
                }
            }
            // `.onEnd` (not `.continuous`) — only re-bucket when the
            // camera stops moving. With `.continuous` and 3000+ areas
            // this fired ~60×/sec during pan/zoom and each fire ran
            // the full O(N) bucket pass, which made the map crash-
            // adjacent laggy at full-US scale. Annotations stay at
            // their pre-pan positions during the gesture; that's
            // visually fine because the pins are pinned to lat/lon
            // anyway, and the cluster identity only needs to refresh
            // when the zoom level changes (which `.onEnd` captures).
            .onMapCameraChange(frequency: .onEnd) { context in
                currentRegion = context.region
                rebuildClusters()
            }
            .navigationTitle("All Areas")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear {
                fitToAllAreas()
                rebuildClusters()
            }
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
