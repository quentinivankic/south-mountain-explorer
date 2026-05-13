import UIKit
import QuartzCore

/// Samples the UI thread's frame rate via `CADisplayLink` and posts
/// the result to `MapDiagnostics.shared.fps`. Used only when the
/// debug HUD is enabled, so the display-link overhead is zero in
/// release builds (it only runs when the user has explicitly
/// flipped the Developer toggle).
///
/// Sampling cadence: every 0.5 s a new FPS value is published.
/// Anything tighter and the value bounces too much to read on the
/// HUD; anything looser and the user can't tell when a pan starts
/// dropping frames.
@MainActor
final class FPSCounter: NSObject {
    static let shared = FPSCounter()

    private var displayLink: CADisplayLink?
    private var lastTimestamp: CFTimeInterval = 0
    private var frameCount: Int = 0

    private override init() {
        super.init()
    }

    func start() {
        guard displayLink == nil else { return }
        let link = CADisplayLink(target: self, selector: #selector(tick(_:)))
        link.add(to: .main, forMode: .common)
        displayLink = link
        lastTimestamp = 0
        frameCount = 0
    }

    func stop() {
        displayLink?.invalidate()
        displayLink = nil
        lastTimestamp = 0
        frameCount = 0
        MapDiagnostics.shared.fps = 0
    }

    @objc private func tick(_ link: CADisplayLink) {
        if lastTimestamp == 0 {
            lastTimestamp = link.timestamp
            return
        }
        frameCount += 1
        let elapsed = link.timestamp - lastTimestamp
        if elapsed >= 0.5 {
            MapDiagnostics.shared.fps = Double(frameCount) / elapsed
            frameCount = 0
            lastTimestamp = link.timestamp
        }
    }
}
