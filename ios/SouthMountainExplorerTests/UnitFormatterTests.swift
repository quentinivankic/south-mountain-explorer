import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `UnitFormatter` — distance + elevation formatting
/// across both imperial / metric branches. Pin the rounding rules
/// so a future "tweak the decimals" doesn't quietly shift output
/// across the whole app.
struct UnitFormatterTests {

    @Test func imperialDistanceShortValuesTwoDecimals() {
        // Under 10 mi → two decimals to match existing hike-detail
        // / trail-row formatting.
        #expect(UnitFormatter.distance(miles: 1.4, units: .imperial) == "1.40 mi")
        #expect(UnitFormatter.distance(miles: 9.99, units: .imperial) == "9.99 mi")
        #expect(UnitFormatter.distance(miles: 0, units: .imperial) == "0.00 mi")
    }

    @Test func imperialDistanceLongValuesOneDecimal() {
        // 10+ mi → one decimal. Area totals routinely hit 100+ mi
        // and two decimals would be silly there.
        #expect(UnitFormatter.distance(miles: 10, units: .imperial) == "10.0 mi")
        #expect(UnitFormatter.distance(miles: 104.6, units: .imperial) == "104.6 mi")
    }

    @Test func metricDistanceConvertsAndFormats() {
        // 1 mi = 1.609344 km. Pin to two decimals at < 10 km.
        #expect(UnitFormatter.distance(miles: 1, units: .metric) == "1.61 km")
        // 10 mi = 16.09344 km → one decimal regime.
        #expect(UnitFormatter.distance(miles: 10, units: .metric) == "16.1 km")
    }

    @Test func elevationImperialRoundsToInteger() {
        // 100 m * 3.28084 = 328.084 ft, rounded to 328.
        #expect(UnitFormatter.elevation(meters: 100, units: .imperial) == "328 ft")
        #expect(UnitFormatter.elevation(meters: 0, units: .imperial) == "0 ft")
        // 1 m = 3.28 ft → rounds to 3.
        #expect(UnitFormatter.elevation(meters: 1, units: .imperial) == "3 ft")
    }

    @Test func elevationMetricRoundsToInteger() {
        #expect(UnitFormatter.elevation(meters: 100.4, units: .metric) == "100 m")
        #expect(UnitFormatter.elevation(meters: 100.6, units: .metric) == "101 m")
        #expect(UnitFormatter.elevation(meters: 0, units: .metric) == "0 m")
    }

    @Test func suffixesMatchUnit() {
        #expect(UnitFormatter.distanceSuffix(units: .imperial) == "mi")
        #expect(UnitFormatter.distanceSuffix(units: .metric) == "km")
        #expect(UnitFormatter.elevationSuffix(units: .imperial) == "ft")
        #expect(UnitFormatter.elevationSuffix(units: .metric) == "m")
    }

    @Test func valueOnlyVariantsMatch() {
        // Stat-grid pattern: value + unit rendered as separate text
        // fields. The value variant should match the
        // suffixed-string's numeric part exactly.
        #expect(UnitFormatter.distanceValue(miles: 1.4, units: .imperial) == "1.40")
        #expect(UnitFormatter.distanceValue(miles: 104.6, units: .imperial) == "104.6")
        #expect(UnitFormatter.elevationValue(meters: 100, units: .imperial) == "328")
        #expect(UnitFormatter.elevationValue(meters: 100, units: .metric) == "100")
    }
}
