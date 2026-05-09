import SwiftUI

enum CardArtStyle {
    case tight   // Just trail polylines on a dark backdrop, no halo.
    case glow    // Trail polylines with a soft glow underneath, hinting at territory.
}

struct AreaCard: View {
    let area: AreaSummary
    var style: CardArtStyle = .tight

    @Environment(FavoritesService.self) private var favorites
    @Environment(ProgressService.self) private var progress
    @Environment(AreaDataService.self) private var areas
    @Environment(AreaSilhouetteService.self) private var silhouettes

    private var cachedArea: Area? { areas.cachedArea(id: area.id) }

    private var completedCount: Int { progress.completionCount(in: area.id) }
    private var totalTrails: Int { cachedArea?.resolvedTrailCount ?? area.trailCount ?? 0 }

    private var progressFraction: Double {
        guard totalTrails > 0 else { return 0 }
        return Double(completedCount) / Double(totalTrails)
    }

    /// Easy / moderate / hard share of this area's trail lines, taken from
    /// the bundled silhouette data so it's available without a network fetch.
    /// Returns nil if no silhouette is bundled for the area.
    private var difficultyMix: (easy: Double, moderate: Double, hard: Double)? {
        guard let silhouette = silhouettes.silhouette(for: area.id) else { return nil }
        var counts = (e: 0, m: 0, h: 0)
        for line in silhouette.l {
            switch line.d {
            case "e": counts.e += 1
            case "m": counts.m += 1
            case "h": counts.h += 1
            default:  break
            }
        }
        let total = counts.e + counts.m + counts.h
        guard total > 0 else { return nil }
        return (
            Double(counts.e) / Double(total),
            Double(counts.m) / Double(total),
            Double(counts.h) / Double(total)
        )
    }

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            artwork
                .frame(width: 220, height: 160)
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))

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

                if let mix = difficultyMix {
                    DifficultyMixBar(mix: mix)
                        .frame(height: 3)
                        .padding(.top, 2)
                }

                if totalTrails > 0 {
                    ProgressView(value: progressFraction)
                        .tint(.white)
                        .padding(.top, 2)
                }
            }
            .padding(14)
            .frame(width: 220, alignment: .leading)
            .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
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

    @ViewBuilder
    private var artwork: some View {
        if let silhouette = silhouettes.silhouette(for: area.id) {
            SilhouetteArtwork(silhouette: silhouette, style: style)
        } else {
            LinearGradient(
                colors: gradientColors,
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
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

private struct DifficultyMixBar: View {
    let mix: (easy: Double, moderate: Double, hard: Double)

    var body: some View {
        GeometryReader { geo in
            HStack(spacing: 0) {
                if mix.easy > 0 {
                    Color.green.frame(width: geo.size.width * mix.easy)
                }
                if mix.moderate > 0 {
                    Color.orange.frame(width: geo.size.width * mix.moderate)
                }
                if mix.hard > 0 {
                    Color.red.frame(width: geo.size.width * mix.hard)
                }
            }
        }
        .clipShape(Capsule())
    }
}

private struct SilhouetteArtwork: View {
    let silhouette: AreaSilhouette
    let style: CardArtStyle

    var body: some View {
        ZStack {
            Color.black.opacity(0.95)

            if style == .glow {
                SilhouetteCanvas(silhouette: silhouette, lineWidth: 5, opacity: 0.45)
                    .blur(radius: 5)
            }

            SilhouetteCanvas(silhouette: silhouette, lineWidth: 1.6, opacity: 1.0)
        }
    }
}

private struct SilhouetteCanvas: View {
    let silhouette: AreaSilhouette
    let lineWidth: CGFloat
    let opacity: Double

    var body: some View {
        Canvas { context, size in
            guard let bbox = silhouette.bbox else { return }
            let pad: CGFloat = 12
            let drawW = size.width - 2 * pad
            let drawH = size.height - 2 * pad
            guard drawW > 0, drawH > 0 else { return }

            // Equirectangular projection with longitude scaled by cos(centerLat)
            // so trails don't look horizontally stretched.
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
                    let lat = pt[0], lon = pt[1]
                    let x = xOffset + (lon - bbox.w) * lonScale * scale
                    // Flip y because lat grows up, screen y grows down.
                    let y = yOffset + canvasH - (lat - bbox.s) * scale
                    let p = CGPoint(x: x, y: y)
                    if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
                }
                context.stroke(
                    path,
                    with: .color(color(for: line.d).opacity(opacity)),
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
                )
            }
        }
    }

    private func color(for difficulty: String) -> Color {
        switch difficulty {
        case "e": return .green
        case "m": return .orange
        case "h": return .red
        default:  return .gray
        }
    }
}
