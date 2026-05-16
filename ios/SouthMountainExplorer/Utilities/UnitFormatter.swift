import Foundation

/// User-facing preference for distance + elevation display.
/// Persisted via `@AppStorage(StorageKeys.units)`. Default
/// `imperial` for the original AZ-based user base; metric is the
/// rest of the world.
enum UnitsPreference: String, CaseIterable, Identifiable {
    case imperial
    case metric

    var id: String { rawValue }

    var label: String {
        switch self {
        case .imperial: return "Imperial (mi / ft)"
        case .metric:   return "Metric (km / m)"
        }
    }
}

/// Centralized formatting for distance + elevation. Every display
/// site that shows mileage or climb reads `@AppStorage(StorageKeys.units)`
/// and routes through these helpers, so the imperial / metric
/// toggle in Settings affects the whole app in one place.
enum UnitFormatter {
    /// Convert internal-canonical meters to a human-readable
    /// distance string in the user's preferred unit. Trailing unit
    /// suffix included.
    static func distance(meters: Double, units: UnitsPreference) -> String {
        switch units {
        case .imperial:
            let mi = meters / 1609.344
            // Two decimals when under 10 mi (matches existing
            // hike-detail / trail-row formatting), one decimal
            // beyond — area totals were "%.1f mi" already.
            if mi < 10 {
                return String(format: "%.2f mi", mi)
            } else {
                return String(format: "%.1f mi", mi)
            }
        case .metric:
            let km = meters / 1000
            if km < 10 {
                return String(format: "%.2f km", km)
            } else {
                return String(format: "%.1f km", km)
            }
        }
    }

    /// Convert a `distanceMi` field (existing app-side unit) to
    /// a user-preferred display string. Many models still carry
    /// `distanceMi`; this is the bridge.
    static func distance(miles: Double, units: UnitsPreference) -> String {
        distance(meters: miles * 1609.344, units: units)
    }

    /// Elevation values in meters → user-preferred display string.
    /// Integer rounding because elevation noise + GPS precision
    /// don't justify decimals at trail scale.
    static func elevation(meters: Double, units: UnitsPreference) -> String {
        switch units {
        case .imperial:
            let ft = (meters * 3.28084).rounded()
            return "\(Int(ft)) ft"
        case .metric:
            return "\(Int(meters.rounded())) m"
        }
    }

    /// Short suffix (no value), used by stat-grid cards where the
    /// value and unit are rendered as separate text fields.
    static func distanceSuffix(units: UnitsPreference) -> String {
        units == .imperial ? "mi" : "km"
    }

    static func elevationSuffix(units: UnitsPreference) -> String {
        units == .imperial ? "ft" : "m"
    }

    /// Value-only number (no unit) for the stat-grid pattern.
    /// Matches the rounding rules of the unit-suffixed variants.
    static func distanceValue(miles: Double, units: UnitsPreference) -> String {
        switch units {
        case .imperial:
            return miles < 10 ? String(format: "%.2f", miles) : String(format: "%.1f", miles)
        case .metric:
            let km = miles * 1.609344
            return km < 10 ? String(format: "%.2f", km) : String(format: "%.1f", km)
        }
    }

    static func elevationValue(meters: Double, units: UnitsPreference) -> String {
        switch units {
        case .imperial:
            return "\(Int((meters * 3.28084).rounded()))"
        case .metric:
            return "\(Int(meters.rounded()))"
        }
    }
}
