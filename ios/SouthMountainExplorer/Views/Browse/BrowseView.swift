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
    @State private var sort: BrowseSort = .alphabetic
    @State private var driveTime: BrowseDriveTime = .any
    @FocusState private var searchFocused: Bool

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
                    List(results) { area in
                        Button {
                            selectedArea = area
                        } label: {
                            BrowseRow(area: area)
                        }
                        .listRowBackground(Color.clear)
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
                AreaView(areaId: area.id, areaName: area.name)
            }
        }
        .onChange(of: selectedArea?.id) { _, newId in
            if let id = newId {
                ActivityLogService.shared.log(
                    category: "area",
                    action: "openFromBrowse",
                    context: ["areaId": id]
                )
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
            // Color swatch
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(LinearGradient(colors: cardColors, startPoint: .topLeading, endPoint: .bottomTrailing))
                .frame(width: 44, height: 44)
                .overlay {
                    Image(systemName: "mountain.2.fill")
                        .foregroundStyle(.white.opacity(0.9))
                        .font(.headline)
                }

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

    private var cardColors: [Color] {
        let palette: [[Color]] = [
            [.green, .teal], [.blue, .indigo], [.orange, .red],
            [.purple, .blue], [.teal, .green], [.pink, .purple],
        ]
        return palette[abs(area.id.hashValue) % palette.count]
    }
}
