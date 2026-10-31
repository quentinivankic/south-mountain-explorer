import Foundation

enum TurnCueSymbol: Sendable, Equatable {
    case keepLeft, keepRight, turnLeft, turnRight, sharpLeft, sharpRight
    case offTrail, backOnTrail, gpsWeak

    var isLeft: Bool { self == .keepLeft || self == .turnLeft || self == .sharpLeft }
    var isRight: Bool { self == .keepRight || self == .turnRight || self == .sharpRight }
}

/// Spoken and banner text. Pure; callers pass the unit preference so this
/// file never touches `UserDefaults` or `UnitFormatter` (shared).
enum TurnCuePhrasing {
    static let feetPerMeter = 3.28084

    /// "80 meters" / "250 feet". Metric rounds to 10 m; imperial to 50 ft
    /// with a 100 ft floor.
    static func spokenDistance(meters: Double, units: UnitsPreference) -> String {
        switch units {
        case .metric:
            let v = max(10, Int((meters / 10).rounded()) * 10)
            return "\(v) \(v == 1 ? "meter" : "meters")"
        case .imperial:
            let v = max(100, Int((meters * feetPerMeter / 50).rounded()) * 50)
            return "\(v) \(v == 1 ? "foot" : "feet")"
        }
    }

    /// Short banner string: "80 m" / "250 ft". Metric rounds to 5 m,
    /// imperial to 10 ft; under 10 m / 25 ft reads "Now".
    static func shortDistance(meters: Double, units: UnitsPreference) -> String {
        switch units {
        case .metric:
            if meters < 10 { return "Now" }
            return "\(Int((meters / 5).rounded()) * 5) m"
        case .imperial:
            let ft = meters * feetPerMeter
            if ft < 25 { return "Now" }
            return "\(Int((ft / 10).rounded()) * 10) ft"
        }
    }

    static func action(_ symbol: TurnCueSymbol) -> String {
        switch symbol {
        case .keepLeft: return "keep left"
        case .keepRight: return "keep right"
        case .turnLeft: return "turn left"
        case .turnRight: return "turn right"
        case .sharpLeft: return "sharp left"
        case .sharpRight: return "sharp right"
        case .offTrail, .backOnTrail, .gpsWeak: return ""
        }
    }

    static func capitalized(_ s: String) -> String {
        guard let f = s.first else { return s }
        return f.uppercased() + s.dropFirst()
    }

    /// "In 80 meters, keep left to stay on National Trail".
    static func approach(symbol: TurnCueSymbol, meters: Double, trailName: String, units: UnitsPreference) -> String {
        let base = "In \(spokenDistance(meters: meters, units: units)), \(action(symbol))"
        return trailName.isEmpty ? base : "\(base) to stay on \(trailName)"
    }

    /// "Keep left".
    static func near(symbol: TurnCueSymbol) -> String {
        capitalized(action(symbol))
    }

    static func offTrail(trailName: String) -> String {
        trailName.isEmpty ? "You may be off the trail" : "You may be off \(trailName)"
    }

    static func backOnTrail(trailName: String) -> String {
        trailName.isEmpty ? "Back on the trail" : "Back on \(trailName)"
    }

    static let gpsWeak = "GPS signal weak"
}
