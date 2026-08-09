import SwiftUI

/// "Discover" hero: a 2x2 gallery of real park silhouettes (Zion, Grand Canyon,
/// Joshua Tree, Griffith Park), baked to a small JSON resource by
/// scripts/gen-onboarding-gallery.py. Each trail is drawn in its difficulty
/// colour — green easy, orange moderate, red hard, gray unknown — matching
/// MapKitMapView so the cards read like the app's own map. Static and offline.
struct OnboardingAreaGallery: View {
    private struct Line { let points: [CGPoint]; let color: Color }
    private struct Area: Identifiable {
        let id: String
        let label: String
        let lines: [Line]
        let bounds: CGRect
    }
    private let areas: [Area]

    init() { areas = Self.load() }

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var body: some View {
        LazyVGrid(columns: columns, spacing: 12) {
            ForEach(areas) { area in
                VStack(spacing: 6) {
                    Canvas { ctx, size in
                        guard area.bounds.width > 0, area.bounds.height > 0 else { return }
                        let scale = min(size.width / area.bounds.width, size.height / area.bounds.height)
                        let ox = (size.width - area.bounds.width * scale) / 2 - area.bounds.minX * scale
                        let oy = (size.height - area.bounds.height * scale) / 2 - area.bounds.minY * scale
                        for line in area.lines {
                            guard let first = line.points.first else { continue }
                            var p = Path()
                            p.move(to: CGPoint(x: first.x * scale + ox, y: first.y * scale + oy))
                            for pt in line.points.dropFirst() {
                                p.addLine(to: CGPoint(x: pt.x * scale + ox, y: pt.y * scale + oy))
                            }
                            ctx.stroke(p, with: .color(line.color),
                                       style: StrokeStyle(lineWidth: 1.3, lineCap: .round, lineJoin: .round))
                        }
                    }
                    .padding(8)
                    .frame(height: 78)
                    .frame(maxWidth: .infinity)
                    .background(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(Color.gray.opacity(0.12))
                    )

                    Text(area.label)
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
        }
        .accessibilityHidden(true)
    }

    // MARK: - Load

    private struct RawLine: Decodable { let d: String; let p: [[Double]] }
    private struct RawArea: Decodable { let id: String; let label: String; let lines: [RawLine] }
    private struct Payload: Decodable { let areas: [RawArea] }

    /// Difficulty code → colour. Mirrors MapKitMapView.difficultyColor.
    private static func color(_ d: String) -> Color {
        switch d {
        case "e": return Color(.systemGreen)
        case "m": return Color(.systemOrange)
        case "h": return Color(.systemRed)
        default:  return Color(.systemGray)
        }
    }

    private static func load() -> [Area] {
        guard
            let url = Bundle.main.url(forResource: "onboarding-discover-gallery", withExtension: "json"),
            let data = try? Data(contentsOf: url),
            let payload = try? JSONDecoder().decode(Payload.self, from: data)
        else { return [] }

        return payload.areas.map { raw in
            var minX = CGFloat.infinity, minY = CGFloat.infinity
            var maxX = -CGFloat.infinity, maxY = -CGFloat.infinity
            let lines: [Line] = raw.lines.map { rl in
                let pts: [CGPoint] = rl.p.compactMap { pair in
                    guard pair.count >= 2 else { return nil }
                    let x = CGFloat(pair[0]), y = CGFloat(pair[1])
                    minX = min(minX, x); minY = min(minY, y)
                    maxX = max(maxX, x); maxY = max(maxY, y)
                    return CGPoint(x: x, y: y)
                }
                return Line(points: pts, color: color(rl.d))
            }
            let bounds = minX.isFinite && maxX > minX && maxY > minY
                ? CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
                : CGRect(x: 0, y: 0, width: 1, height: 1)
            return Area(id: raw.id, label: raw.label, lines: lines, bounds: bounds)
        }
    }
}
