import SwiftUI
import CoreLocation

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

struct BrowseView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(FavoritesService.self) private var favorites
    @Environment(LocationService.self) private var location

    @State private var query = ""
    @State private var selectedArea: AreaSummary? = nil
    @State private var sort: BrowseSort = .alphabetic

    /// Sort + filter pipeline. Search happens first (cheap string match),
    /// then sort. Nearest falls back to alphabetic when location is
    /// unavailable rather than producing an arbitrary order.
    private var results: [AreaSummary] {
        let pool = query.isEmpty ? areas.summaries : areas.search(query)
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
                    } label: {
                        Image(systemName: "arrow.up.arrow.down.circle")
                    }
                    .accessibilityLabel("Sort areas")
                }
            }
        }
        .sheet(item: $selectedArea) { area in
            NavigationStack {
                AreaView(areaId: area.id, areaName: area.name)
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
                        Text("\(count) trails · \(String(format: "%.1f", mi)) mi")
                    }
                    if let d = distanceMi {
                        Text("·")
                        Text("\(formatDistance(d)) away")
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

    private func formatDistance(_ mi: Double) -> String {
        if mi < 10 { return String(format: "%.1f mi", mi) }
        return "\(Int(mi.rounded())) mi"
    }

    private var cardColors: [Color] {
        let palette: [[Color]] = [
            [.green, .teal], [.blue, .indigo], [.orange, .red],
            [.purple, .blue], [.teal, .green], [.pink, .purple],
        ]
        return palette[abs(area.id.hashValue) % palette.count]
    }
}
