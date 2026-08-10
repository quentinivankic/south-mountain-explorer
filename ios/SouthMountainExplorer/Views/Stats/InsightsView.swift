import SwiftUI

/// Advanced Stats ("Insights") — the Pro-tier depth layer pushed from the
/// Stats tab. Renders personal records, hiking streaks, lifetime rollups,
/// and per-year totals from `StatsInsights.compute`. Pure presentation; all
/// math lives in `StatsInsights` (unit-tested separately).
///
/// NOTE: this screen is not yet gated — the Pro paywall/entitlement check
/// is added with the StoreKit work. Building the feature first keeps that
/// PR focused on billing.
struct InsightsView: View {
    let hikes: [SavedRecording]

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    /// Everest's height (m) for the lifetime "you've climbed N Everests"
    /// fun stat — the same framing AllTrails-style year-in-reviews use.
    private let everestMeters = 8849.0

    /// CACHED. This was a computed property, and `body` reads it FIFTEEN times
    /// per evaluation — each read re-ran `StatsInsights.compute`, whose default
    /// `ascentFor` runs the full `elevationStats` pipeline over every hike's
    /// entire GPS path, plus per-hike Calendar interval math. That meant
    /// 15 x (all hikes x all GPS points) per render of this screen.
    @State private var insights: StatsInsights = .empty

    var body: some View {
        List {
            streaksSection
            recordsSection
            lifetimeSection
            byYearSection
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Insights")
        .navigationBarTitleDisplayMode(.inline)
        // Compute once per hike-set change instead of on every body evaluation.
        .task(id: hikes.count) { insights = StatsInsights.compute(hikes: hikes) }
    }

    // MARK: - Streaks

    private var streaksSection: some View {
        Section("Streaks") {
            HStack(spacing: 12) {
                metric("\(insights.currentWeekStreak)", "Week streak", "flame.fill")
                metric("\(insights.longestWeekStreak)", "Longest", "trophy.fill")
                metric("\(insights.totalDaysOut)", "Days out", "figure.hiking")
            }
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
        }
    }

    private func metric(_ value: String, _ label: String, _ icon: String) -> some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(.tint)
            Text(value)
                .font(.title2.weight(.semibold))
                .monospacedDigit()
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    // MARK: - Personal records

    private var recordsSection: some View {
        Section("Personal Records") {
            if let r = insights.longestHike {
                recordRow("Longest hike", "arrow.left.and.right",
                          UnitFormatter.distance(miles: r.value, units: units), r.date)
            }
            if let r = insights.biggestClimb {
                recordRow("Biggest climb", "mountain.2.fill",
                          UnitFormatter.elevation(meters: r.value, units: units), r.date)
            }
            if let r = insights.longestDuration {
                recordRow("Longest time", "clock.fill",
                          Self.hoursMinutes(seconds: Int(r.value)), r.date)
            }
            if let r = insights.fastestPace {
                recordRow("Fastest pace", "hare.fill",
                          UnitFormatter.pace(metersPerSecond: 1609.344 / r.value, units: units), r.date)
            }
            if let r = insights.mostMilesInADay {
                recordRow("Most in a day", "sun.max.fill",
                          UnitFormatter.distance(miles: r.value, units: units), r.periodStart)
            }
            if let r = insights.mostHikesInAMonth {
                let n = Int(r.value)
                recordRow("Best month", "calendar",
                          n == 1 ? "1 hike" : "\(n) hikes", r.periodStart, dateStyle: .month)
            }
        }
    }

    private enum RecordDateStyle { case day, month }

    private func recordRow(_ title: String, _ icon: String, _ value: String,
                           _ date: Date, dateStyle: RecordDateStyle = .day) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.body)
                .foregroundStyle(.tint)
                .frame(width: 26)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                Text(dateStyle == .month
                     ? date.formatted(.dateTime.month(.wide).year())
                     : date.formatted(date: .abbreviated, time: .omitted))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(value)
                .font(.body.weight(.semibold))
                .monospacedDigit()
        }
    }

    // MARK: - Lifetime

    private var lifetimeSection: some View {
        Section("Lifetime") {
            LabeledContent("Total distance",
                           value: UnitFormatter.distance(miles: insights.totalMiles, units: units))
            LabeledContent("Total climbed",
                           value: UnitFormatter.elevation(meters: insights.totalAscentMeters, units: units))
            if insights.totalAscentMeters > 0 {
                LabeledContent("That's",
                               value: String(format: "%.1f× Everest", insights.totalAscentMeters / everestMeters))
            }
        }
    }

    // MARK: - By year

    private var byYearSection: some View {
        Group {
            if !insights.byYear.isEmpty {
                Section("By Year") {
                    ForEach(insights.byYear) { y in
                        HStack {
                            Text(String(y.year))
                                .font(.body.weight(.semibold))
                                .monospacedDigit()
                            Spacer()
                            VStack(alignment: .trailing, spacing: 1) {
                                Text(UnitFormatter.distance(miles: y.miles, units: units))
                                    .monospacedDigit()
                                Text("^[\(y.hikeCount) hike](inflect: true) · \(UnitFormatter.elevation(meters: y.ascentMeters, units: units))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - Helpers

    /// "Xh Ym" / "Ym" duration formatting, mirroring StatsView's private
    /// helper so record durations read the same as the summary card.
    static func hoursMinutes(seconds: Int) -> String {
        let h = seconds / 3600
        let m = (seconds % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
}
