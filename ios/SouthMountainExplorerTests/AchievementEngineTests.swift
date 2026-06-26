import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `AchievementEngine.evaluate` — the pure Dex badge
/// derivation. All time-based badges (seasons, early-bird, distinct
/// days) are checked against a FIXED UTC calendar so results don't
/// depend on the machine's time zone.
struct AchievementEngineTests {

    // MARK: - Fixtures

    /// Fixed UTC calendar so month / hour / day-of bucketing is
    /// deterministic regardless of host time zone.
    private var utc: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }

    /// Build a UTC date from components. Force-unwrap is fine in tests.
    private func date(_ y: Int, _ mo: Int, _ d: Int, _ h: Int = 12) -> Date {
        var comps = DateComponents()
        comps.year = y; comps.month = mo; comps.day = d; comps.hour = h
        return utc.date(from: comps)!
    }

    private func trail(_ id: String, _ difficulty: Difficulty, _ miles: Double = 2) -> Trail {
        Trail(id: id, name: id, distanceMi: miles, difficulty: difficulty, segments: [])
    }

    private func hike(_ id: String, started: Date, miles: Double, completed: [String] = []) -> SavedRecording {
        SavedRecording(
            id: id, areaId: "a", startedAt: started,
            endedAt: started.addingTimeInterval(3600),
            distanceMi: miles, durationSeconds: 3600,
            completedTrailIds: completed, path: [])
    }

    private func badge(_ all: [Achievement], _ id: String) -> Achievement? {
        all.first { $0.id == id }
    }

    // MARK: - Empty state

    @Test func emptyHistoryEarnsNothing() {
        let trails = [trail("t1", .easy), trail("t2", .hard)]
        let result = AchievementEngine.evaluate(
            trails: trails, hikes: [], completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(result.allSatisfy { !$0.isEarned })
        // Trailblazer + Pathfinder + Completionist, easy + hard
        // difficulty (no moderate present), 4 distance, 4 dedication.
        #expect(badge(result, "trailblazer") != nil)
        #expect(badge(result, "easy-first") != nil)
        #expect(badge(result, "hard-first") != nil)
        #expect(badge(result, "moderate-first") == nil) // area has none
    }

    // MARK: - Milestones

    @Test func trailblazerEarnedOnFirstHikeWithEarliestDate() {
        let early = date(2025, 3, 1)
        let late = date(2025, 6, 1)
        let hikes = [hike("h2", started: late, miles: 1),
                     hike("h1", started: early, miles: 1)]
        let result = AchievementEngine.evaluate(
            trails: [trail("t1", .easy)], hikes: hikes,
            completedTrailIds: [], completionDates: [:], calendar: utc)
        let tb = badge(result, "trailblazer")
        #expect(tb?.isEarned == true)
        #expect(tb?.earnedDate == early) // earliest, regardless of input order
    }

    @Test func completionistTracksProgressAndEarnsAtFullSet() {
        let trails = [trail("t1", .easy), trail("t2", .moderate), trail("t3", .hard)]
        let dates = ["t1": date(2025, 1, 1), "t2": date(2025, 2, 1)]
        let partial = AchievementEngine.evaluate(
            trails: trails, hikes: [], completedTrailIds: ["t1", "t2"],
            completionDates: dates, calendar: utc)
        let c = badge(partial, "completionist")
        #expect(c?.isEarned == false)
        #expect(c?.progress?.current == 2)
        #expect(c?.progress?.target == 3)
        #expect(c?.earnedDate == nil)

        // Now complete the set — earn date is the LAST completion.
        let allDates = dates.merging(["t3": date(2025, 3, 1)]) { _, b in b }
        let full = AchievementEngine.evaluate(
            trails: trails, hikes: [], completedTrailIds: ["t1", "t2", "t3"],
            completionDates: allDates, calendar: utc)
        let c2 = badge(full, "completionist")
        #expect(c2?.isEarned == true)
        #expect(c2?.earnedDate == date(2025, 3, 1))
    }

    @Test func completionistOmittedWhenAreaHasNoTrails() {
        let result = AchievementEngine.evaluate(
            trails: [], hikes: [], completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(badge(result, "completionist") == nil)
    }

    @Test func orphanCompletionsDoNotCountTowardCompletionist() {
        // A completion whose trail id is no longer in the area set
        // (rotated out by a Refresh) must not inflate the count.
        let trails = [trail("t1", .easy)]
        let result = AchievementEngine.evaluate(
            trails: trails, hikes: [],
            completedTrailIds: ["t1", "stale-id"],
            completionDates: ["t1": date(2025, 1, 1), "stale-id": date(2025, 1, 2)],
            calendar: utc)
        let c = badge(result, "completionist")
        #expect(c?.progress?.current == 1)
        #expect(c?.isEarned == true) // 1 of 1, stale id ignored
    }

    // MARK: - Difficulty

    @Test func difficultyBadgeEarnedFromMatchingCompletion() {
        let trails = [trail("e1", .easy), trail("h1", .hard)]
        let result = AchievementEngine.evaluate(
            trails: trails, hikes: [], completedTrailIds: ["e1"],
            completionDates: ["e1": date(2025, 5, 5)], calendar: utc)
        #expect(badge(result, "easy-first")?.isEarned == true)
        #expect(badge(result, "easy-first")?.earnedDate == date(2025, 5, 5))
        #expect(badge(result, "hard-first")?.isEarned == false)
    }

    // MARK: - Distance

    @Test func distanceTiersAccumulateAcrossHikes() {
        // Three 4-mile hikes = 12 mi total → clears the 10 mi tier,
        // not the 25 mi tier.
        let hikes = [
            hike("h1", started: date(2025, 1, 1), miles: 4),
            hike("h2", started: date(2025, 1, 2), miles: 4),
            hike("h3", started: date(2025, 1, 3), miles: 4)
        ]
        let result = AchievementEngine.evaluate(
            trails: [trail("t1", .easy)], hikes: hikes,
            completedTrailIds: [], completionDates: [:], calendar: utc)
        #expect(badge(result, "dist-10")?.isEarned == true)
        #expect(badge(result, "dist-25")?.isEarned == false)
        // 10 mi tier crossed on the 3rd hike (8 mi after two, 12 after three).
        #expect(badge(result, "dist-10")?.earnedDate == date(2025, 1, 3))
        // Partial progress on the 25 mi tier: 12 of 25 mi (in meters).
        let p = badge(result, "dist-25")?.progress
        #expect(p?.metric == .distanceMeters)
        #expect(abs((p?.current ?? 0) - 12 * AchievementEngine.metersPerMile) < 0.001)
    }

    // MARK: - Dedication

    @Test func fourSeasonsNeedsAllFourAndDatesTheClosingHike() {
        // Winter, spring, summer — only 3 seasons, not earned.
        let threeHikes = [
            hike("w", started: date(2025, 1, 15), miles: 1),
            hike("sp", started: date(2025, 4, 15), miles: 1),
            hike("su", started: date(2025, 7, 15), miles: 1)
        ]
        let three = AchievementEngine.evaluate(
            trails: [], hikes: threeHikes, completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(badge(three, "four-seasons")?.isEarned == false)
        #expect(badge(three, "four-seasons")?.progress?.current == 3)

        // Add a fall hike — earned, dated to the fall hike (the closer).
        let fall = hike("fa", started: date(2025, 10, 15), miles: 1)
        let four = AchievementEngine.evaluate(
            trails: [], hikes: threeHikes + [fall], completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(badge(four, "four-seasons")?.isEarned == true)
        #expect(badge(four, "four-seasons")?.earnedDate == date(2025, 10, 15))
    }

    @Test func earlyBirdNeedsPre7amStart() {
        let noonHike = hike("noon", started: date(2025, 1, 1, 12), miles: 1)
        let noEarly = AchievementEngine.evaluate(
            trails: [], hikes: [noonHike], completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(badge(noEarly, "early-bird")?.isEarned == false)

        let dawnHike = hike("dawn", started: date(2025, 1, 2, 6), miles: 1)
        let early = AchievementEngine.evaluate(
            trails: [], hikes: [noonHike, dawnHike], completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(badge(early, "early-bird")?.isEarned == true)
        #expect(badge(early, "early-bird")?.earnedDate == date(2025, 1, 2, 6))
    }

    @Test func longHaulerNeedsFiveMileSingleHike() {
        let shortHikes = [hike("a", started: date(2025, 1, 1), miles: 3),
                          hike("b", started: date(2025, 1, 2), miles: 4)]
        let none = AchievementEngine.evaluate(
            trails: [], hikes: shortHikes, completedTrailIds: [],
            completionDates: [:], calendar: utc)
        // 7 mi total but no single 5-mi hike.
        #expect(badge(none, "long-hauler")?.isEarned == false)

        let big = hike("big", started: date(2025, 1, 3), miles: 6)
        let earned = AchievementEngine.evaluate(
            trails: [], hikes: shortHikes + [big], completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(badge(earned, "long-hauler")?.isEarned == true)
        #expect(badge(earned, "long-hauler")?.earnedDate == date(2025, 1, 3))
    }

    @Test func regularCountsDistinctDaysAndEarnsAtTen() {
        // Ten hikes on ten distinct days → earned, dated to the 10th day.
        var hikes: [SavedRecording] = []
        for day in 1...10 {
            hikes.append(hike("h\(day)", started: date(2025, 1, day), miles: 1))
        }
        let result = AchievementEngine.evaluate(
            trails: [], hikes: hikes, completedTrailIds: [],
            completionDates: [:], calendar: utc)
        let r = badge(result, "regular")
        #expect(r?.isEarned == true)
        #expect(r?.earnedDate == date(2025, 1, 10))
        #expect(r?.progress?.current == 10)
    }

    @Test func regularTreatsMultipleHikesSameDayAsOneDay() {
        // Two hikes on the same calendar day count as one distinct day.
        let hikes = [
            hike("morning", started: date(2025, 1, 1, 8), miles: 1),
            hike("evening", started: date(2025, 1, 1, 18), miles: 1)
        ]
        let result = AchievementEngine.evaluate(
            trails: [], hikes: hikes, completedTrailIds: [],
            completionDates: [:], calendar: utc)
        #expect(badge(result, "regular")?.progress?.current == 1)
    }
}
