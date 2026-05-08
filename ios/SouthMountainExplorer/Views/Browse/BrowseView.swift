import SwiftUI

struct BrowseView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(FavoritesService.self) private var favorites

    @State private var query = ""
    @State private var selectedArea: AreaSummary? = nil

    private var results: [AreaSummary] {
        query.isEmpty ? areas.summaries : areas.search(query)
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
        }
        .sheet(item: $selectedArea) { area in
            NavigationStack {
                AreaView(areaId: area.id, areaName: area.name)
            }
        }
    }
}

struct BrowseRow: View {
    let area: AreaSummary
    @Environment(FavoritesService.self) private var favorites
    @Environment(ProgressService.self) private var progress

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
                Text(area.subtitle)
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
