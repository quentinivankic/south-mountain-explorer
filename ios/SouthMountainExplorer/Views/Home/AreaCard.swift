import SwiftUI

struct AreaCard: View {
    let area: AreaSummary

    @Environment(FavoritesService.self) private var favorites
    @Environment(ProgressService.self) private var progress
    @Environment(AreaDataService.self) private var areas

    private var cachedArea: Area? { areas.cachedArea(id: area.id) }

    private var completedCount: Int { progress.completionCount(in: area.id) }
    private var totalTrails: Int { cachedArea?.resolvedTrailCount ?? area.trailCount ?? 0 }

    private var progressFraction: Double {
        guard totalTrails > 0 else { return 0 }
        return Double(completedCount) / Double(totalTrails)
    }

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            // Background gradient standing in for a photo
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: 220, height: 160)

            // Info overlay — Liquid Glass card
            VStack(alignment: .leading, spacing: 4) {
                Text(area.name)
                    .font(.headline)
                    .foregroundStyle(.white)
                    .lineLimit(1)

                HStack(spacing: 6) {
                    Text(area.subtitle)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.8))

                    if totalTrails > 0 {
                        Text("·")
                            .foregroundStyle(.white.opacity(0.5))
                        Text("\(completedCount)/\(totalTrails) trails")
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.8))
                    }
                }

                if totalTrails > 0 {
                    ProgressView(value: progressFraction)
                        .tint(.white)
                        .padding(.top, 2)
                }
            }
            .padding(14)
            .frame(width: 220, alignment: .leading)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .padding(6)
        }
        .overlay(alignment: .topTrailing) {
            Button {
                Task { await favorites.toggle(areaId: area.id) }
            } label: {
                Image(systemName: favorites.isFavorite(area.id) ? "heart.fill" : "heart")
                    .foregroundStyle(favorites.isFavorite(area.id) ? .red : .white)
                    .padding(10)
                    .glassEffect(in: .circle)
            }
            .padding(10)
        }
    }

    private var gradientColors: [Color] {
        let palette: [[Color]] = [
            [.green, .teal],
            [.blue, .indigo],
            [.orange, .red],
            [.purple, .blue],
            [.teal, .green],
        ]
        let index = abs(area.id.hashValue) % palette.count
        return palette[index]
    }
}
