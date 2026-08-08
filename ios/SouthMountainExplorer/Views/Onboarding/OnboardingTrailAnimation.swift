import SwiftUI

/// Onboarding hero: South Mountain's trails lighting cyan one by one, left to
/// right, on a gentle loop. The geometry is baked to a small normalized JSON
/// resource by `scripts/gen-onboarding-shapes.py` (77 trails, ~7 KB), so the
/// hero is instant and works fully offline on first launch — no area fetch.
struct OnboardingTrailAnimation: View {
    /// trails → segments → points, in a normalized frame (y points down).
    private let trails: [[[CGPoint]]]
    /// Bounding box of all points, for fit-to-frame at draw time.
    private let bounds: CGRect

    /// Seconds to light every trail, then to hold them all lit before looping.
    private let lightDuration: Double = 4.5
    private let holdDuration: Double = 1.4
    private var cycle: Double { lightDuration + holdDuration }

    init() {
        let (t, b) = Self.load()
        trails = t
        bounds = b
    }

    var body: some View {
        TimelineView(.animation) { timeline in
            Canvas { ctx, size in
                guard !trails.isEmpty, bounds.width > 0, bounds.height > 0 else { return }

                // Fractional "cursor": how many trails are lit right now.
                let phase = timeline.date.timeIntervalSinceReferenceDate
                    .truncatingRemainder(dividingBy: cycle)
                let litFrac = phase < lightDuration
                    ? (phase / lightDuration) * Double(trails.count)
                    : Double(trails.count)

                // Fit the content bbox into the canvas: uniform scale, centered.
                let scale = min(size.width / bounds.width, size.height / bounds.height)
                let ox = (size.width - bounds.width * scale) / 2 - bounds.minX * scale
                let oy = (size.height - bounds.height * scale) / 2 - bounds.minY * scale
                func project(_ p: CGPoint) -> CGPoint {
                    CGPoint(x: p.x * scale + ox, y: p.y * scale + oy)
                }

                let base = Color.gray.opacity(0.22)
                for (i, segments) in trails.enumerated() {
                    var path = Path()
                    for seg in segments {
                        guard let first = seg.first else { continue }
                        path.move(to: project(first))
                        for p in seg.dropFirst() { path.addLine(to: project(p)) }
                    }
                    // 0 before the cursor reaches this trail, ramping to 1 as it
                    // passes — a soft one-by-one sweep rather than a hard snap.
                    let lit = max(0, min(1, litFrac - Double(i)))
                    let color = lit <= 0
                        ? base
                        : Color.completedTrail.opacity(0.35 + 0.65 * lit)
                    ctx.stroke(
                        path,
                        with: .color(color),
                        style: StrokeStyle(lineWidth: lit > 0 ? 2.2 : 1.6,
                                           lineCap: .round, lineJoin: .round)
                    )
                }
            }
        }
        .accessibilityHidden(true)
    }

    // MARK: - Load

    private struct Payload: Decodable { let trails: [[[[Double]]]] }

    private static func load() -> ([[[CGPoint]]], CGRect) {
        guard
            let url = Bundle.main.url(forResource: "onboarding-south-mountain", withExtension: "json"),
            let data = try? Data(contentsOf: url),
            let payload = try? JSONDecoder().decode(Payload.self, from: data)
        else { return ([], .zero) }

        var minX = CGFloat.infinity, minY = CGFloat.infinity
        var maxX = -CGFloat.infinity, maxY = -CGFloat.infinity
        let trails: [[[CGPoint]]] = payload.trails.map { segments in
            segments.map { seg in
                seg.compactMap { pair -> CGPoint? in
                    guard pair.count >= 2 else { return nil }
                    let x = CGFloat(pair[0]), y = CGFloat(pair[1])
                    minX = min(minX, x); minY = min(minY, y)
                    maxX = max(maxX, x); maxY = max(maxY, y)
                    return CGPoint(x: x, y: y)
                }
            }
        }
        guard minX.isFinite, maxX > minX, maxY > minY else { return (trails, .zero) }
        return (trails, CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY))
    }
}
