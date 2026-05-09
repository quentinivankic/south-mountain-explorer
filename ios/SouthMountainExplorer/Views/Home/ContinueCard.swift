import SwiftUI

struct ContinueCard: View {
    let area: AreaSummary

    @Environment(AreaSilhouetteService.self) private var silhouettes
    @Environment(ProgressService.self) private var progress
    @Environment(AreaDataService.self) private var areas

    private var cachedArea: Area? { areas.cachedArea(id: area.id) }
    private var totalTrails: Int { cachedArea?.resolvedTrailCount ?? area.trailCount ?? 0 }
    private var completedCount: Int { progress.completionCount(in: area.id) }

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            artwork
                .frame(height: 200)
                .frame(maxWidth: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))

            VStack(alignment: .leading, spacing: 6) {
                Label("Continue exploring", systemImage: "arrow.uturn.forward.circle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.85))

                Text(area.name)
                    .font(.title3)
                    .fontWeight(.semibold)
                    .foregroundStyle(.white)
                    .lineLimit(1)

                if totalTrails > 0 {
                    Text("\(completedCount)/\(totalTrails) trails")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.85))
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassEffect(in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .padding(8)
        }
    }

    @ViewBuilder
    private var artwork: some View {
        if let silhouette = silhouettes.silhouette(for: area.id) {
            ZStack {
                Color.black.opacity(0.95)
                ContinueGlow(silhouette: silhouette)
            }
        } else {
            LinearGradient(colors: [.indigo, .blue], startPoint: .topLeading, endPoint: .bottomTrailing)
        }
    }
}

private struct ContinueGlow: View {
    let silhouette: AreaSilhouette

    var body: some View {
        ZStack {
            silhouetteCanvas(lineWidth: 7, opacity: 0.45)
                .blur(radius: 6)
            silhouetteCanvas(lineWidth: 2.0, opacity: 1.0)
        }
    }

    private func silhouetteCanvas(lineWidth: CGFloat, opacity: Double) -> some View {
        Canvas { context, size in
            guard let bbox = silhouette.bbox else { return }
            let pad: CGFloat = 16
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
                    let lat = pt[0], lon = pt[1]
                    let x = xOffset + (lon - bbox.w) * lonScale * scale
                    let y = yOffset + canvasH - (lat - bbox.s) * scale
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
                    with: .color(color.opacity(opacity)),
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
                )
            }
        }
    }
}
