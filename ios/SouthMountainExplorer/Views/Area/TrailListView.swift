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
    /// Free-text trail-name search. Filtered in `AreaView.computeFilteredTrails`
    /// alongside the other dimensions; this view owns the input UI.
    @Binding var searchQuery: String
    /// Pre-filtered trail set computed in AreaView so the map view can see
    /// the same set without TrailListView having to fan it back out.
    let filteredTrails: [Trail]
    var onRecordTrail: ((Trail) -> Void)? = nil

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording

    @FocusState private var searchFocused: Bool

    private var trimmedQuery: String {
        searchQuery.trimmingCharacters(in: .whitespaces)
    }

    private var activeFilterCount: Int {
        var n = 0
        if statusFilter != .all { n += 1 }
        if difficultyFilter != .all { n += 1 }
        if lengthFilter != .all { n += 1 }
        if routeFilter != .all { n += 1 }
        if !trimmedQuery.isEmpty { n += 1 }
        return n
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Trail-name search + filter menu. The summary header
            // (trail count, completion ratio) used to live above this
            // row but moved to AreaView's sheet header so the area
            // name + summary read as one block. The filter menu pairs
            // naturally with the search field, the way iOS settings
            // / mail toolbars do.
            HStack(spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                    TextField("Search trails", text: $searchQuery)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($searchFocused)
                        .submitLabel(.search)
                    if !searchQuery.isEmpty {
                        Button {
                            searchQuery = ""
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(.quaternary.opacity(0.5))
                )

                filterMenu
            }
            .padding(.horizontal)
            .padding(.top, 8)
            .padding(.bottom, 10)

            // "Showing X of Y" hint — only when a filter is active.
            // Visible feedback that the list is narrowed; the rest of
            // the summary info (trail count / completion) lives in
            // the sheet header above.
            if activeFilterCount > 0 {
                Text("Showing \(filteredTrails.count) of \(area.trails.count)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal)
                    .padding(.bottom, 6)
            }

            Divider()

            // ScrollViewReader so we can scroll the just-selected
            // trail's row into view when the user taps a trail on
            // the map. Without this, selecting a trail far down the
            // alphabetical list left the row off-screen and the
            // selection was effectively invisible.
            ScrollViewReader { proxy in
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
                                if !trimmedQuery.isEmpty && activeFilterCount == 1 {
                                    // Pure-search miss: spell out the
                                    // query so the user immediately
                                    // sees what they typed.
                                    Text("No trails named \u{201C}\(trimmedQuery)\u{201D}.")
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                        .multilineTextAlignment(.center)
                                        .padding(.horizontal, 24)
                                } else {
                                    Text("No trails match these filters.")
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                }
                                Button("Clear filters") {
                                    statusFilter = .all
                                    difficultyFilter = .all
                                    lengthFilter = .all
                                    routeFilter = .all
                                    searchQuery = ""
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
                            // Tag each row with the trail id so
                            // ScrollViewReader can target it via
                            // proxy.scrollTo when selection changes.
                            .id(trail.id)
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
            .onChange(of: selectedTrailId) { _, newId in
                guard let newId else { return }
                // Animate the row into view. LazyVStack only realizes
                // rows that are on-screen, so scrollTo must trigger
                // both the scroll AND lazy-row materialization.
                withAnimation(.easeInOut(duration: 0.25)) {
                    proxy.scrollTo(newId, anchor: .center)
                }
            }
            .onAppear {
                // Opened from a Browse trail-search result: AreaView sets
                // selectedTrailId BEFORE this list mounts, so .onChange above
                // never fires for it. Scroll the pre-selected row into view on
                // appear, deferred one hop so the LazyVStack has laid out
                // enough to resolve the target id.
                guard let tid = selectedTrailId else { return }
                Task {
                    await Task.yield()
                    withAnimation(.easeInOut(duration: 0.25)) {
                        proxy.scrollTo(tid, anchor: .center)
                    }
                }
            }
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
    /// Called when the trailing "Record" button (shown only while this row is
    /// selected) is tapped — starts recording this trail.
    var onRecordTrail: ((Trail) -> Void)? = nil

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    private var isComplete: Bool { progress.isComplete(areaId: areaId, trailId: trail.id) }
    private var coveragePct: Double { coverage.trailCoverage(areaId: areaId, trailId: trail.id) }
    private var isRecordingThis: Bool { recording.activeRecording?.trailId == trail.id }
    private var isSelected: Bool { selectedTrailId == trail.id }

    var body: some View {
        HStack(spacing: 14) {
            // The trail's own shape, stroked in its difficulty color
            // (cyan once completed — same color language as the map's cyan
            // completed stroke). Replaced the leaf/arrow/bolt difficulty
            // glyphs; difficulty stays readable via the colored text in the
            // caption row.
            TrailShapeThumb(
                trail: trail,
                color: isComplete ? .completedTrail : difficultyColor
            )

            VStack(alignment: .leading, spacing: 2) {
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
                    Label(UnitFormatter.distance(miles: trail.distanceMi, units: units), systemImage: "figure.walk")
                    if let gain = trail.gainFt, gain > 0 {
                        Text("·")
                        Label(UnitFormatter.elevation(feet: Double(gain), units: units),
                              systemImage: "arrow.up.forward")
                    }
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

            // Trailing control. Normally the completion checkmark (tap to
            // mark done). The moment this row is SELECTED it becomes a Record
            // button — tap a trail in the list, then hit Record right there,
            // no popup. Re-tapping the row deselects and the checkmark returns.
            Button {
                if isSelected {
                    onRecordTrail?(trail)
                } else {
                    Task { await progress.toggleTrail(areaId: areaId, trailId: trail.id) }
                }
            } label: {
                if isSelected {
                    Label(isRecordingThis ? "Recording" : "Record",
                          systemImage: "record.circle")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(Capsule().fill(isRecordingThis ? Color.red : Color.accentColor))
                } else {
                    // Outlined checkmark hints the action; fills in cyan when complete.
                    // Wrap both branches in AnyShapeStyle so the ternary has a single
                    // type — .cyan is a Color, .tertiary is a HierarchicalShapeStyle,
                    // and Swift can't unify them otherwise.
                    Image(systemName: isComplete ? "checkmark.circle.fill" : "checkmark.circle")
                        .font(.title3)
                        .foregroundStyle(isComplete ? AnyShapeStyle(Color.cyan) : AnyShapeStyle(.tertiary))
                }
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal)
        .padding(.vertical, 9)
        .background(isSelected ? Color.accentColor.opacity(0.15) : Color.clear)
        .contentShape(Rectangle())
        .onTapGesture {
            // Tap only highlights the polyline on the map (no popup) — so you
            // can click around trails freely. It also toggles selection, which
            // swaps this row's trailing control to a Record button; tapping the
            // same row again deselects and restores the checkmark.
            ActivityLogService.shared.log(
                category: "trail",
                action: "tap",
                context: ["areaId": areaId, "trailId": trail.id]
            )
            selectedTrailId = (selectedTrailId == trail.id) ? nil : trail.id
        }
    }

    private var difficultyColor: Color {
        switch trail.difficulty {
        case .easy: return .green
        case .moderate: return .orange
        case .hard: return .red
        }
    }

}

/// Mini rendering of a single trail's geometry — the row's leading
/// icon. Same equirectangular projection as the area silhouettes,
/// decimated to keep list scrolling cheap on long trails (a few
/// thousand points drawn per row would be wasted at 36 pt).
private struct TrailShapeThumb: View {
    let trail: Trail
    let color: Color

    var body: some View {
        Canvas { context, size in
            var minLat = Double.greatestFiniteMagnitude
            var maxLat = -Double.greatestFiniteMagnitude
            var minLon = Double.greatestFiniteMagnitude
            var maxLon = -Double.greatestFiniteMagnitude
            for segment in trail.segments {
                for pt in segment where pt.count >= 2 {
                    minLat = min(minLat, pt[0]); maxLat = max(maxLat, pt[0])
                    minLon = min(minLon, pt[1]); maxLon = max(maxLon, pt[1])
                }
            }
            guard minLat <= maxLat, minLon <= maxLon else { return }

            let pad: CGFloat = 3
            let drawW = size.width - 2 * pad
            let drawH = size.height - 2 * pad
            guard drawW > 0, drawH > 0 else { return }
            let centerLat = (minLat + maxLat) / 2
            let lonScale = cos(centerLat * .pi / 180)
            let xRange = max((maxLon - minLon) * lonScale, .leastNonzeroMagnitude)
            let yRange = max(maxLat - minLat, .leastNonzeroMagnitude)
            let scale = min(drawW / xRange, drawH / yRange)
            let canvasW = xRange * scale
            let canvasH = yRange * scale
            let xOffset = pad + (drawW - canvasW) / 2
            let yOffset = pad + (drawH - canvasH) / 2

            for segment in trail.segments {
                guard segment.count >= 2 else { continue }
                // ~60 points is plenty of shape at 36 pt.
                let stride = max(1, segment.count / 60)
                var path = Path()
                var first = true
                var i = 0
                while i < segment.count {
                    let pt = segment[i]
                    if pt.count >= 2 {
                        let x = xOffset + (pt[1] - minLon) * lonScale * scale
                        let y = yOffset + canvasH - (pt[0] - minLat) * scale
                        let p = CGPoint(x: x, y: y)
                        if first { path.move(to: p); first = false } else { path.addLine(to: p) }
                    }
                    // Always include the last point so the line reaches
                    // the trail's true end.
                    i = (i + stride > segment.count - 1 && i < segment.count - 1)
                        ? segment.count - 1
                        : i + stride
                }
                context.stroke(
                    path,
                    with: .color(color),
                    style: StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round)
                )
            }
        }
        .frame(width: 36, height: 36)
    }
}
