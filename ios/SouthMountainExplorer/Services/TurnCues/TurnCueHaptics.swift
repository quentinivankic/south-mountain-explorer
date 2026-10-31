import CoreHaptics
import Foundation
import UIKit

/// Plays distinct haptic patterns per cue. Uses CoreHaptics where the
/// hardware supports it; otherwise falls back to UIKit feedback generators
/// (simulator, older devices). Haptics do not fire while the app is locked
/// or backgrounded (an iOS limit).
@MainActor
final class TurnCueHaptics {
    private var engine: CHHapticEngine?
    private let supportsHaptics = CHHapticEngine.capabilitiesForHardware().supportsHaptics

    func play(_ pattern: TurnCueHapticPattern) {
        if supportsHaptics, playCore(pattern) { return }
        playFallback(pattern)
    }

    private func transient(_ time: TimeInterval, _ intensity: Float, _ sharpness: Float = 0.5) -> CHHapticEvent {
        CHHapticEvent(
            eventType: .hapticTransient,
            parameters: [
                CHHapticEventParameter(parameterID: .hapticIntensity, value: intensity),
                CHHapticEventParameter(parameterID: .hapticSharpness, value: sharpness),
            ],
            relativeTime: time
        )
    }

    private func events(for pattern: TurnCueHapticPattern) -> [CHHapticEvent] {
        switch pattern {
        case .approach:
            return [transient(0, 0.8)]
        case .nearLeft:
            return [transient(0, 1), transient(0.12, 1)]
        case .nearRight:
            return [transient(0, 1), transient(0.12, 1), transient(0.24, 1)]
        case .offTrail:
            let long = CHHapticEvent(
                eventType: .hapticContinuous,
                parameters: [
                    CHHapticEventParameter(parameterID: .hapticIntensity, value: 0.7),
                    CHHapticEventParameter(parameterID: .hapticSharpness, value: 0.3),
                ],
                relativeTime: 0, duration: 0.4
            )
            return [long, transient(0.6, 1)]
        case .backOnTrail:
            return [transient(0, 0.5, 0.3), transient(0.1, 0.9, 0.7)]
        }
    }

    private func playCore(_ pattern: TurnCueHapticPattern) -> Bool {
        do {
            if engine == nil {
                let e = try CHHapticEngine()
                e.isAutoShutdownEnabled = true
                engine = e
            }
            guard let engine else { return false }
            try engine.start()
            let player = try engine.makePlayer(with: CHHapticPattern(events: events(for: pattern), parameters: []))
            try player.start(atTime: CHHapticTimeImmediate)
            return true
        } catch {
            engine = nil
            return false
        }
    }

    private func playFallback(_ pattern: TurnCueHapticPattern) {
        switch pattern {
        case .approach:
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        case .nearLeft:
            tap(count: 2)
        case .nearRight:
            tap(count: 3)
        case .offTrail:
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
        case .backOnTrail:
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    private func tap(count: Int) {
        let gen = UIImpactFeedbackGenerator(style: .heavy)
        gen.impactOccurred()
        guard count > 1 else { return }
        Task { @MainActor in
            for _ in 1..<count {
                try? await Task.sleep(for: .milliseconds(120))
                gen.impactOccurred()
            }
        }
    }
}
