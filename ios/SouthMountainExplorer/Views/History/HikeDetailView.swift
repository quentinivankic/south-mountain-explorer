import SwiftUI
import MapKit

struct HikeDetailView: View {
    let hike: SavedRecording
    let areaName: String

    @Environment(AreaDataService.self) private var areas
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial
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

                elevationSection
                    .padding(.horizontal)

                if !hike.completedTrailIds.isEmpty {
                    completedTrailsSection
                        .padding(.horizontal)
                }

                if !hike.revisitedTrailIds.isEmpty {
                    revisitedTrailsSection
                        .padding(.horizontal)
                }
            }
            .padding(.vertical)
        }
        .navigationTitle(areaName)
        .navigationBarTitleDisplayMode(.inline)
        .task { area = await areas.area(id: hike.areaId) }
        .task { await prepareShareImage() }
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

    /// Render the MKMapSnapshot first (off main, since it's async), then
    /// build the SwiftUI share card on top of it via ImageRenderer.
    private func prepareShareImage() async {
        let mapImage = await HikeMapSnapshot.render(path: hike.path)
        let card = ShareableHikeCard(hike: hike, areaName: areaName, mapImage: mapImage)
        let renderer = ImageRenderer(content: card)
        renderer.scale = 1
        if let ui = renderer.uiImage {
            shareImage = Image(uiImage: ui)
        }
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
            stat(
                value: UnitFormatter.distanceValue(miles: hike.distanceMi, units: units),
                unit: UnitFormatter.distanceSuffix(units: units),
                label: "Distance"
            )
            Divider().frame(height: 36)
            stat(value: durationString, unit: "", label: "Duration")
            Divider().frame(height: 36)
            // Overall pace from the persisted aggregates — distance
            // and duration are on every SavedRecording so this is
            // retroactive on existing history with no migration.
            stat(
                value: UnitFormatter.paceValue(metersPerSecond: overallPaceMps, units: units),
                unit: UnitFormatter.paceSuffix(units: units),
                label: "Pace"
            )
            Divider().frame(height: 36)
            stat(value: dateString, unit: "", label: "Date")
        }
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity)
        .compatibleGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    /// Overall pace in m/s for this hike. Zero-guards on duration
    /// so a degenerate (corrupted / zero-second) record renders
    /// "—" rather than crashing on divide-by-zero.
    private var overallPaceMps: Double {
        guard hike.durationSeconds > 0 else { return 0 }
        let meters = hike.distanceMi * 1609.344
        return meters / Double(hike.durationSeconds)
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

    /// Elevation section: profile chart + 2×2 stat grid (ascent /
    /// descent / max / min). Skipped entirely with a tiny "not
    /// recorded" caption when the hike pre-dates altitude capture
    /// (PR A of build 17) or when every GPS fix had bad vertical
    /// accuracy.
    @ViewBuilder
    private var elevationSection: some View {
        if let stats = elevationStats(path: hike.path) {
            VStack(alignment: .leading, spacing: 10) {
                Label("Elevation", systemImage: "mountain.2.fill")
                    .font(.headline)
                    .foregroundStyle(.green)

                VStack(spacing: 14) {
                    ElevationProfileView(
                        stats: stats,
                        totalDistanceMeters: hike.distanceMi * 1609.344
                    )
                    .frame(height: 160)

                    HStack(spacing: 0) {
                        stat(value: UnitFormatter.elevationValue(meters: stats.totalAscentMeters, units: units),
                             unit: UnitFormatter.elevationSuffix(units: units), label: "Ascent")
                        Divider().frame(height: 36)
                        stat(value: UnitFormatter.elevationValue(meters: stats.totalDescentMeters, units: units),
                             unit: UnitFormatter.elevationSuffix(units: units), label: "Descent")
                        Divider().frame(height: 36)
                        stat(value: UnitFormatter.elevationValue(meters: stats.maxAltitudeMeters, units: units),
                             unit: UnitFormatter.elevationSuffix(units: units), label: "High")
                        Divider().frame(height: 36)
                        stat(value: UnitFormatter.elevationValue(meters: stats.minAltitudeMeters, units: units),
                             unit: UnitFormatter.elevationSuffix(units: units), label: "Low")
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .compatibleGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
        } else if !hike.path.isEmpty {
            // Pre-feature hike with a path but no altitude. Tiny
            // explanatory row so the user doesn't wonder why this
            // section is missing.
            HStack {
                Image(systemName: "mountain.2")
                    .foregroundStyle(.secondary)
                Text("Elevation not recorded for this hike")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .padding(.horizontal, 4)
        }
    }

    private var completedTrailsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("\(hike.completedTrailIds.count) newly completed",
                  systemImage: "checkmark.seal.fill")
                .font(.headline)
                .foregroundStyle(.green)

            VStack(alignment: .leading, spacing: 8) {
                ForEach(hike.completedTrailIds, id: \.self) { trailId in
                    HStack {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                        Text(trailName(for: trailId))
                            .font(.body)
                        Spacer()
                    }
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .compatibleGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
    }

    private var revisitedTrailsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("\(hike.revisitedTrailIds.count) previously completed",
                  systemImage: "arrow.clockwise.circle.fill")
                .font(.headline)
                .foregroundStyle(.cyan)

            VStack(alignment: .leading, spacing: 8) {
                ForEach(hike.revisitedTrailIds, id: \.self) { trailId in
                    HStack {
                        Image(systemName: "arrow.clockwise")
                            .foregroundStyle(.cyan)
                        Text(trailName(for: trailId))
                            .font(.body)
                        Spacer()
                    }
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .compatibleGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
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
