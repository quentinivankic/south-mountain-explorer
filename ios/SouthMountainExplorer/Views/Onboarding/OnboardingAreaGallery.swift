import SwiftUI

/// "Discover" hero: a 2x2 gallery of real park silhouettes (Zion, Grand Canyon,
/// Joshua Tree, Griffith Park), baked to a small JSON resource by
/// scripts/gen-onboarding-gallery.py. Shows the app's own trail data instead of
/// a stock SF Symbol. Static and offline.
struct OnboardingAreaGallery: View {
    private struct Area: Identifiable {
        let id: String
        let label: String
        let lines: [[CGPoint]]
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
                    Silhouette(lines: area.lines, bounds: area.bounds)
                        .stroke(Color.completedTrail,
                                style: StrokeStyle(lineWidth: 1.3, lineCap: .round, lineJoin: .round))
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

    // MARK: - Shape

    private struct Silhouette: Shape {
        let lines: [[CGPoint]]
        let bounds: CGRect
        func path(in rect: CGRect) -> Path {
            guard bounds.width > 0, bounds.height > 0 else { return Path() }
            let scale = min(rect.width / bounds.width, rect.height / bounds.height)
            let ox = rect.minX + (rect.width - bounds.width * scale) / 2 - bounds.minX * scale
            let oy = rect.minY + (rect.height - bounds.height * scale) / 2 - bounds.minY * scale
            var p = Path()
            for line in lines {
                guard let first = line.first else { continue }
                p.move(to: CGPoint(x: first.x * scale + ox, y: first.y * scale + oy))
                for pt in line.dropFirst() {
                    p.addLine(to: CGPoint(x: pt.x * scale + ox, y: pt.y * scale + oy))
                }
            }
            return p
        }
    }

    // MARK: - Load

    private struct RawArea: Decodable { let id: String; let label: String; let lines: [[[Double]]] }
    private struct Payload: Decodable { let areas: [RawArea] }

    private static func load() -> [Area] {
        guard
            let url = Bundle.main.url(forResource: "onboarding-discover-gallery", withExtension: "json"),
            let data = try? Data(contentsOf: url),
            let payload = try? JSONDecoder().decode(Payload.self, from: data)
        else { return [] }

        return payload.areas.map { raw in
            var minX = CGFloat.infinity, minY = CGFloat.infinity
            var maxX = -CGFloat.infinity, maxY = -CGFloat.infinity
            let lines: [[CGPoint]] = raw.lines.map { line in
                line.compactMap { pair -> CGPoint? in
                    guard pair.count >= 2 else { return nil }
                    let x = CGFloat(pair[0]), y = CGFloat(pair[1])
                    minX = min(minX, x); minY = min(minY, y)
                    maxX = max(maxX, x); maxY = max(maxY, y)
                    return CGPoint(x: x, y: y)
                }
            }
            let bounds = minX.isFinite && maxX > minX && maxY > minY
                ? CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
                : CGRect(x: 0, y: 0, width: 1, height: 1)
            return Area(id: raw.id, label: raw.label, lines: lines, bounds: bounds)
        }
    }
}
