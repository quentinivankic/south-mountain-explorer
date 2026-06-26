import SwiftUI

/// The "Dex" — a Pokédex-style achievement page for one area. Lives as
/// the second segment of the area sheet (Trails | Dex). Every badge is
/// derived on the fly by `AchievementEngine` from recorded hikes +
/// trail completions, so it fills in retroactively and needs no
/// storage of its own.
struct DexView: View {
    let area: Area

    @Environment(RecordingService.self) private var recording
    @Environment(ProgressService.self) private var progress
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var achievements: [Achievement] = []
    @State private var didLoad = false

    /// 3-up grid that adapts down on narrow devices.
    private let columns = [GridItem(.adaptive(minimum: 96), spacing: 12)]

    private var earnedCount: Int { achievements.filter(\.isEarned).count }

    var body: some View {
        ScrollView {
            if achievements.isEmpty && didLoad {
                // Should be rare — every area yields at least the
                // milestone + distance badges — but guard anyway.
                ContentUnavailableView(
                    "No Badges Yet",
                    systemImage: "rosette",
                    description: Text("Record a hike here to start earning badges."))
                    .padding(.top, 40)
            } else {
                summaryHeader
                ForEach(AchievementCategory.allCases, id: \.self) { category in
                    let items = achievements.filter { $0.category == category }
                    if !items.isEmpty {
                        section(title: category.rawValue, items: items)
                    }
                }
            }
        }
        .scrollIndicators(.hidden)
        // Reload when completions for THIS area change (e.g. the user
        // checks off a trail on the Trails tab then flips to the Dex).
        // History changes only after a recording finishes, which routes
        // through the summary sheet and re-appears this view, so the
        // initial `.task` covers that path.
        .task { await load() }
        .onChange(of: progress.completions[area.id]) { _, _ in
            Task { await load() }
        }
    }

    // MARK: - Header

    private var summaryHeader: some View {
        VStack(spacing: 6) {
            Image(systemName: earnedCount == achievements.count ? "rosette" : "trophy.fill")
                .font(.title2)
                .foregroundStyle(earnedCount == achievements.count ? .purple : .yellow)
            Text("\(earnedCount) of \(achievements.count) earned")
                .font(.headline)
            ProgressView(value: Double(earnedCount),
                         total: Double(max(achievements.count, 1)))
                .tint(.purple)
                .frame(maxWidth: 220)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .padding(.horizontal, 20)
    }

    // MARK: - Sections

    private func section(title: String, items: [Achievement]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 20)
            LazyVGrid(columns: columns, spacing: 14) {
                ForEach(items) { achievement in
                    DexBadgeCell(
                        achievement: achievement,
                        statusText: statusText(for: achievement))
                }
            }
            .padding(.horizontal, 16)
        }
        .padding(.bottom, 18)
    }

    // MARK: - Status text

    /// One short line under each badge: the earn date when earned,
    /// otherwise the progress readout (accumulating badges) or the
    /// requirement blurb (binary badges).
    private func statusText(for a: Achievement) -> String {
        if a.isEarned {
            if let date = a.earnedDate {
                return Self.dateFormatter.string(from: date)
            }
            return "Earned"
        }
        if let p = a.progress {
            return progressText(p)
        }
        // Binary, locked: distance badges carry no engine detail (their
        // requirement is unit-dependent), so fall back to the progress
        // readout above; everything else has a requirement blurb.
        return a.detail
    }

    private func progressText(_ p: AchievementProgress) -> String {
        switch p.metric {
        case .distanceMeters:
            let cur = UnitFormatter.distanceValue(
                miles: p.current / AchievementEngine.metersPerMile, units: units)
            let tgt = UnitFormatter.distanceValue(
                miles: p.target / AchievementEngine.metersPerMile, units: units)
            return "\(cur) / \(tgt) \(UnitFormatter.distanceSuffix(units: units))"
        case .count(let unit):
            return "\(Int(p.current)) / \(Int(p.target)) \(unit)"
        }
    }

    // MARK: - Load

    private func load() async {
        let history = await recording.loadHistory()
        let areaHikes = history.filter { $0.areaId == area.id }
        let completedMap = progress.completedTrails(in: area.id)
        let completedTrailIds = Set(completedMap.keys)
        var completionDates: [String: Date] = [:]
        for tid in completedTrailIds {
            if let d = progress.completionDate(areaId: area.id, trailId: tid) {
                completionDates[tid] = d
            }
        }
        achievements = AchievementEngine.evaluate(
            trails: area.trails,
            hikes: areaHikes,
            completedTrailIds: completedTrailIds,
            completionDates: completionDates)
        didLoad = true
    }

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f
    }()
}

/// A single Dex badge cell: circular face (tinted + symbol when earned,
/// desaturated + lock when not) over a title and one status line.
private struct DexBadgeCell: View {
    let achievement: Achievement
    let statusText: String

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                Circle()
                    .fill(achievement.isEarned
                          ? AnyShapeStyle(earnedGradient)
                          : AnyShapeStyle(Color(.tertiarySystemFill)))
                    .frame(width: 64, height: 64)
                Image(systemName: achievement.symbol)
                    .font(.system(size: 26))
                    .foregroundStyle(achievement.isEarned ? .white : Color(.tertiaryLabel))
                if !achievement.isEarned {
                    // Small lock chip, bottom-trailing, so a locked
                    // badge reads as "not yet" at a glance even when
                    // the symbol itself is recognizable.
                    Image(systemName: "lock.fill")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.secondary)
                        .padding(4)
                        .background(Color(.systemBackground), in: Circle())
                        .offset(x: 22, y: 22)
                }
            }
            // Slim progress arc substitute: a thin bar under in-progress
            // (locked but accumulating) badges so the grid telegraphs
            // "close to earning this" without opening a detail view.
            if !achievement.isEarned, let p = achievement.progress, p.fraction > 0 {
                ProgressView(value: p.fraction)
                    .tint(.purple)
                    .frame(width: 56)
            }
            Text(achievement.title)
                .font(.caption.weight(.semibold))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .foregroundStyle(achievement.isEarned ? .primary : .secondary)
            Text(statusText)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(achievement.title), \(achievement.isEarned ? "earned" : "locked"). \(statusText)")
    }

    private var earnedGradient: LinearGradient {
        LinearGradient(
            colors: [.purple, .indigo],
            startPoint: .topLeading,
            endPoint: .bottomTrailing)
    }
}
