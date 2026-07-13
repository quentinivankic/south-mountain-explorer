import SwiftUI

/// A whisper-faint, full-bleed backdrop of a real dense trail network
/// (Tonto National Forest — bundled + downsampled to `trail-mesh-tonto.json`)
/// drawn in the slightest grey. Replaces flat black/white app backgrounds with
/// a subtle sense of place. Non-interactive and theme-aware; decoded once and
/// shared across every surface.
struct TrailMeshBackground: View {
    @Environment(\.colorScheme) private var colorScheme

    private static let mesh: AreaSilhouette? = {
        guard let url = Bundle.main.url(forResource: "trail-mesh-tonto", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let s = try? JSONDecoder().decode(AreaSilhouette.self, from: data)
        else { return nil }
        return s
    }()

    var body: some View {
        // Canvas alone in a ZStack sibling to Color was reporting a smaller
        // ideal size than the full-bleed background box, so the mesh only
        // painted a fraction of the screen (bottom third, off-center) even
        // though the box itself filled correctly. Wrapping in GeometryReader
        // and explicitly frame-locking the Canvas to its reported size forces
        // it to always draw across the FULL available area, not just
        // whatever size it decides it wants.
        GeometryReader { proxy in
            Canvas { context, size in
                guard let mesh = Self.mesh, let bbox = mesh.bbox else { return }
                // Equirectangular projection (longitude scaled by cos(centerLat) so
                // trails aren't horizontally stretched), scaled to FILL the space so
                // the web reaches every edge — density over completeness.
                let centerLat = (bbox.s + bbox.n) / 2
                let lonScale = cos(centerLat * .pi / 180)
                let xRange = max((bbox.e - bbox.w) * lonScale, .leastNonzeroMagnitude)
                let yRange = max(bbox.n - bbox.s, .leastNonzeroMagnitude)
                let scale = max(size.width / xRange, size.height / yRange)
                let canvasW = xRange * scale
                let canvasH = yRange * scale
                let xOffset = (size.width - canvasW) / 2
                let yOffset = (size.height - canvasH) / 2

                let ink = strokeColor
                for line in mesh.l {
                    guard line.p.count >= 2 else { continue }
                    var path = Path()
                    for (i, pt) in line.p.enumerated() {
                        guard pt.count >= 2 else { continue }
                        let x = xOffset + (pt[1] - bbox.w) * lonScale * scale
                        // Flip y — latitude grows up, screen y grows down.
                        let y = yOffset + canvasH - (pt[0] - bbox.s) * scale
                        let p = CGPoint(x: x, y: y)
                        if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
                    }
                    context.stroke(path, with: .color(ink),
                                   style: StrokeStyle(lineWidth: 0.6, lineCap: .round, lineJoin: .round))
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .allowsHitTesting(false)
        .ignoresSafeArea()
    }

    /// The "slightest grey" — a hair brighter on dark, a hair darker on light.
    /// Tune these two opacities to taste.
    private var strokeColor: Color {
        colorScheme == .dark ? Color.white.opacity(0.15) : Color.black.opacity(0.12)
    }
}

extension View {
    /// Swap a flat surface for the base system colour plus the faint Tonto
    /// trail mesh, when the Settings toggle is on. MUST be applied to the
    /// List/ScrollView INSIDE a NavigationStack (not outside it) — a
    /// NavigationStack paints its own opaque background that would cover this.
    /// Hides the default scroll/list background so the mesh shows through.
    func trailMeshBackground() -> some View {
        modifier(TrailMeshBackgroundModifier())
    }
}

private struct TrailMeshBackgroundModifier: ViewModifier {
    @AppStorage(StorageKeys.trailMesh) private var enabled = true

    func body(content: Content) -> some View {
        content
            .scrollContentBackground(enabled ? .hidden : .automatic)
            .background {
                if enabled {
                    ZStack {
                        Color(.systemBackground)
                        TrailMeshBackground()
                    }
                    .ignoresSafeArea()
                }
            }
    }
}
