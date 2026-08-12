import SwiftUI

/// Stats tab. Replaces the History tab — same data, augmented with
/// cumulative totals, records and streaks, and per-area completion
/// rows so the user sees engagement at a glance instead of having
/// to scroll a hike list to recall what they've done.
///
/// History list lives at the bottom of this view (as "Recent Hikes")
/// so the swipe-to-delete + tap-into-detail flow still works — we
/// reuse the existing `HikeRow` + `HikeDetailView`. No data migration:
/// stats are derived per-render from `RecordingService.loadHistory`
/// + `ProgressService.completions` + `AreaDataService.summaries`.
struct StatsView: View {
    @Environment(RecordingService.self) private var recording
    @Environment(ProgressService.self) private var progress
    @Environment(AreaDataService.self) private var areas
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var hikes: [SavedRecording] = []
    /// Starts true so the first frame shows the spinner, not a flash of
    /// "No Hikes Yet" before .task has loaded history.
    @State private var isLoading = true

    /// CACHED derived data. These were computed inline in `statsList`, so every
    /// body evaluation re-ran them — and `aggregate` calls `elevationStats` for
    /// EVERY hike, walking each one's entire GPS path, while `areaCompletionRows`
    /// re-scanned history and re-counted completions per area. Recomputed only
    /// when the inputs actually change (see `refreshDerived`).
    /// Hike whose detail screen was last opened, so returning restores to that
    /// row rather than the top of the list.
    @State private var lastViewedHikeId: String? = nil
    @State private var summary: StatsSummary = .empty
    @State private var areaRows: [AreaCompletionRowModel] = []

    /// Recompute the cached values. Cheap relative to doing it per render.
    private func refreshDerived() {
        summary = aggregate(hikes: hikes)
        areaRows = areaCompletionRows()
        insights = StatsInsights.compute(hikes: hikes)
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("Loading…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if hikes.isEmpty {
                    emptyState
                } else {
                    statsList
                }
            }
            .trailMeshBackground()
            .navigationTitle("Stats")
            .task { await loadHikes() }
            .task {
                // Hydrate geometry for the (few) areas with completions so the
                // per-area count is computed the authoritative fingerprint way
                // — matching the checkmarks — instead of the raw completions
                // dictionary. @Observable recomputes the rows once cached.
                for areaId in progress.completions.filter({ !$0.value.isEmpty }).keys {
                    _ = await areas.area(id: areaId)
                }
            }
            .refreshable { await loadHikes() }
            // Recompute the cached aggregates when the hikes change or an area
            // finishes hydrating, instead of on every body evaluation.
            .task(id: derivedKey) { refreshDerived() }
        }
    }

    /// Inputs the cached aggregates depend on.
    private var derivedKey: String {
        [
            String(hikes.count),
            hikes.first?.id ?? "-",
            String(progress.completions.count),
            String(areas.summaries.count),
        ].joined(separator: "|")
    }

    private var emptyState: some View {
        ContentUnavailableView(
            "No Hikes Yet",
            systemImage: "chart.line.uptrend.xyaxis",
            description: Text("Start recording a hike from any trail area and your stats will appear here.")
        )
    }

    private var statsList: some View {
        ScrollViewReader { proxy in
        List {
            Section {
                StatsSummaryCard(summary: summary)
                    .listRowInsets(.init(top: 8, leading: 16, bottom: 8, trailing: 16))
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }

            // Insights used to live behind a NavigationLink. Its content is
            // what people come to Stats for, so it's inlined here and the
            // separate screen is gone — along with the hikes-per-month chart,
            // which restated the same history the sections below do.
            streaksSection
            recordsSection
            byYearSection

            if !areaRows.isEmpty {
                Section("Area Progress") {
                    ForEach(areaRows) { row in
                        AreaCompletionRow(row: row)
                            .accessibilityIdentifier("area-progress-\(row.id)")
                    }
                }
            }

            Section("Recent Hikes") {
                ForEach(hikes) { hike in
                    NavigationLink {
                        // Record the opened hike HERE, not via a gesture on the
                        // row. A .simultaneousGesture(TapGesture()) on a
                        // NavigationLink competes with the link's own tap
                        // handling, which made rows need a long press to open.
                        HikeDetailView(hike: hike, areaName: areaName(for: hike.areaId))
                            .onAppear { lastViewedHikeId = hike.id }
                    } label: {
                        HikeRow(
                            hike: hike,
                            areaName: areaName(for: hike.areaId),
                            trailName: trailName(for: hike)
                        )
                    }
                    .id(hike.id)
                    .accessibilityIdentifier("hike-row-\(hike.id)")
                }
                .onDelete { indexSet in
                    Task { await deleteHikes(at: indexSet) }
                }
            }
        }
        .listStyle(.insetGrouped)
        // Coming back from a hike's detail used to land at the top of the page.
        // Restore to the row that was opened.
        .onAppear {
            guard let id = lastViewedHikeId else { return }
            proxy.scrollTo(id, anchor: .center)
        }
        }
    }

    // MARK: - Insights (inlined; the separate Insights screen is gone)

    /// Computed once per hike-set change, like the other aggregates — this
    /// walks every hike's full GPS path via elevationStats.
    @State private var insights: StatsInsights = .empty

    private var streaksSection: some View {
        Section("Streaks") {
            HStack(spacing: 0) {
                insightMetric("\(insights.currentWeekStreak)", "Week streak", "flame.fill")
                insightMetric("\(insights.longestWeekStreak)", "Longest", "trophy.fill")
                insightMetric("\(insights.totalDaysOut)", "Days out", "figure.hiking")
            }
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
        }
    }

    private func insightMetric(_ value: String, _ label: String, _ icon: String) -> some View {
        VStack(spacing: 4) {
            Image(systemName: icon).foregroundStyle(.tint)
            Text(value).font(.title3.bold().monospacedDigit())
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private var recordsSection: some View {
        Section("Personal Records") {
            if let r = insights.longestHike {
                insightRow("Longest hike", "arrow.left.and.right",
                           UnitFormatter.distance(miles: r.value, units: units), r.date)
            }
            if let r = insights.biggestClimb {
                insightRow("Biggest climb", "mountain.2.fill",
                           UnitFormatter.elevation(meters: r.value, units: units), r.date)
            }
            if let r = insights.longestDuration {
                // "Longest time" was ambiguous about what was being timed.
                insightRow("Longest activity time", "clock.fill",
                           Self.hoursMinutes(seconds: Int(r.value)), r.date)
            }
            if let r = insights.fastestPace {
                insightRow("Fastest pace", "hare.fill",
                           UnitFormatter.pace(metersPerSecond: 1609.344 / r.value, units: units), r.date)
            }
            if let r = insights.mostMilesInADay {
                // Names the unit: this row is distance, not hike count.
                insightRow("Most distance in a day", "sun.max.fill",
                           UnitFormatter.distance(miles: r.value, units: units), r.periodStart)
            }
            if let r = insights.mostHikesInAMonth {
                let n = Int(r.value)
                insightRow("Best month", "calendar",
                           n == 1 ? "1 hike" : "\(n) hikes", r.periodStart, monthOnly: true)
            }
        }
    }

    private func insightRow(_ title: String, _ icon: String, _ value: String,
                            _ date: Date, monthOnly: Bool = false) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(.tint)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                Text(monthOnly
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

    @ViewBuilder
    private var byYearSection: some View {
        if !insights.byYear.isEmpty {
            Section("By Year") {
                ForEach(insights.byYear) { y in
                    HStack {
                        Text(String(y.year)).font(.body.weight(.medium))
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

    /// h:mm for a duration in seconds.
    private static func hoursMinutes(seconds: Int) -> String {
        let h = seconds / 3600, m = (seconds % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    // MARK: - Data shaping

    /// Aggregate totals across the user's entire hike history. Cheap
    /// (O(hikes × pathPoints) — pathPoints dominate via the elevation
    /// calc) so a recompute on every body eval is fine at the
    /// typical-user scale (tens to low-hundreds of hikes).
    private func aggregate(hikes: [SavedRecording]) -> StatsSummary {
        var miles = 0.0
        var ascentMeters = 0.0
        var seconds = 0
        for hike in hikes {
            miles += hike.distanceMi
            seconds += hike.durationSeconds
            if let elev = elevationStats(path: hike.path) {
                ascentMeters += elev.totalAscentMeters
            }
        }
        let areasWithCompletion = progress.completions.filter { !$0.value.isEmpty }.count
        return StatsSummary(
            hikeCount: hikes.count,
            totalMiles: miles,
            totalAscentMeters: ascentMeters,
            totalSeconds: seconds,
            areasWithCompletion: areasWithCompletion
        )
    }

    /// Per-area engagement rows: every area with at least one completed
    /// trail OR at least one recorded hike. Hike-only areas show as
    /// "0 / N" — a brand-new area should appear from the very first
    /// hike (even a 1%-of-a-trail one), not stay invisible until a
    /// completion finally lands.
    private func areaCompletionRows() -> [AreaCompletionRowModel] {
        // touchedAreaIds so a WALK surfaces every area it credited, not
        // just the primary it filed under.
        var areaIds = Set(progress.completions.filter { !$0.value.isEmpty }.keys)
        areaIds.formUnion(hikes.flatMap { $0.touchedAreaIds })

        var rows: [AreaCompletionRowModel] = []
        for areaId in areaIds {
            guard let summary = areas.summaries.first(where: { $0.id == areaId }) else { continue }
            let trailCompletions = progress.completions[areaId] ?? [:]
            let total = summary.trailCount ?? 0
            // Most recent activity drives the sort — the latest
            // completion, or for completion-less areas the latest hike.
            // Engagement-ordered, not alphabetical.
            let mostRecent = (
                trailCompletions.values.compactMap(parseISODate)
                + hikes.filter { $0.touchedAreaIds.contains(areaId) }.map(\.startedAt)
            ).max() ?? .distantPast
            // Count the fingerprint-authoritative way (matching the checkmarks
            // and the Area page) when the area's geometry is loaded; the raw
            // completions count can include a stale trail id from a data update.
            // The .task below hydrates completed areas so this path is taken.
            let completed = areas.cachedArea(id: areaId)
                .map { progress.completionCount(in: areaId, trails: $0.trails) }
                ?? trailCompletions.count
            rows.append(AreaCompletionRowModel(
                id: areaId,
                name: summary.name,
                completed: completed,
                total: total,
                mostRecent: mostRecent
            ))
        }
        return rows.sorted { $0.mostRecent > $1.mostRecent }
    }

    /// Hoisted out of `parseISODate`, which allocated a fresh formatter on every
    /// call — once per completed trail, per area, per body evaluation.
    /// ISO8601DateFormatter is among the most expensive Foundation objects to
    /// construct; `date(from:)` is read-only so one shared instance is fine.
    private static let isoParser = ISO8601DateFormatter()

    private func parseISODate(_ s: String) -> Date? {
        Self.isoParser.date(from: s)
    }

    /// O(1) via the service's id index. This was `summaries.first { ... }` — a
    /// linear scan of ~29,850 rows, called twice per row inside `ForEach(hikes)`.
    private func areaName(for areaId: String) -> String {
        areas.summary(id: areaId)?.name ?? "Unknown area"
    }

    private func trailName(for hike: SavedRecording) -> String? {
        guard let trailId = hike.trailId,
              let area = areas.cachedArea(id: hike.areaId)
        else { return nil }
        return area.trails.first { $0.id == trailId }?.name
    }

    private func loadHikes() async {
        // Only show the spinner on the FIRST load. Flipping isLoading on a
        // refresh swaps the whole List out for a ProgressView and back, which
        // destroys the list and its scroll position.
        let firstLoad = hikes.isEmpty
        if firstLoad { isLoading = true }
        hikes = await recording.loadHistory()
        isLoading = false
    }

    private func deleteHikes(at indexSet: IndexSet) async {
        // Snapshot the ids and update the array BEFORE awaiting. `hikes` is
        // reassigned by loadHikes() from both .task and .refreshable, so a
        // refresh landing while this loop was suspended could shrink the array
        // and make the next hikes[i] — or remove(atOffsets:) with now-stale
        // offsets — trap.
        let ids = indexSet.compactMap { hikes.indices.contains($0) ? hikes[$0].id : nil }
        hikes.remove(atOffsets: indexSet)
        for id in ids {
            await recording.deleteRecording(id: id)
        }
    }
}

// MARK: - Summary card

/// Aggregate data shape consumed by StatsSummaryCard. Pure data; no
/// view layer dependency so it can be unit-tested separately.
struct StatsSummary: Equatable {
    let hikeCount: Int
    let totalMiles: Double
    let totalAscentMeters: Double
    let totalSeconds: Int
    let areasWithCompletion: Int

    /// Placeholder shown until the cached aggregate is first computed.
    static let empty = StatsSummary(
        hikeCount: 0, totalMiles: 0, totalAscentMeters: 0,
        totalSeconds: 0, areasWithCompletion: 0
    )
}

private struct StatsSummaryCard: View {
    let summary: StatsSummary

    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    /// Avg pace across all hikes — total distance over total time.
    /// Zero-guards so an empty-history or zero-duration aggregate
    /// renders the "—" sentinel rather than crashing.
    private var avgPaceMps: Double {
        guard summary.totalSeconds > 0 else { return 0 }
        return summary.totalMiles * 1609.344 / Double(summary.totalSeconds)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                stat(label: "Hikes", value: "\(summary.hikeCount)")
                stat(label: "Distance",
                     value: UnitFormatter.distanceValue(miles: summary.totalMiles, units: units),
                     unit: UnitFormatter.distanceSuffix(units: units))
            }
            HStack(spacing: 14) {
                stat(label: "Ascent",
                     value: UnitFormatter.elevationValue(meters: summary.totalAscentMeters, units: units),
                     unit: UnitFormatter.elevationSuffix(units: units))
                stat(label: "Time", value: hoursMinutes(seconds: summary.totalSeconds))
            }
            HStack(spacing: 14) {
                stat(label: "Avg Pace",
                     value: UnitFormatter.paceValue(metersPerSecond: avgPaceMps, units: units),
                     unit: UnitFormatter.paceSuffix(units: units))
            }
            if summary.areasWithCompletion > 0 {
                Text("^[\(summary.areasWithCompletion) area](inflect: true) with completed trails")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func stat(label: String, value: String, unit: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(value)
                    .font(.title2.weight(.semibold))
                    .monospacedDigit()
                if let unit {
                    Text(unit)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func hoursMinutes(seconds: Int) -> String {
        let h = seconds / 3600
        let m = (seconds % 3600) / 60
        if h > 0 { return "\(h)h \(m)m" }
        return "\(m)m"
    }
}

// MARK: - Area completion rows

private struct AreaCompletionRowModel: Identifiable, Equatable {
    let id: String
    let name: String
    let completed: Int
    let total: Int
    let mostRecent: Date
}

private struct AreaCompletionRow: View {
    let row: AreaCompletionRowModel

    var body: some View {
        NavigationLink {
            AreaView(areaId: row.id, areaName: row.name)
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(row.name)
                        .font(.body.weight(.medium))
                    if row.total > 0 {
                        ProgressView(value: Double(row.completed), total: Double(row.total))
                            .progressViewStyle(.linear)
                            .tint(.accentColor)
                            .frame(maxWidth: 120)
                    }
                }
                Spacer()
                if row.total > 0 {
                    Text("\(row.completed) / \(row.total)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                } else {
                    Text("\(row.completed) ✓")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
