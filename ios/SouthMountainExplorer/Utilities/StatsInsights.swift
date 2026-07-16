import Foundation

/// Pure, view-independent computation of the **Advanced Stats** (Insights)
/// shown to Pro users on top of the free Stats tab. Everything here is
/// derived from the hike history alone — personal records, hiking streaks,
/// per-year totals, and a monthly distance/elevation trend — so it can be
/// unit-tested without a view layer, StoreKit, or the areas cache.
///
/// Distances are miles, elevation is meters (converted at display time by
/// `UnitFormatter`), durations are seconds. All fields are plain values so
/// the struct is `Equatable` for cheap SwiftUI diffing.
struct StatsInsights: Equatable {

    // MARK: Personal records

    /// A single "best ever" hike, carrying enough to render a card + deep
    /// link to the hike. `value` is the raw metric (miles / meters /
    /// seconds / seconds-per-mile); the view formats it per unit prefs.
    struct Record: Equatable {
        let hikeId: String
        let date: Date
        let value: Double
    }

    /// Most-in-a-window records aggregate several hikes, so they carry the
    /// window (a day / month) instead of a single hike id.
    struct WindowRecord: Equatable {
        /// Start of the calendar window (day or month) the record falls in.
        let periodStart: Date
        let value: Double
    }

    let longestHike: Record?          // max distanceMi
    let biggestClimb: Record?         // max per-hike ascent (m)
    let longestDuration: Record?      // max durationSeconds
    let fastestPace: Record?          // min seconds-per-mile (fastest)
    let mostMilesInADay: WindowRecord?    // max summed miles over a calendar day
    let mostHikesInAMonth: WindowRecord?  // max hike count over a calendar month

    // MARK: Streaks (by calendar week — daily is too strict for hiking)

    /// Number of consecutive calendar weeks, ending at the most recent
    /// hike's week, that each contain >= 1 hike. 0 for empty history.
    let currentWeekStreak: Int
    /// Longest run of consecutive hiking weeks anywhere in the history.
    let longestWeekStreak: Int
    /// Count of distinct calendar days with >= 1 hike.
    let totalDaysOut: Int

    // MARK: Trends

    /// Per-calendar-year totals, newest year first.
    struct YearTotals: Equatable, Identifiable {
        let year: Int
        let hikeCount: Int
        let miles: Double
        let ascentMeters: Double
        let seconds: Int
        var id: Int { year }
    }
    let byYear: [YearTotals]

    /// Lifetime rollups (handy for the "= N Everests" fun stat + headline).
    let totalMiles: Double
    let totalAscentMeters: Double

    static let empty = StatsInsights(
        longestHike: nil, biggestClimb: nil, longestDuration: nil,
        fastestPace: nil, mostMilesInADay: nil, mostHikesInAMonth: nil,
        currentWeekStreak: 0, longestWeekStreak: 0, totalDaysOut: 0,
        byYear: [], totalMiles: 0, totalAscentMeters: 0
    )
}

extension StatsInsights {

    /// Compute insights from the full hike history.
    ///
    /// `ascentFor` supplies each hike's ascent in meters — injected so the
    /// heavy `elevationStats(path:)` pass stays testable and callers can
    /// reuse an already-computed value. Defaults to computing it from the
    /// hike's own path.
    ///
    /// `calendar` and `now` are injectable so streak math is deterministic
    /// under test (no dependence on the machine's clock/zone).
    static func compute(
        hikes: [SavedRecording],
        now: Date = Date(),
        calendar: Calendar = .current,
        ascentFor: (SavedRecording) -> Double = { elevationStats(path: $0.path)?.totalAscentMeters ?? 0 }
    ) -> StatsInsights {
        guard !hikes.isEmpty else { return .empty }

        var longest: Record?
        var biggestClimb: Record?
        var longestDur: Record?
        var fastest: Record?
        var totalMiles = 0.0
        var totalAscent = 0.0

        // Grouping accumulators.
        var milesByDay: [Date: Double] = [:]
        var hikesByMonth: [Date: Int] = [:]
        var yearAgg: [Int: StatsInsights.YearTotals] = [:]
        var hikingDays = Set<Date>()
        var hikingWeeks = Set<Date>()

        for hike in hikes {
            let miles = hike.distanceMi
            let ascent = ascentFor(hike)
            totalMiles += miles
            totalAscent += ascent

            if longest == nil || miles > longest!.value {
                longest = Record(hikeId: hike.id, date: hike.startedAt, value: miles)
            }
            if ascent > 0, biggestClimb == nil || ascent > biggestClimb!.value {
                biggestClimb = Record(hikeId: hike.id, date: hike.startedAt, value: ascent)
            }
            if longestDur == nil || hike.durationSeconds > Int(longestDur!.value) {
                longestDur = Record(hikeId: hike.id, date: hike.startedAt, value: Double(hike.durationSeconds))
            }
            // Fastest pace: seconds per mile, smaller is faster. Ignore
            // zero-distance / zero-duration hikes (roam blips) so a
            // degenerate 0-mi record can't masquerade as "infinitely fast."
            if miles > 0.1, hike.durationSeconds > 0 {
                let pace = Double(hike.durationSeconds) / miles
                if fastest == nil || pace < fastest!.value {
                    fastest = Record(hikeId: hike.id, date: hike.startedAt, value: pace)
                }
            }

            if let day = calendar.dateInterval(of: .day, for: hike.startedAt)?.start {
                milesByDay[day, default: 0] += miles
                hikingDays.insert(day)
            }
            if let month = calendar.dateInterval(of: .month, for: hike.startedAt)?.start {
                hikesByMonth[month, default: 0] += 1
            }
            if let week = calendar.dateInterval(of: .weekOfYear, for: hike.startedAt)?.start {
                hikingWeeks.insert(week)
            }

            let year = calendar.component(.year, from: hike.startedAt)
            let prev = yearAgg[year]
            yearAgg[year] = YearTotals(
                year: year,
                hikeCount: (prev?.hikeCount ?? 0) + 1,
                miles: (prev?.miles ?? 0) + miles,
                ascentMeters: (prev?.ascentMeters ?? 0) + ascent,
                seconds: (prev?.seconds ?? 0) + hike.durationSeconds
            )
        }

        let bestDay = milesByDay.max { $0.value < $1.value }
            .map { WindowRecord(periodStart: $0.key, value: $0.value) }
        let bestMonth = hikesByMonth.max { $0.value < $1.value }
            .map { WindowRecord(periodStart: $0.key, value: Double($0.value)) }

        let (current, longestStreak) = weekStreaks(
            weeks: hikingWeeks, now: now, calendar: calendar
        )

        return StatsInsights(
            longestHike: longest,
            biggestClimb: biggestClimb,
            longestDuration: longestDur,
            fastestPace: fastest,
            mostMilesInADay: bestDay,
            mostHikesInAMonth: bestMonth,
            currentWeekStreak: current,
            longestWeekStreak: longestStreak,
            totalDaysOut: hikingDays.count,
            byYear: yearAgg.values.sorted { $0.year > $1.year },
            totalMiles: totalMiles,
            totalAscentMeters: totalAscent
        )
    }

    /// Given the set of calendar-week start dates that contain a hike,
    /// return (currentStreak, longestStreak) in consecutive weeks.
    ///
    /// The current streak counts back from the week containing `now`; if
    /// the user hasn't hiked this week yet it still counts a streak ending
    /// LAST week (so a Monday check doesn't read the weekend's streak as
    /// broken), but a two-week gap breaks it.
    private static func weekStreaks(
        weeks: Set<Date>, now: Date, calendar: Calendar
    ) -> (current: Int, longest: Int) {
        guard !weeks.isEmpty else { return (0, 0) }
        let sorted = weeks.sorted()
        let weekSeconds: TimeInterval = 7 * 24 * 3600

        // Longest run of consecutive weeks (adjacent = ~1 week apart).
        var longest = 1
        var run = 1
        for i in 1..<sorted.count {
            let gap = sorted[i].timeIntervalSince(sorted[i - 1])
            if gap < weekSeconds * 1.5 {
                run += 1
                longest = max(longest, run)
            } else {
                run = 1
            }
        }

        // Current streak: walk backward from this week (or last week) while
        // each earlier week is present.
        guard let thisWeek = calendar.dateInterval(of: .weekOfYear, for: now)?.start else {
            return (0, longest)
        }
        let lastWeek = thisWeek.addingTimeInterval(-weekSeconds)
        var anchor: Date
        if weeks.contains(thisWeek) {
            anchor = thisWeek
        } else if weeks.contains(lastWeek) {
            anchor = lastWeek
        } else {
            return (0, longest)   // no hike this week or last → streak is over
        }
        var current = 0
        while weeks.contains(anchor) {
            current += 1
            anchor = anchor.addingTimeInterval(-weekSeconds)
            // Re-snap to the week boundary so DST / leap seconds don't
            // drift the anchor off the stored week-start keys.
            anchor = calendar.dateInterval(of: .weekOfYear, for: anchor)?.start ?? anchor
        }
        return (current, longest)
    }
}
