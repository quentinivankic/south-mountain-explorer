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

                // A cursor advancing through the trails in order. Each trail's
                // cyan is DRAWN along its length (trimmedPath 0 to progress) as
                // the cursor passes it, and consecutive draws overlap by
                // `drawSpan` trails so it reads as a continuous pen stroke rather
                // than each trail snapping on whole in a single frame.
                let count = Double(trails.count)
                let drawSpan = 4.0
                let phase = timeline.date.timeIntervalSinceReferenceDate
                    .truncatingRemainder(dividingBy: cycle)
                let cursor = phase < lightDuration
                    ? (phase / lightDuration) * (count + drawSpan)
                    : count + drawSpan   // hold: every trail fully drawn

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
                    // Faint full trail, always visible, so the park shape reads
                    // from the first frame.
                    ctx.stroke(path, with: .color(base),
                               style: StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round))
                    // Cyan drawn from the trail's start to `progress` of its
                    // total length, so it paints ALONG the trail rather than
                    // popping in all at once.
                    let progress = max(0, min(1, (cursor - Double(i)) / drawSpan))
                    if progress > 0 {
                        let drawn = path.trimmedPath(from: 0, to: CGFloat(progress))
                        ctx.stroke(drawn, with: .color(Color.completedTrail),
                                   style: StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))
                    }
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
