import SwiftUI
import UIKit

/// Pure timing and projection for the loading draw-in. Kept free of UIKit state
/// so the stagger, ordering inputs and fit math are unit-testable.
nonisolated enum LoadingDrawIn {
    typealias BBox = (w: Double, s: Double, e: Double, n: Double)

    /// Every line has finished drawing by this many seconds.
    static let totalDuration: TimeInterval = 1.0
    static let linePadding: CGFloat = 24
    static let lineWidth: CGFloat = 2.4

    /// A single-line silhouette takes the whole duration so a tiny area does
    /// not snap in; everything else uses 0.4 s per line.
    static func perLineDuration(count: Int) -> TimeInterval {
        count == 1 ? totalDuration : 0.4
    }

    /// Gap between consecutive line starts, sized so the last line lands
    /// exactly at `totalDuration`.
    static func stagger(count: Int) -> TimeInterval {
        count > 1 ? (totalDuration - perLineDuration(count: count)) / Double(count - 1) : 0
    }

    /// Delay before line `index` (in draw order) starts.
    static func beginOffset(index: Int, count: Int) -> TimeInterval {
        Double(index) * stagger(count: count)
    }

    struct Fit: Equatable {
        let scale: Double
        let lonScale: Double
        let xOffset: Double
        let yOffset: Double
        let canvasHeight: Double
    }

    /// Same fit the Canvas used: equirectangular with cos(lat) correction,
    /// aspect-preserving, centered inside `linePadding` on every side.
    static func fit(bbox: BBox, size: CGSize) -> Fit? {
        let pad = Double(linePadding)
        let drawW = Double(size.width) - 2 * pad
        let drawH = Double(size.height) - 2 * pad
        guard drawW > 0, drawH > 0 else { return nil }
        let centerLat = (bbox.s + bbox.n) / 2
        let lonScale = cos(centerLat * .pi / 180)
        let xRange = max((bbox.e - bbox.w) * lonScale, .leastNonzeroMagnitude)
        let yRange = max(bbox.n - bbox.s, .leastNonzeroMagnitude)
        let scale = min(drawW / xRange, drawH / yRange)
        let canvasW = xRange * scale
        let canvasH = yRange * scale
        return Fit(
            scale: scale,
            lonScale: lonScale,
            xOffset: pad + (drawW - canvasW) / 2,
            yOffset: pad + (drawH - canvasH) / 2,
            canvasHeight: canvasH
        )
    }

    /// Projects `[lat, lon]` points into view coordinates. Malformed points
    /// (fewer than two values) are skipped.
    static func project(_ points: [[Double]], bbox: BBox, fit: Fit) -> [CGPoint] {
        points.compactMap { pt in
            guard pt.count >= 2 else { return nil }
            let x = fit.xOffset + (pt[1] - bbox.w) * fit.lonScale * fit.scale
            let y = fit.yOffset + fit.canvasHeight - (pt[0] - bbox.s) * fit.scale
            return CGPoint(x: x, y: y)
        }
    }

    /// Trail color for a difficulty code, matching the previous Canvas colors.
    static func color(forDifficulty d: String) -> UIColor {
        switch d {
        case "e": return .systemGreen
        case "m": return .systemOrange
        case "h": return .systemRed
        default: return .systemGray
        }
    }
}

/// UIKit host that draws each trail as a CAShapeLayer and animates `strokeEnd`
/// with Core Animation. The animations run on the render server, so they keep
/// playing while the main thread is busy mounting the map.
@MainActor
final class LoadingSilhouetteLayerHost: UIView {
    private var lines: [SilhouetteLine] = []
    private var bounds4: [Double] = []
    private var shapeLayers: [CAShapeLayer] = []
    private var laidOutSize: CGSize = .zero
    private var hasStarted = false

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        isOpaque = false
        isUserInteractionEnabled = false
        registerForTraitChanges([UITraitUserInterfaceStyle.self]) { (host: LoadingSilhouetteLayerHost, _) in
            host.applyColors()
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    /// Rebuilds the layers only when the silhouette actually changed, so a
    /// SwiftUI update never restarts a running or completed animation.
    func configure(lines newLines: [SilhouetteLine], bounds newBounds: [Double]) {
        guard newLines != lines || newBounds != bounds4 else { return }
        lines = newLines
        bounds4 = newBounds
        shapeLayers.forEach { $0.removeFromSuperlayer() }
        shapeLayers = newLines.map { line in
            let shape = CAShapeLayer()
            shape.fillColor = nil
            shape.lineWidth = LoadingDrawIn.lineWidth
            shape.lineCap = .round
            shape.lineJoin = .round
            shape.strokeEnd = 1
            layer.addSublayer(shape)
            return shape
        }
        applyColors()
        laidOutSize = .zero
        hasStarted = false
        setNeedsLayout()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        setNeedsLayout()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.width > 0, bounds.height > 0 else { return }
        if bounds.size != laidOutSize {
            laidOutSize = bounds.size
            updatePaths()
        }
        if !hasStarted, window != nil { startAnimations() }
    }

    private func applyColors() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for (shape, line) in zip(shapeLayers, lines) {
            shape.strokeColor = LoadingDrawIn
                .color(forDifficulty: line.d)
                .resolvedColor(with: traitCollection).cgColor
        }
        CATransaction.commit()
    }

    /// Re-lays paths into the current bounds. Changing `path` never touches a
    /// running `strokeEnd` animation, so rotation and fold/unfold do not restart it.
    private func updatePaths() {
        guard bounds4.count == 4 else { return }
        let box: LoadingDrawIn.BBox = (bounds4[0], bounds4[1], bounds4[2], bounds4[3])
        guard let fit = LoadingDrawIn.fit(bbox: box, size: bounds.size) else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for (shape, line) in zip(shapeLayers, lines) {
            let pts = LoadingDrawIn.project(line.p, bbox: box, fit: fit)
            let path = CGMutablePath()
            if let first = pts.first {
                path.move(to: first)
                pts.dropFirst().forEach { path.addLine(to: $0) }
            }
            shape.frame = bounds
            shape.path = path
        }
        CATransaction.commit()
    }

    private func startAnimations() {
        hasStarted = true
        // Reduce Motion: show the completed silhouette immediately.
        guard !UIAccessibility.isReduceMotionEnabled else { return }
        let count = shapeLayers.count
        let duration = LoadingDrawIn.perLineDuration(count: count)
        for (i, shape) in shapeLayers.enumerated() {
            let anim = CABasicAnimation(keyPath: "strokeEnd")
            anim.fromValue = 0
            anim.toValue = 1
            anim.duration = duration
            anim.beginTime = shape.convertTime(CACurrentMediaTime(), from: nil)
                + LoadingDrawIn.beginOffset(index: i, count: count)
            // Hold the start value until this line's turn; model value stays 1.
            anim.fillMode = .backwards
            anim.timingFunction = CAMediaTimingFunction(name: .linear)
            shape.add(anim, forKey: "drawIn")
        }
    }
}

struct LoadingSilhouetteLayers: UIViewRepresentable {
    let lines: [SilhouetteLine]
    let bounds4: [Double]

    func makeUIView(context: Context) -> LoadingSilhouetteLayerHost {
        let host = LoadingSilhouetteLayerHost()
        host.configure(lines: lines, bounds: bounds4)
        return host
    }

    func updateUIView(_ host: LoadingSilhouetteLayerHost, context: Context) {
        host.configure(lines: lines, bounds: bounds4)
    }
}
