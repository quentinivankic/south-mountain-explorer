import Foundation

enum TurnCueHapticPattern: Sendable, Equatable {
    case approach, nearLeft, nearRight, offTrail, backOnTrail
}

/// Environment a cue is delivered into. Built by the coordinator at dispatch time.
struct TurnCueDeliveryContext: Sendable, Equatable {
    var voiceEnabled: Bool
    var hapticsEnabled: Bool
    var voiceOverRunning: Bool
    var appActive: Bool
    var callActive: Bool
}

enum TurnCueAction: Sendable, Equatable {
    case speak(String)
    case announce(String)
    case haptic(TurnCueHapticPattern)
    case banner(TurnCueEvent)
}

/// Decides HOW an event is delivered from user settings and the
/// environment. The engine decides WHAT happened; keeping the two apart
/// lets each be tested alone.
enum TurnCueDispatcher {
    static func plan(_ event: TurnCueEvent, _ ctx: TurnCueDeliveryContext) -> [TurnCueAction] {
        var actions: [TurnCueAction] = [.banner(event)]
        if ctx.hapticsEnabled, let pattern = hapticPattern(for: event) {
            actions.append(.haptic(pattern))
        }
        if ctx.voiceEnabled, !ctx.callActive {
            if ctx.voiceOverRunning && ctx.appActive {
                actions.append(.announce(event.spoken))
            } else {
                actions.append(.speak(event.spoken))
            }
        }
        return actions
    }

    static func hapticPattern(for event: TurnCueEvent) -> TurnCueHapticPattern? {
        switch event.kind {
        case .approach: return .approach
        case .atJunction: return event.symbol.isLeft ? .nearLeft : .nearRight
        case .offTrail: return .offTrail
        case .backOnTrail: return .backOnTrail
        case .gpsWeak: return nil
        }
    }

    /// Unset keys mean ON.
    static func boolSetting(_ key: String, defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: key) as? Bool ?? true
    }
}
