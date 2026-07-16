import Testing
import Foundation
@testable import SouthMountainExplorer

/// Tests for `StatsInsights.compute` — the pure Advanced-Stats engine.
/// A fixed UTC Gregorian calendar + injected `now`/`ascentFor` keep the
/// records, streak, and per-year math deterministic regardless of the
/// machine's clock and zone.
struct StatsInsightsTests {

    private var cal: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }

    /// Build a date at UTC midnight-plus-hours for the given Y/M/D.
    private func date(_ y: Int, _ m: Int, _ d: Int, hour: Int = 8) -> Date {
        var comps = DateComponents()
        comps.year = y; comps.month = m; comps.day = d; comps.hour = hour
        return cal.date(from: comps)!
    }

    private func hike(
        _ id: String, _ start: Date,
        miles: Double, minutes: Int
    ) -> SavedRecording {
        SavedRecording(
            id: id, areaId: "a",
            startedAt: start,
            endedAt: start.addingTimeInterval(Double(minutes) * 60),
            distanceMi: miles,
            durationSeconds: minutes * 60,
            completedTrailIds: [],
            path: []
        )
    }

    // MARK: - Empty

    @Test func emptyHistory_isEmpty() {
        let out = StatsInsights.compute(hikes: [], now: date(2026, 7, 16), calendar: cal)
        #expect(out == .empty)
        #expect(out.longestHike == nil)
        #expect(out.currentWeekStreak == 0)
    }

    // MARK: - Records

    @Test func picksLongestAndFastestAndClimb() {
        let hikes = [
            hike("short", date(2026, 3, 1), miles: 2, minutes: 60),   // 30 min/mi
            hike("long",  date(2026, 3, 8), miles: 9, minutes: 300),  // longest distance
            hike("fast",  date(2026, 3, 9), miles: 4, minutes: 40),   // 10 min/mi (fastest)
        ]
        // Injected ascent: the middle hike climbs the most.
        let ascent: (SavedRecording) -> Double = { h in
            ["short": 100.0, "long": 800.0, "fast": 50.0][h.id] ?? 0
        }
        let out = StatsInsights.compute(hikes: hikes, now: date(2026, 3, 10), calendar: cal, ascentFor: ascent)

        #expect(out.longestHike?.hikeId == "long")
        #expect(out.longestHike?.value == 9)
        #expect(out.fastestPace?.hikeId == "fast")     // smallest sec/mi
        #expect(out.biggestClimb?.hikeId == "long")
        #expect(out.biggestClimb?.value == 800)
        #expect(out.longestDuration?.hikeId == "long") // 300 min
    }

    @Test func fastestPace_ignoresZeroDistanceBlips() {
        let hikes = [
            hike("blip", date(2026, 3, 1), miles: 0.0, minutes: 1),   // would be "instant"
            hike("real", date(2026, 3, 2), miles: 3.0, minutes: 45),  // 15 min/mi
        ]
        let out = StatsInsights.compute(hikes: hikes, now: date(2026, 3, 3), calendar: cal, ascentFor: { _ in 0 })
        #expect(out.fastestPace?.hikeId == "real")
    }

    // MARK: - Windowed records

    @Test func mostMilesInADay_sumsSameDayHikes() {
        let hikes = [
            hike("am", date(2026, 4, 4, hour: 7), miles: 5, minutes: 90),
            hike("pm", date(2026, 4, 4, hour: 16), miles: 4, minutes: 80),  // same day → 9 total
            hike("other", date(2026, 4, 5), miles: 7, minutes: 120),        // 7 on its own day
        ]
        let out = StatsInsights.compute(hikes: hikes, now: date(2026, 4, 6), calendar: cal, ascentFor: { _ in 0 })
        #expect(out.mostMilesInADay?.value == 9)
        #expect(out.totalDaysOut == 2)
    }

    // MARK: - Streaks

    @Test func weekStreak_currentAndLongest() {
        // Hikes in weeks of: Jun 1, Jun 8, Jun 15 (3 in a row), then a gap,
        // then Jul 6 and Jul 13 (current run of 2, "now" = Jul 16).
        let hikes = [
            hike("w1", date(2026, 6, 1), miles: 3, minutes: 60),
            hike("w2", date(2026, 6, 8), miles: 3, minutes: 60),
            hike("w3", date(2026, 6, 15), miles: 3, minutes: 60),
            hike("w5", date(2026, 7, 6), miles: 3, minutes: 60),
            hike("w6", date(2026, 7, 13), miles: 3, minutes: 60),
        ]
        let out = StatsInsights.compute(hikes: hikes, now: date(2026, 7, 16), calendar: cal, ascentFor: { _ in 0 })
        #expect(out.longestWeekStreak == 3)
        #expect(out.currentWeekStreak == 2)   // Jul 13 week + Jul 6 week
    }

    @Test func weekStreak_brokenWhenNoRecentHike() {
        let hikes = [
            hike("a", date(2026, 5, 4), miles: 3, minutes: 60),
            hike("b", date(2026, 5, 11), miles: 3, minutes: 60),
        ]
        // now is well after → neither this week nor last week has a hike.
        let out = StatsInsights.compute(hikes: hikes, now: date(2026, 7, 16), calendar: cal, ascentFor: { _ in 0 })
        #expect(out.currentWeekStreak == 0)
        #expect(out.longestWeekStreak == 2)
    }

    // MARK: - Per-year totals

    @Test func byYear_aggregatesNewestFirst() {
        let hikes = [
            hike("y25a", date(2025, 8, 1), miles: 4, minutes: 60),
            hike("y25b", date(2025, 9, 1), miles: 6, minutes: 90),
            hike("y26",  date(2026, 1, 1), miles: 10, minutes: 150),
        ]
        let out = StatsInsights.compute(hikes: hikes, now: date(2026, 7, 16), calendar: cal,
                                        ascentFor: { _ in 100 })
        #expect(out.byYear.map(\.year) == [2026, 2025])
        #expect(out.byYear.first?.hikeCount == 1)
        #expect(out.byYear.first?.miles == 10)
        let y25 = out.byYear.first { $0.year == 2025 }
        #expect(y25?.hikeCount == 2)
        #expect(y25?.miles == 10)               // 4 + 6
        #expect(y25?.ascentMeters == 200)       // 100 + 100
        #expect(out.totalMiles == 20)
    }
}
