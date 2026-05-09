import SwiftUI
import MapKit

struct HikeDetailView: View {
    let hike: SavedRecording
    let areaName: String

    @Environment(AreaDataService.self) private var areas
    @State private var area: Area? = nil
    @State private var shareImage: Image? = nil

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                hikeMap
                    .frame(height: 280)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .padding(.horizontal)

                statsCard
                    .padding(.horizontal)

                if !hike.completedTrailIds.isEmpty {
                    completedTrailsSection
                        .padding(.horizontal)
                }
            }
            .padding(.vertical)
        }
        .navigationTitle(areaName)
        .navigationBarTitleDisplayMode(.inline)
        .task { area = await areas.area(id: hike.areaId) }
        .task { shareImage = makeShareImage() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if let img = shareImage {
                    ShareLink(
                        item: img,
                        preview: SharePreview("Hike at \(areaName)", image: img)
                    ) {
                        Image(systemName: "square.and.arrow.up")
                    }
                }
            }
        }
    }

    @MainActor
    private func makeShareImage() -> Image? {
        let card = ShareableHikeCard(hike: hike, areaName: areaName)
        let renderer = ImageRenderer(content: card)
        renderer.scale = 3.0
        guard let ui = renderer.uiImage else { return nil }
        return Image(uiImage: ui)
    }

    private var hikeMap: some View {
        Map {
            // Show area trails faintly underneath the recorded path so the
            // hike has visual context.
            if let area {
                ForEach(area.trails) { trail in
                    ForEach(Array(trail.segments.enumerated()), id: \.offset) { _, segment in
                        let coords = segment.compactMap { node -> CLLocationCoordinate2D? in
                            guard node.count >= 2 else { return nil }
                            return CLLocationCoordinate2D(latitude: node[0], longitude: node[1])
                        }
                        MapPolyline(coordinates: coords)
                            .stroke(.gray.opacity(0.4),
                                    style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    }
                }
            }

            if hike.path.count > 1 {
                let pathCoords = hike.path.map {
                    CLLocationCoordinate2D(latitude: $0[0], longitude: $0[1])
                }
                MapPolyline(coordinates: pathCoords)
                    .stroke(.blue,
                            style: StrokeStyle(lineWidth: 4, lineCap: .round, lineJoin: .round))
            }
        }
        .mapStyle(.standard(elevation: .realistic, pointsOfInterest: .excludingAll))
    }

    private var statsCard: some View {
        HStack(spacing: 0) {
            stat(value: String(format: "%.2f", hike.distanceMi), unit: "mi", label: "Distance")
            Divider().frame(height: 36)
            stat(value: durationString, unit: "", label: "Duration")
            Divider().frame(height: 36)
            stat(value: dateString, unit: "", label: "Date")
        }
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity)
        .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func stat(value: String, unit: String, label: String) -> some View {
        VStack(spacing: 4) {
            HStack(alignment: .lastTextBaseline, spacing: 2) {
                Text(value)
                    .font(.title3.bold().monospacedDigit())
                if !unit.isEmpty {
                    Text(unit)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private var completedTrailsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("\(hike.completedTrailIds.count) trail\(hike.completedTrailIds.count == 1 ? "" : "s") completed",
                  systemImage: "checkmark.seal.fill")
                .font(.headline)
                .foregroundStyle(.cyan)

            VStack(alignment: .leading, spacing: 8) {
                ForEach(hike.completedTrailIds, id: \.self) { trailId in
                    HStack {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.cyan)
                        Text(trailName(for: trailId))
                            .font(.body)
                        Spacer()
                    }
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
    }

    private func trailName(for trailId: String) -> String {
        area?.trails.first { $0.id == trailId }?.name ?? trailId
    }

    private var durationString: String {
        let h = hike.durationSeconds / 3600
        let m = (hike.durationSeconds % 3600) / 60
        if h > 0 { return "\(h)h \(m)m" }
        return "\(m)m"
    }

    private var dateString: String {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f.string(from: hike.startedAt)
    }
}
