import SwiftUI

enum TrailStatusFilter: String, CaseIterable, Identifiable {
    case all, incomplete, complete
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all: return "All"
        case .incomplete: return "Incomplete"
        case .complete: return "Complete"
        }
    }
}

enum TrailDifficultyFilter: String, CaseIterable, Identifiable {
    case all, easy, moderate, hard
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all: return "All"
        case .easy: return "Easy"
        case .moderate: return "Moderate"
        case .hard: return "Hard"
        }
    }
    func matches(_ d: Difficulty) -> Bool {
        switch self {
        case .all: return true
        case .easy: return d == .easy
        case .moderate: return d == .moderate
        case .hard: return d == .hard
        }
    }
}

enum TrailLengthFilter: String, CaseIterable, Identifiable {
    case all, short, medium, long
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all: return "All"
        case .short: return "< 1 mi"
        case .medium: return "1–3 mi"
        case .long: return "> 3 mi"
        }
    }
    func matches(_ mi: Double) -> Bool {
        switch self {
        case .all: return true
        case .short: return mi < 1
        case .medium: return (1..<3).contains(mi)
        case .long: return mi >= 3
        }
    }
}

enum TrailRouteFilter: String, CaseIterable, Identifiable {
    case all, loop, linear
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all:    return "All"
        case .loop:   return "Loop"
        case .linear: return "Linear"
        }
    }
    func matches(_ t: RouteType) -> Bool {
        switch self {
        case .all:    return true
        case .loop:   return t == .loop
        case .linear: return t == .linear
        }
    }
}

struct TrailListView: View {
    let area: Area
    @Binding var selectedTrailId: String?
    @Binding var statusFilter: TrailStatusFilter
    @Binding var difficultyFilter: TrailDifficultyFilter
    @Binding var lengthFilter: TrailLengthFilter
    @Binding var routeFilter: TrailRouteFilter
    /// Pre-filtered trail set computed in AreaView so the map view can see
    /// the same set without TrailListView having to fan it back out.
    let filteredTrails: [Trail]
    var onRecordTrail: ((Trail) -> Void)? = nil

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording

    private var activeFilterCount: Int {
        var n = 0
        if statusFilter != .all { n += 1 }
        if difficultyFilter != .all { n += 1 }
        if lengthFilter != .all { n += 1 }
        if routeFilter != .all { n += 1 }
        return n
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Summary header
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(area.resolvedTrailCount) trails · \(String(format: "%.1f", area.resolvedTotalMi)) mi total")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)

                    // Filter to the area's current trail IDs so orphan
                    // completions (from before the trail-id determinism fix)
                    // don't double-count.
                    let completed = progress.completionCount(in: area.id, validTrailIds: Set(area.trails.map(\.id)))
                    if area.resolvedTrailCount > 0 {
                        Text("\(completed) of \(area.resolvedTrailCount) completed")
                            .font(.caption)
                            .foregroundStyle(completed == area.resolvedTrailCount ? .green : .secondary)
                    }

                    if activeFilterCount > 0 {
                        Text("Showing \(filteredTrails.count) of \(area.trails.count)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }

                    // Discoverability nudge for the long-press → "Record
                    // This Trail" context menu. Hidden mid-recording since
                    // the action wouldn't be available anyway.
                    if recording.activeRecording == nil {
                        Label("Tap to highlight on the map · long-press to record just that trail", systemImage: "hand.tap")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                            .padding(.top, 2)
                    }
                }
                Spacer()
                filterMenu
            }
            .padding(.horizontal)
            .padding(.vertical, 12)

            Divider()

            ScrollView {
                LazyVStack(spacing: 0) {
                    if filteredTrails.isEmpty {
                        VStack(spacing: 6) {
                            Image(systemName: activeFilterCount > 0
                                  ? "line.3.horizontal.decrease.circle"
                                  : "exclamationmark.triangle")
                                .font(.title2)
                                .foregroundStyle(.tertiary)
                            if activeFilterCount > 0 {
                                Text("No trails match these filters.")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                Button("Clear filters") {
                                    statusFilter = .all
                                    difficultyFilter = .all
                                    lengthFilter = .all
                                    routeFilter = .all
                                }
                                .font(.caption)
                            } else {
                                // Reached when the area's trail data fetch
                                // came back empty (rare — usually an Overpass
                                // hiccup). The defensive guard in
                                // AreaDataService prevents overwriting good
                                // cached data with empty, so this state
                                // should self-resolve on next open.
                                Text("Trail data didn't load.")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                Text("Try closing and reopening this area, or use Settings → Refresh Trail Data.")
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                                    .multilineTextAlignment(.center)
                                    .padding(.horizontal, 24)
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 40)
                    } else {
                        ForEach(filteredTrails) { trail in
                            TrailRow(
                                trail: trail,
                                areaId: area.id,
                                selectedTrailId: $selectedTrailId,
                                onRecordTrail: onRecordTrail
                            )
                            Divider().padding(.leading)
                        }
                    }
                }
                // The parent panel uses .ignoresSafeArea(.bottom) so it
                // can extend flush to the screen edge. That means the
                // last row would land in the home-indicator zone without
                // padding to push it back up. ~50pt covers the home
                // indicator area on every modern iPhone with margin.
                .padding(.bottom, 50)
            }
        }
    }

    private var filterMenu: some View {
        Menu {
            // Nested submenus give each dimension a tappable, labelled
            // entry point ("Status ▸") in the parent menu, so the user
            // can tell what they're toggling instead of seeing three
            // identical "All" rows stacked. Selected option is shown
            // inline next to the submenu label by SwiftUI when the
            // Picker has a single selection.
            Menu {
                Picker("Status", selection: $statusFilter) {
                    ForEach(TrailStatusFilter.allCases) { f in
                        Text(f.label).tag(f)
                    }
                }
            } label: {
                Label("Status: \(statusFilter.label)", systemImage: "checkmark.circle")
            }
            Menu {
                Picker("Difficulty", selection: $difficultyFilter) {
                    ForEach(TrailDifficultyFilter.allCases) { f in
                        Text(f.label).tag(f)
                    }
                }
            } label: {
                Label("Difficulty: \(difficultyFilter.label)", systemImage: "figure.hiking")
            }
            Menu {
                Picker("Length", selection: $lengthFilter) {
                    ForEach(TrailLengthFilter.allCases) { f in
                        Text(f.label).tag(f)
                    }
                }
            } label: {
                Label("Length: \(lengthFilter.label)", systemImage: "ruler")
            }
            Menu {
                Picker("Route", selection: $routeFilter) {
                    ForEach(TrailRouteFilter.allCases) { f in
                        Text(f.label).tag(f)
                    }
                }
            } label: {
                Label("Route: \(routeFilter.label)", systemImage: "arrow.triangle.2.circlepath")
            }
            if activeFilterCount > 0 {
                Divider()
                Button(role: .destructive) {
                    statusFilter = .all
                    difficultyFilter = .all
                    lengthFilter = .all
                    routeFilter = .all
                } label: {
                    Label("Clear All", systemImage: "xmark.circle")
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: activeFilterCount > 0
                      ? "line.3.horizontal.decrease.circle.fill"
                      : "line.3.horizontal.decrease.circle")
                if activeFilterCount > 0 {
                    Text("\(activeFilterCount)")
                        .font(.caption.weight(.semibold))
                }
            }
            .font(.title3)
            .foregroundStyle(activeFilterCount > 0 ? Color.accentColor : .secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
        }
        .accessibilityLabel("Filter trails")
    }
}

struct TrailRow: View {
    let trail: Trail
    let areaId: String
    @Binding var selectedTrailId: String?
    var onRecordTrail: ((Trail) -> Void)? = nil

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording

    private var isComplete: Bool { progress.isComplete(areaId: areaId, trailId: trail.id) }
    private var coveragePct: Double { coverage.trailCoverage(areaId: areaId, trailId: trail.id) }
    private var isRecordingThis: Bool { recording.activeRecording?.trailId == trail.id }
    private var isSelected: Bool { selectedTrailId == trail.id }

    var body: some View {
        HStack(spacing: 14) {
            // Difficulty / completion indicator
            ZStack {
                Circle()
                    .fill(isComplete ? Color.cyan : Color(.systemFill))
                    .frame(width: 32, height: 32)
                Image(systemName: isComplete ? "checkmark" : difficultyIcon)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(isComplete ? .white : difficultyColor)
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(trail.name)
                        .font(.body)
                        .fontWeight(isRecordingThis ? .semibold : .regular)
                    if isRecordingThis {
                        Image(systemName: "record.circle.fill")
                            .foregroundStyle(.red)
                            .symbolEffect(.pulse)
                    }
                }

                HStack(spacing: 8) {
                    Label(String(format: "%.1f mi", trail.distanceMi), systemImage: "figure.walk")
                    Text("·")
                    Text(trail.difficulty.rawValue)
                        .foregroundStyle(difficultyColor)
                    Text("·")
                    Label(trail.routeType.label, systemImage: trail.routeType.systemImage)
                }
                .font(.caption)
                .foregroundStyle(.secondary)

                if coveragePct > 0.02 && !isComplete {
                    ProgressView(value: coveragePct)
                        .tint(difficultyColor)
                        .frame(maxWidth: 120)
                }
            }

            Spacer()

            Button {
                Task { await progress.toggleTrail(areaId: areaId, trailId: trail.id) }
            } label: {
                // Outlined checkmark hints the action; fills in cyan when complete.
                // Wrap both branches in AnyShapeStyle so the ternary has a single
                // type — .cyan is a Color, .tertiary is a HierarchicalShapeStyle,
                // and Swift can't unify them otherwise.
                Image(systemName: isComplete ? "checkmark.circle.fill" : "checkmark.circle")
                    .font(.title3)
                    .foregroundStyle(isComplete ? AnyShapeStyle(Color.cyan) : AnyShapeStyle(.tertiary))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal)
        .padding(.vertical, 12)
        .background(isSelected ? Color.accentColor.opacity(0.15) : Color.clear)
        .contentShape(Rectangle())
        .onTapGesture {
            selectedTrailId = isSelected ? nil : trail.id
        }
        .contextMenu {
            // "Record This Trail" appears only when no recording is in
            // flight; AreaView's separate conflict guard handles the
            // bottom-bar Record Hike button. Recording in trail mode
            // labels the saved hike with this trail's name in History
            // and lights it up on the map as a dashed cyan guide line.
            if recording.activeRecording == nil, let onRecordTrail {
                Button {
                    onRecordTrail(trail)
                } label: {
                    Label("Record This Trail", systemImage: "record.circle")
                }
            }
        }
    }

    private var difficultyColor: Color {
        switch trail.difficulty {
        case .easy: return .green
        case .moderate: return .orange
        case .hard: return .red
        }
    }

    private var difficultyIcon: String {
        switch trail.difficulty {
        case .easy: return "leaf"
        case .moderate: return "arrow.up.right"
        case .hard: return "bolt.fill"
        }
    }
}
