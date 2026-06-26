import Foundation

/// Derives an area's Dex badges from existing data — recorded hikes,
/// trail completions, and the area's trail set. Pure and stateless: no
/// persistence, no `@Observable`, no `now`. Every badge (and its earn
/// date) is replayed from history, so the Dex populates retroactively
/// the first time a user opens it and stays correct after any data
/// reset / import.
///
/// `evaluate` is `Calendar`-injectable so season / time-of-day / day
/// bucketing is testable without depending on the host machine's time
/// zone (tests pass a fixed UTC calendar; the app passes `.current`).
enum AchievementEngine {
    /// One mile in meters. Distance badges keep canonical meters
    /// internally; `DexView` formats them through `UnitFormatter`.
    static let metersPerMile = 1609.344

    /// Build the full ordered badge list for an area.
    ///
    /// - Parameters:
    ///   - hikes: recorded hikes already filtered to this area.
    ///   - completedTrailIds: every completed trail id for the area
    ///     (existence drives earned-state + counts).
    ///   - completionDates: parsed completion timestamps, a subset of
    ///     `completedTrailIds` (drives the earn date only — a trail
    ///     with an unparseable stamp still counts as complete).
    ///
    /// Badges that can't apply to this area are omitted: a difficulty
    /// badge only appears when the area actually has a trail of that
    /// difficulty, so the Dex never shows an unearnable cell.
    static func evaluate(
        trails: [Trail],
        hikes: [SavedRecording],
        completedTrailIds: Set<String>,
        completionDates: [String: Date],
        calendar: Calendar = .current
    ) -> [Achievement] {
        let sortedHikes = hikes.sorted { $0.startedAt < $1.startedAt }
        let validTrailIds = Set(trails.map(\.id))
        let validCompleted = completedTrailIds.intersection(validTrailIds)

        var out: [Achievement] = []
        out.append(contentsOf: milestoneBadges(
            trails: trails, sortedHikes: sortedHikes,
            validCompleted: validCompleted, completionDates: completionDates))
        out.append(contentsOf: difficultyBadges(
            trails: trails, validCompleted: validCompleted,
            completionDates: completionDates))
        out.append(contentsOf: distanceBadges(sortedHikes: sortedHikes))
        out.append(contentsOf: dedicationBadges(
            sortedHikes: sortedHikes, calendar: calendar))
        return out
    }

    // MARK: - Milestones

    private static func milestoneBadges(
        trails: [Trail],
        sortedHikes: [SavedRecording],
        validCompleted: Set<String>,
        completionDates: [String: Date]
    ) -> [Achievement] {
        var badges: [Achievement] = []

        // Trailblazer — first recorded hike.
        let firstHike = sortedHikes.first?.startedAt
        badges.append(Achievement(
            id: "trailblazer",
            title: "Trailblazer",
            detail: "Record your first hike here.",
            symbol: "figure.hiking",
            category: .milestones,
            isEarned: firstHike != nil,
            earnedDate: firstHike,
            progress: nil))

        // Pathfinder — first completed trail. Binary badge, so no
        // progress bar (a "1 of 1" ring would be noise).
        let firstCompletionDate = completionDates
            .filter { validCompleted.contains($0.key) }
            .values.min()
        badges.append(Achievement(
            id: "pathfinder",
            title: "Pathfinder",
            detail: "Complete your first trail here.",
            symbol: "flag.checkered",
            category: .milestones,
            isEarned: !validCompleted.isEmpty,
            earnedDate: firstCompletionDate,
            progress: nil))

        // Completionist — every trail in the area. Only meaningful when
        // the area actually has trails.
        if !trails.isEmpty {
            let completedCount = validCompleted.count
            let earned = completedCount >= trails.count
            // The completion that finished the set is the latest one.
            let earnedDate = earned
                ? completionDates.filter { validCompleted.contains($0.key) }.values.max()
                : nil
            badges.append(Achievement(
                id: "completionist",
                title: "Completionist",
                detail: "Complete every trail in this area.",
                symbol: "crown.fill",
                category: .milestones,
                isEarned: earned,
                earnedDate: earnedDate,
                progress: AchievementProgress(
                    current: Double(completedCount),
                    target: Double(trails.count),
                    metric: .count(unit: "trails"))))
        }

        return badges
    }

    // MARK: - Difficulty

    private static func difficultyBadges(
        trails: [Trail],
        validCompleted: Set<String>,
        completionDates: [String: Date]
    ) -> [Achievement] {
        let specs: [(Difficulty, String, String, String, String)] = [
            (.easy, "easy-first", "Easygoer", "leaf.fill", "Complete an easy trail here."),
            (.moderate, "moderate-first", "Pacesetter", "flame.fill", "Complete a moderate trail here."),
            (.hard, "hard-first", "Summit Seeker", "mountain.2.fill", "Complete a hard trail here.")
        ]

        return specs.compactMap { difficulty, id, title, symbol, detail in
            let idsOfDifficulty = trails
                .filter { $0.difficulty == difficulty }
                .map(\.id)
            // Skip difficulties the area doesn't contain — an unearnable
            // badge is worse than an absent one.
            guard !idsOfDifficulty.isEmpty else { return nil }

            let completedOfDifficulty = idsOfDifficulty.filter(validCompleted.contains)
            let earnedDate = completedOfDifficulty
                .compactMap { completionDates[$0] }
                .min()
            return Achievement(
                id: id,
                title: title,
                detail: detail,
                symbol: symbol,
                category: .difficulty,
                isEarned: !completedOfDifficulty.isEmpty,
                earnedDate: earnedDate,
                progress: nil)
        }
    }

    // MARK: - Distance

    private static func distanceBadges(sortedHikes: [SavedRecording]) -> [Achievement] {
        // (id, title, symbol, threshold in miles). `detail` is left
        // empty — DexView synthesizes a unit-aware requirement string
        // from `progress.target` so it honors the imperial/metric toggle.
        let specs: [(String, String, String, Double)] = [
            ("dist-10", "Trail Legs", "shoeprints.fill", 10),
            ("dist-25", "Wanderer", "figure.hiking", 25),
            ("dist-50", "Pathmaster", "map.fill", 50),
            ("dist-100", "Century Club", "trophy.fill", 100)
        ]
        let totalMeters = sortedHikes.reduce(0.0) { $0 + $1.distanceMi * metersPerMile }

        return specs.map { id, title, symbol, miles in
            let target = miles * metersPerMile
            let earned = totalMeters >= target
            let earnedDate = earned
                ? dateCrossingCumulativeDistance(sortedHikes, targetMeters: target)
                : nil
            return Achievement(
                id: id,
                title: title,
                detail: "",
                symbol: symbol,
                category: .distance,
                isEarned: earned,
                earnedDate: earnedDate,
                progress: AchievementProgress(
                    current: min(totalMeters, target),
                    target: target,
                    metric: .distanceMeters))
        }
    }

    // MARK: - Dedication

    private static func dedicationBadges(
        sortedHikes: [SavedRecording],
        calendar: Calendar
    ) -> [Achievement] {
        var badges: [Achievement] = []

        // Four Seasons — a hike in each meteorological season.
        var seenSeasons: Set<Int> = []
        var fourSeasonsDate: Date? = nil
        for hike in sortedHikes {
            seenSeasons.insert(meteorologicalSeason(of: hike.startedAt, calendar: calendar))
            if seenSeasons.count == 4 && fourSeasonsDate == nil {
                fourSeasonsDate = hike.startedAt
            }
        }
        badges.append(Achievement(
            id: "four-seasons",
            title: "Four Seasons",
            detail: "Hike here in all four seasons.",
            symbol: "cloud.sun.fill",
            category: .dedication,
            isEarned: seenSeasons.count == 4,
            earnedDate: fourSeasonsDate,
            progress: AchievementProgress(
                current: Double(seenSeasons.count),
                target: 4,
                metric: .count(unit: "seasons"))))

        // Early Bird — a hike started before 7 AM local.
        let earlyBird = sortedHikes.first {
            calendar.component(.hour, from: $0.startedAt) < 7
        }
        badges.append(Achievement(
            id: "early-bird",
            title: "Early Bird",
            detail: "Start a hike before 7 AM.",
            symbol: "sunrise.fill",
            category: .dedication,
            isEarned: earlyBird != nil,
            earnedDate: earlyBird?.startedAt,
            progress: nil))

        // Long Hauler — a single hike of 5 mi or more.
        let longHaul = sortedHikes.first { $0.distanceMi >= 5 }
        badges.append(Achievement(
            id: "long-hauler",
            title: "Long Hauler",
            detail: "Finish a 5 mi hike in one outing.",
            symbol: "figure.run",
            category: .dedication,
            isEarned: longHaul != nil,
            earnedDate: longHaul?.startedAt,
            progress: nil))

        // Regular — hikes on 10 distinct calendar days.
        let dayTarget = 10
        var seenDays: Set<Date> = []
        var regularDate: Date? = nil
        for hike in sortedHikes {
            seenDays.insert(calendar.startOfDay(for: hike.startedAt))
            if seenDays.count == dayTarget && regularDate == nil {
                regularDate = hike.startedAt
            }
        }
        badges.append(Achievement(
            id: "regular",
            title: "Regular",
            detail: "Hike here on 10 different days.",
            symbol: "calendar",
            category: .dedication,
            isEarned: seenDays.count >= dayTarget,
            earnedDate: regularDate,
            progress: AchievementProgress(
                current: Double(min(seenDays.count, dayTarget)),
                target: Double(dayTarget),
                metric: .count(unit: "days"))))

        return badges
    }

    // MARK: - Helpers

    /// Meteorological season index for a date: 0 winter (Dec–Feb),
    /// 1 spring (Mar–May), 2 summer (Jun–Aug), 3 fall (Sep–Nov).
    /// Meteorological (not astronomical) so it's a pure month lookup —
    /// no equinox math, no latitude/hemisphere dependence.
    static func meteorologicalSeason(of date: Date, calendar: Calendar) -> Int {
        switch calendar.component(.month, from: date) {
        case 12, 1, 2: return 0
        case 3, 4, 5: return 1
        case 6, 7, 8: return 2
        default: return 3
        }
    }

    /// Replays hikes in chronological order and returns the start date
    /// of the hike whose cumulative distance first reaches `targetMeters`.
    /// `nil` if the total never gets there.
    private static func dateCrossingCumulativeDistance(
        _ sortedHikes: [SavedRecording],
        targetMeters: Double
    ) -> Date? {
        var running = 0.0
        for hike in sortedHikes {
            running += hike.distanceMi * metersPerMile
            if running >= targetMeters { return hike.startedAt }
        }
        return nil
    }
}
