import SwiftUI
import Charts

/// Stats tab. Replaces the History tab — same data, augmented with
/// cumulative totals, a 12-month bar chart, and per-area completion
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

    @State private var hikes: [SavedRecording] = []
    @State private var isLoading = false

    /// CACHED derived data. These were computed inline in `statsList`, so every
    /// body evaluation re-ran them — and `aggregate` calls `elevationStats` for
    /// EVERY hike, walking each one's entire GPS path, while `areaCompletionRows`
    /// re-scanned history and re-counted completions per area. Recomputed only
    /// when the inputs actually change (see `refreshDerived`).
    @State private var summary: StatsSummary = .empty
    @State private var buckets: [MonthBucket] = []
    @State private var areaRows: [AreaCompletionRowModel] = []

    /// Recompute the cached values. Cheap relative to doing it per render.
    private func refreshDerived() {
        summary = aggregate(hikes: hikes)
        buckets = monthBuckets(hikes: hikes)
        areaRows = areaCompletionRows()
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
        List {
            Section {
                StatsSummaryCard(summary: summary)
                    .listRowInsets(.init(top: 8, leading: 16, bottom: 8, trailing: 16))
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }

            Section("Hikes per Month") {
                HikesPerMonthChart(buckets: buckets)
                    .frame(height: 160)
                    .padding(.vertical, 8)
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }

            Section {
                NavigationLink {
                    InsightsView(hikes: hikes)
                } label: {
                    Label("Insights", systemImage: "chart.bar.xaxis")
                }
                .accessibilityIdentifier("insights-link")
            } footer: {
                Text("Personal records, streaks, and yearly trends.")
            }

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
                        HikeDetailView(hike: hike, areaName: areaName(for: hike.areaId))
                    } label: {
                        HikeRow(
                            hike: hike,
                            areaName: areaName(for: hike.areaId),
                            trailName: trailName(for: hike)
                        )
                    }
                    .accessibilityIdentifier("hike-row-\(hike.id)")
                }
                .onDelete { indexSet in
                    Task { await deleteHikes(at: indexSet) }
                }
            }
        }
        .listStyle(.insetGrouped)
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

    /// Last 12 months including the current one, with hike-count + miles
    /// bucketed by start date. Buckets ordered oldest → newest for the
    /// Swift Charts X axis.
    private func monthBuckets(hikes: [SavedRecording]) -> [MonthBucket] {
        let cal = Calendar.current
        let now = Date()
        var buckets: [Date: MonthBucket] = [:]
        // Pre-seed twelve buckets so an empty month renders as a zero
        // bar instead of disappearing from the X axis — keeps the
        // chart's time axis consistent across renders.
        for i in 0..<12 {
            if let monthStart = cal.date(byAdding: .month, value: -i, to: now)
                .flatMap({ cal.dateInterval(of: .month, for: $0)?.start }) {
                buckets[monthStart] = MonthBucket(month: monthStart, hikeCount: 0, miles: 0)
            }
        }
        for hike in hikes {
            guard let monthStart = cal.dateInterval(of: .month, for: hike.startedAt)?.start,
                  buckets[monthStart] != nil else { continue }
            buckets[monthStart]?.hikeCount += 1
            buckets[monthStart]?.miles += hike.distanceMi
        }
        return buckets.values.sorted { $0.month < $1.month }
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
        areas.summary(id: areaId)?.name ?? areaId
    }

    private func trailName(for hike: SavedRecording) -> String? {
        guard let trailId = hike.trailId,
              let area = areas.cachedArea(id: hike.areaId)
        else { return nil }
        return area.trails.first { $0.id == trailId }?.name
    }

    private func loadHikes() async {
        isLoading = true
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

// MARK: - Hikes-per-month chart

private struct MonthBucket: Identifiable, Equatable {
    var id: Date { month }
    let month: Date
    var hikeCount: Int
    var miles: Double
}

private struct HikesPerMonthChart: View {
    let buckets: [MonthBucket]

    var body: some View {
        Chart {
            ForEach(buckets) { b in
                BarMark(
                    x: .value("Month", b.month, unit: .month),
                    y: .value("Hikes", b.hikeCount)
                )
                .foregroundStyle(Color.accentColor)
            }
        }
        .chartXAxis {
            AxisMarks(values: .stride(by: .month, count: 2)) { value in
                AxisValueLabel(format: .dateTime.month(.abbreviated), centered: true)
            }
        }
        .chartYAxis {
            AxisMarks(values: .automatic(desiredCount: 3))
        }
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
