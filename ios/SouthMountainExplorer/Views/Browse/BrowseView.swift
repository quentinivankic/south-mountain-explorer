import SwiftUI
import CoreLocation

extension Notification.Name {
    /// Posted by ContentView every time the user taps the Browse tab's
    /// tab-bar icon — including re-taps while the tab is already
    /// selected. BrowseView responds by focusing the search field so
    /// the keyboard opens immediately.
    static let browseSearchTabTapped = Notification.Name("summit.browseSearchTabTapped")
}

private enum BrowseSort: String, CaseIterable, Identifiable {
    case alphabetic, nearest, mostTrails, longest

    var id: String { rawValue }
    var label: String {
        switch self {
        case .alphabetic: return "A → Z"
        case .nearest:    return "Nearest"
        case .mostTrails: return "Most trails"
        case .longest:    return "Total miles"
        }
    }
    var systemImage: String {
        switch self {
        case .alphabetic: return "textformat.abc"
        case .nearest:    return "location"
        case .mostTrails: return "map"
        case .longest:    return "ruler"
        }
    }
}

/// Drive-time bands estimated from straight-line distance. Real routing would
/// be a per-area MKDirections call (too expensive for a list filter), so we
/// approximate at ~1.3 min/mi — a mix of highway + local roads.
private enum BrowseDriveTime: String, CaseIterable, Identifiable {
    case any, t30, t60, t120

    var id: String { rawValue }
    var label: String {
        switch self {
        case .any:  return "Any distance"
        case .t30:  return "< 30 min"
        case .t60:  return "< 1 hr"
        case .t120: return "< 2 hr"
        }
    }
    /// Max straight-line miles that fit under this band, given our 1.3 min/mi
    /// estimate. nil means no cap.
    var maxMiles: Double? {
        switch self {
        case .any:  return nil
        case .t30:  return 30.0 / 1.3
        case .t60:  return 60.0 / 1.3
        case .t120: return 120.0 / 1.3
        }
    }
}

struct BrowseView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(FavoritesService.self) private var favorites
    @Environment(LocationService.self) private var location

    @State private var query = ""
    @State private var selectedArea: AreaSummary? = nil
    /// Trail id to pre-select in the area sheet — set when the sheet is
    /// opened from a trail search result, nil when opened from an area
    /// row. Cleared on sheet dismiss.
    @State private var pendingTrailId: String? = nil
    @State private var sort: BrowseSort = .alphabetic
    @State private var driveTime: BrowseDriveTime = .any
    @FocusState private var searchFocused: Bool

    /// Trail-name matches for the current query, across every area with
    /// a full payload available locally (favorites, prefetched nearby,
    /// previously opened — trail names don't exist in the lightweight
    /// index). Capped so a one-letter query doesn't dump thousands of
    /// rows above the area results.
    private var trailResults: [AreaDataService.TrailSearchHit] {
        guard !query.isEmpty else { return [] }
        let q = query.lowercased()
        return Array(areas.trailSearchHits().filter { $0.searchKey.contains(q) }.prefix(25))
    }

    /// Sort + filter pipeline. Search happens first (cheap string match), then
    /// drive-time cap (silently skipped when we don't have a user location —
    /// hiding everything would be more surprising than showing the unfiltered
    /// list), then sort. Nearest falls back to alphabetic when location is
    /// unavailable rather than producing an arbitrary order.
    private var results: [AreaSummary] {
        var pool = query.isEmpty ? areas.summaries : areas.search(query)
        if let cap = driveTime.maxMiles, let loc = location.userLocation {
            pool = pool.filter { haversine($0, loc) <= cap }
        }
        switch sort {
        case .alphabetic:
            return pool.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        case .nearest:
            guard let loc = location.userLocation else {
                return pool.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            }
            return pool.sorted {
                haversine($0, loc) < haversine($1, loc)
            }
        case .mostTrails:
            return pool.sorted { ($0.trailCount ?? 0) > ($1.trailCount ?? 0) }
        case .longest:
            return pool.sorted { ($0.totalMi ?? 0) > ($1.totalMi ?? 0) }
        }
    }

    var body: some View {
        NavigationStack {
            Group {
                if areas.isLoadingIndex {
                    ProgressView("Loading areas...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List {
                        let trailHits = trailResults
                        if !trailHits.isEmpty {
                            Section("Trails") {
                                ForEach(trailHits) { hit in
                                    Button {
                                        pendingTrailId = hit.trailId
                                        selectedArea = areas.summaries.first { $0.id == hit.areaId }
                                    } label: {
                                        TrailHitRow(hit: hit)
                                    }
                                    .listRowBackground(Color.clear)
                                }
                            }
                        }
                        Section {
                            ForEach(results) { area in
                                Button {
                                    pendingTrailId = nil
                                    selectedArea = area
                                } label: {
                                    BrowseRow(area: area)
                                }
                                .listRowBackground(Color.clear)
                            }
                        } header: {
                            // Only label the section when a Trails
                            // section sits above it — an unqualified
                            // list doesn't need a header.
                            if !trailHits.isEmpty {
                                Text("Areas")
                            }
                        }
                    }
                    .listStyle(.plain)
                    .searchable(text: $query, prompt: "Search trails and parks")
                    .searchFocused($searchFocused)
                }
            }
            .navigationTitle("Browse")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Sort", selection: $sort) {
                            ForEach(BrowseSort.allCases) { option in
                                Label(option.label, systemImage: option.systemImage).tag(option)
                            }
                        }
                        Divider()
                        // Drive-time cap. Disabled-styled (still tappable, but
                        // no-op) when location is unavailable so the user
                        // understands why the filter wouldn't apply.
                        Picker("Drive time", selection: $driveTime) {
                            ForEach(BrowseDriveTime.allCases) { option in
                                Label(option.label, systemImage: "car").tag(option)
                            }
                        }
                    } label: {
                        Image(systemName: driveTime == .any
                              ? "arrow.up.arrow.down.circle"
                              : "line.3.horizontal.decrease.circle.fill")
                    }
                    .accessibilityLabel("Sort and filter areas")
                }
            }
        }
        .sheet(item: $selectedArea) { area in
            NavigationStack {
                AreaView(
                    areaId: area.id,
                    areaName: area.name,
                    initialSelectedTrailId: pendingTrailId
                )
            }
        }
        .onChange(of: selectedArea?.id) { _, newId in
            if let id = newId {
                ActivityLogService.shared.log(
                    category: "area",
                    action: "openFromBrowse",
                    context: ["areaId": id, "trailId": pendingTrailId ?? "nil"]
                )
            } else {
                pendingTrailId = nil
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .browseSearchTabTapped)) { _ in
            Task { @MainActor in
                // Let the tab-switch transition settle first — grabbing
                // focus mid-transition gets dropped by the system, which
                // would make the first tap into the tab do nothing.
                try? await Task.sleep(for: .milliseconds(350))
                searchFocused = true
            }
        }
    }

    private func haversine(_ a: AreaSummary, _ loc: CLLocationCoordinate2D) -> Double {
        let R = 3958.8
        let dLat = (a.centerLat - loc.latitude) * .pi / 180
        let dLon = (a.centerLon - loc.longitude) * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(loc.latitude * .pi / 180) * cos(a.centerLat * .pi / 180)
            * sin(dLon / 2) * sin(dLon / 2)
        return R * 2 * atan2(sqrt(h), sqrt(1 - h))
    }
}

struct BrowseRow: View {
    let area: AreaSummary
    @Environment(FavoritesService.self) private var favorites
    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    private var distanceMi: Double? {
        guard let loc = location.userLocation else { return nil }
        let R = 3958.8
        let dLat = (area.centerLat - loc.latitude) * .pi / 180
        let dLon = (area.centerLon - loc.longitude) * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(loc.latitude * .pi / 180) * cos(area.centerLat * .pi / 180)
            * sin(dLon / 2) * sin(dLon / 2)
        return R * 2 * atan2(sqrt(h), sqrt(1 - h))
    }

    var body: some View {
        HStack(spacing: 14) {
            SilhouetteThumb(areaId: area.id)

            VStack(alignment: .leading, spacing: 2) {
                Text(area.name)
                    .font(.body)
                    .foregroundStyle(.primary)
                HStack(spacing: 4) {
                    Text(area.subtitle)
                    if let count = area.trailCount, let mi = area.totalMi {
                        Text("·")
                        Text("\(count) trails · \(UnitFormatter.distance(miles: mi, units: units)) total")
                    }
                    if let d = distanceMi {
                        Text("·")
                        Text("\(UnitFormatter.distance(miles: d, units: units)) away")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                // Without a line limit the HStack resolves a too-wide
                // caption by WRAPPING one of its Texts mid-row ("88.6
                // / mi total" on two lines) instead of truncating.
                .lineLimit(1)
            }

            Spacer()

            if favorites.isFavorite(area.id) {
                Image(systemName: "heart.fill")
                    .foregroundStyle(.red)
                    .font(.caption)
            }

            Image(systemName: "chevron.right")
                .foregroundStyle(.tertiary)
                .font(.caption)
        }
        .contentShape(Rectangle())
    }
}

/// A trail search result: the trail's name over its area, length, and
/// difficulty, fronted by the area's silhouette thumbnail.
private struct TrailHitRow: View {
    let hit: AreaDataService.TrailSearchHit
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    var body: some View {
        HStack(spacing: 14) {
            SilhouetteThumb(areaId: hit.areaId)

            VStack(alignment: .leading, spacing: 2) {
                Text(hit.trailName)
                    .font(.body)
                    .foregroundStyle(.primary)
                HStack(spacing: 4) {
                    Text(hit.areaName)
                    Text("·")
                    Text(UnitFormatter.distance(miles: hit.distanceMi, units: units))
                    Text("·")
                    Text(hit.difficulty.rawValue)
                        .foregroundStyle(difficultyColor)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }

            Spacer()

            Image(systemName: "chevron.right")
                .foregroundStyle(.tertiary)
                .font(.caption)
        }
        .contentShape(Rectangle())
    }

    private var difficultyColor: Color {
        switch hit.difficulty {
        case .easy: return .green
        case .moderate: return .orange
        case .hard: return .red
        }
    }
}

/// 44×44 thumbnail of an area's trail-line silhouette — the same neon
/// linework as the Explore cards, shrunk to a list swatch. Falls back
/// to a neutral placeholder while the silhouette loads; the `.task`
/// kicks the R2 fetch (deduped by the service) for rows as they appear.
private struct SilhouetteThumb: View {
    let areaId: String
    @Environment(AreaSilhouetteService.self) private var silhouettes

    var body: some View {
        // Bare linework — no backing box or border. The lines ARE the
        // icon; a container plate around them just read as clutter.
        Group {
            if let silhouette = silhouettes.cachedSilhouette(for: areaId) {
                ThumbCanvas(silhouette: silhouette)
            } else {
                Image(systemName: "mountain.2.fill")
                    .foregroundStyle(.tertiary)
                    .font(.subheadline)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: 44, height: 44)
        .task(id: areaId) {
            await silhouettes.silhouette(for: areaId)
        }
    }
}

/// Minimal silhouette painter for the 44pt thumbnails — the same
/// equirectangular projection as the card artwork, single stroke pass,
/// no glow (it wouldn't read at this size).
private struct ThumbCanvas: View {
    let silhouette: AreaSilhouette

    var body: some View {
        Canvas { context, size in
            guard let bbox = silhouette.bbox else { return }
            let pad: CGFloat = 5
            let drawW = size.width - 2 * pad
            let drawH = size.height - 2 * pad
            guard drawW > 0, drawH > 0 else { return }
            let centerLat = (bbox.s + bbox.n) / 2
            let lonScale = cos(centerLat * .pi / 180)
            let xRange = max((bbox.e - bbox.w) * lonScale, .leastNonzeroMagnitude)
            let yRange = max(bbox.n - bbox.s, .leastNonzeroMagnitude)
            let scale = min(drawW / xRange, drawH / yRange)
            let canvasW = xRange * scale
            let canvasH = yRange * scale
            let xOffset = pad + (drawW - canvasW) / 2
            let yOffset = pad + (drawH - canvasH) / 2

            for line in silhouette.l {
                guard line.p.count >= 2 else { continue }
                var path = Path()
                for (i, pt) in line.p.enumerated() {
                    guard pt.count >= 2 else { continue }
                    let x = xOffset + (pt[1] - bbox.w) * lonScale * scale
                    let y = yOffset + canvasH - (pt[0] - bbox.s) * scale
                    let p = CGPoint(x: x, y: y)
                    if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
                }
                let color: Color
                switch line.d {
                case "e": color = .green
                case "m": color = .orange
                case "h": color = .red
                default:  color = .gray
                }
                context.stroke(
                    path,
                    with: .color(color),
                    style: StrokeStyle(lineWidth: 1.1, lineCap: .round, lineJoin: .round)
                )
            }
        }
    }
}
