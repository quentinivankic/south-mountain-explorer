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

/// Order of the trail list. There was no sort at all before this — trails
/// rendered in whatever order the publisher stored them, which makes "which one
/// should I do next" hard to answer in a park with 77 of them.
enum TrailSort: String, CaseIterable, Identifiable {
    case standard, nearest, shortest, longest, progress, alphabetical
    var id: String { rawValue }
    var label: String {
        switch self {
        case .standard:     return "Default"
        case .nearest:      return "Nearest to me"
        case .shortest:     return "Shortest"
        case .longest:      return "Longest"
        case .progress:     return "Most progress"
        case .alphabetical: return "A–Z"
        }
    }
    var systemImage: String {
        switch self {
        case .standard:     return "list.bullet"
        case .nearest:      return "location"
        case .shortest:     return "arrow.down.right.and.arrow.up.left"
        case .longest:      return "arrow.up.left.and.arrow.down.right"
        case .progress:     return "chart.bar"
        case .alphabetical: return "textformat.abc"
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
    @Binding var sort: TrailSort
    /// Free-text trail-name search. Filtered in `AreaView.computeFilteredTrails`
    /// alongside the other dimensions; this view owns the input UI.
    @Binding var searchQuery: String
    /// Pre-filtered trail set computed in AreaView so the map view can see
    /// the same set without TrailListView having to fan it back out.
    let filteredTrails: [Trail]
    var onRecordTrail: ((Trail) -> Void)? = nil

    // Height reports for AreaView's smallest-sheet-stop arithmetic. Measured
    // rather than derived from font metrics, so the stop stays right at any
    // Dynamic Type size.
    //
    // `onChromeHeight` replaces the old `onSearchBarHeight`: it reports the
    // WHOLE block above the rows — search field, filter hint, divider — as one
    // composed measurement. The old name measured only the search field, so the
    // filter hint was silently absent from the stop's arithmetic and any active
    // filter made the page taller than the sheet believed it was.
    var onChromeHeight: ((CGFloat) -> Void)? = nil
    var onCollapsedRowHeight: ((CGFloat) -> Void)? = nil
    var onSelectedRowHeight: ((CGFloat) -> Void)? = nil

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
        // THE CHROME IS FIXED AND ONLY THE ROWS SCROLL, and that split is the
        // whole point of this layout.
        //
        // Both previous arrangements failed, in opposite directions:
        //
        //   Everything FIXED   - the smallest sheet stop is a SUM the layout has
        //                        to keep exactly, and every term missing from it
        //                        is something visibly squeezed out. Three terms
        //                        were missing before anyone counted.
        //   Everything SCROLLS - the search field becomes scroll CONTENT, so any
        //                        stale offset scrolls it out of sight and leaves
        //                        the cut edge partway through the first row.
        //                        That is the reported screenshot exactly: a
        //                        sheet sized to include a search field that has
        //                        been scrolled away, with a trail name sliced in
        //                        half sitting where it should be.
        //
        // Fixed chrome over a scrolling list has neither failure. The sum is now
        // only `header + chrome`, both measured as whole composed blocks, and
        // the scroll view underneath is greedy — it absorbs every shortfall by
        // scrolling rather than by clipping. The search field sits ABOVE the
        // scroll view's top edge, where no content offset can reach it.
        ScrollViewReader { proxy in
            VStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 0) {
                    // The search field NEVER stands down.
                    //
                    // It used to hide at the smallest stop while a trail was
                    // selected, to buy that stop ~44pt. Two costs, both paid:
                    // the block above the rows had two different heights that
                    // one measurement had to carry, and the user's own reading
                    // of it was "is the search bar straight up going away?".
                    // A control that vanishes is not a saving.
                    // Always present means one height, one measurement, and
                    // nothing above the rows that can appear or disappear.
                    VStack(alignment: .leading, spacing: 0) {
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
                    }
                    if activeFilterCount > 0 {
                        HStack(spacing: 8) {
                            Text("Showing \(filteredTrails.count) of \(area.trails.count)")
                            Button("Clear") {
                                statusFilter = .all
                                difficultyFilter = .all
                                lengthFilter = .all
                                routeFilter = .all
                                searchQuery = ""
                            }
                            .font(.caption2.weight(.semibold))
                            Spacer(minLength: 0)
                        }
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal)
                        .padding(.vertical, 5)
                    }

                    Divider()
                }
                // Measured as ONE COMPOSED BLOCK — search field, filter hint
                // and rule together — never summed from parts.
                //
                // `fixedSize` comes BEFORE the measurement, because modifiers
                // apply bottom-up: measuring first would report the height this
                // block was SQUEEZED into, and that height would then size the
                // sheet to keep it squeezed.
                .fixedSize(horizontal: false, vertical: true)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { onChromeHeight?($0) }

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
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
                        ForEach(Array(filteredTrails.enumerated()), id: \.element.id) { index, trail in
                            TrailRow(
                                trail: trail,
                                areaId: area.id,
                                areaParking: area.parking,
                                selectedTrailId: $selectedTrailId,
                                onRecordTrail: onRecordTrail
                            )
                            // Tag each row with the trail id so
                            // ScrollViewReader can target it via
                            // proxy.scrollTo when selection changes.
                            .id(trail.id)
                            // Feed AreaView the two row heights its smallest
                            // sheet stop is sized from. The collapsed reference
                            // comes from row 0, or row 1 when row 0 is the one
                            // that's expanded.
                            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { h in
                                if trail.id == selectedTrailId {
                                    onSelectedRowHeight?(h)
                                } else if index == 0 {
                                    // Row 0 ONLY. This was `index <= 1`, so two
                                    // rows both reported — and rows are not all
                                    // the same height, since only some carry a
                                    // progress bar. The value flip-flopped
                                    // between them on every re-layout, moving
                                    // the sheet's smallest stop with it.
                                    onCollapsedRowHeight?(h)
                                }
                            }
                            Divider().padding(.leading)
                        }
                    }
                    }
                }
                // ON THE SCROLL VIEW, which is what stops UIKit adding the
                // home-indicator strip as a bottom CONTENT inset. It was on the
                // LazyVStack — the scroll view's CONTENT — where it did nothing
                // about the scroll view's own inset.
                .ignoresSafeArea(edges: .bottom)
                .onChange(of: selectedTrailId) { _, newId in
                    guard let newId else {
                        // DESELECT. The row that was open just shrank by the
                        // height of its chart and parking line, and the scroll
                        // offset does not shrink with it — so the list is left
                        // parked partway through a row, which reads as a trail
                        // name sliced in half at the top of the list.
                        // Back to the top of the LIST. The chrome is no longer
                        // inside this scroll view, so there is nothing above the
                        // first row to return to — and nothing a stale offset
                        // could hide, which is what this used to be fixing.
                        if let firstId = filteredTrails.first?.id {
                            Task {
                                await Task.yield()
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    proxy.scrollTo(firstId, anchor: .top)
                                }
                            }
                        }
                        return
                    }
                    // Animate the row into view. LazyVStack only realizes
                    // rows that are on-screen, so scrollTo must trigger
                    // both the scroll AND lazy-row materialization.
                    withAnimation(.easeInOut(duration: 0.25)) {
                        proxy.scrollTo(newId, anchor: .top)
                    }
                    // Then again once the layout has settled. Selecting a row makes
                    // it TALLER (the chart and parking line expand into it) and the
                    // panel changes height underneath at the same moment, so the
                    // offset computed a moment ago is stale by the time both land —
                    // which is how the selected row ended up with its title tucked
                    // under the page dots. Same deferred-one-hop trick the deep-link
                    // path below already uses.
                    Task {
                        await Task.yield()
                        withAnimation(.easeInOut(duration: 0.2)) {
                            proxy.scrollTo(newId, anchor: .top)
                        }
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
                            proxy.scrollTo(tid, anchor: .top)
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
            Divider()
            // Sort lives in the same menu as the filters, so ordering the list
            // costs no vertical space in the sheet.
            Menu {
                Picker("Sort", selection: $sort) {
                    ForEach(TrailSort.allCases) { s in
                        Label(s.label, systemImage: s.systemImage).tag(s)
                    }
                }
            } label: {
                Label("Sort: \(sort.label)", systemImage: "arrow.up.arrow.down")
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
    /// The area's parking lots, so the selected row can state where to park —
    /// especially when the nearest lot is far and its map pin sits off-screen.
    var areaParking: [ParkingLot]? = nil
    @Binding var selectedTrailId: String?
    /// Called when the trailing "Record" button (shown only while this row is
    /// selected) is tapped — starts recording this trail.
    var onRecordTrail: ((Trail) -> Void)? = nil

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording
    @Environment(LocationService.self) private var location
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    /// Chart orientation, LATCHED when the profile opens.
    ///
    /// It has to be latched rather than computed live: "which end is nearer"
    /// inverts at the trail's midpoint, so recomputing would mirror the chart
    /// mid-hike on every point-to-point trail. Fixing it at open keeps the
    /// picture still while you walk across it — the marker moves, the trail
    /// doesn't.
    @State private var profileStartIsNearer = true

    private var isComplete: Bool { progress.isComplete(trail, areaId: areaId) }

    /// Trailing control glyph. Checkmark normally; a record light once the row
    /// is selected, so the control keeps its size and the row never reflows.
    private var recordControlSymbol: String {
        if isRecordingThis { return "record.circle.fill" }
        if isSelected { return "record.circle" }
        return isComplete ? "checkmark.circle.fill" : "checkmark.circle"
    }

    private var recordControlStyle: AnyShapeStyle {
        if isRecordingThis { return AnyShapeStyle(Color.red) }
        if isSelected { return AnyShapeStyle(Color.accentColor) }
        return isComplete ? AnyShapeStyle(Color.cyan) : AnyShapeStyle(.tertiary)
    }

    /// Where to park for this trail, phrased for the expanded row. nil when the
    /// area has no parking at all. Crucially STATES the distance for a far
    /// fallback lot — whose map pin sits off-screen because the camera frames
    /// the trail — so an empty-looking map doesn't read as "no parking here".
    private var nearestParkingInfo: (text: String, isNear: Bool)? {
        // The area's own lots PLUS the global pool — a lot 358 m outside a
        // wilderness is useful to the hiker whether or not the pipeline decided
        // that wilderness "owns" it. Same merge the map pins use.
        let pk = Area.nearestParkingWithFallback(
            lots: ParkingPoolService.shared.merged(with: areaParking, for: trail),
            for: trail)
        guard let top = pk.first else { return nil }
        if top.isNear { return ("Parking at the trailhead", true) }
        let dist = UnitFormatter.distance(meters: top.meters, units: units)
        let name = top.lot.name.map { "\($0), " } ?? ""
        return ("Nearest parking: \(name)\(dist) away", false)
    }
    private var coveragePct: Double { coverage.trailCoverage(areaId: areaId, trailId: trail.id) }
    private var isRecordingThis: Bool { recording.activeRecording?.trailId == trail.id }
    private var isSelected: Bool { selectedTrailId == trail.id }

    /// Where the hiker sits on THIS trail, 0…1, or nil when they aren't on it.
    ///
    /// Only computed for the selected row — snapping walks the whole polyline,
    /// and doing that for every row in a 600-trail area on each render would be
    /// wasteful for a chart nobody is looking at.
    private var snappedFraction: Double? {
        guard isSelected, trail.profileFt != nil,
              let here = location.liveLocation ?? location.userLocation,
              let snap = TrailProfile.snap(lat: here.latitude, lon: here.longitude,
                                           segments: trail.segments),
              snap.offTrailMeters <= Self.onTrailMeters
        else { return nil }
        return snap.fraction
    }

    /// How close counts as "on this trail" for drawing the marker. Deliberately
    /// looser than coverage's 10 m: coverage decides whether you WALKED a
    /// segment and must be strict, while this only decides whether to draw a
    /// dot — and a dot that blinks out on ordinary GPS wobble is worse than one
    /// sitting a few metres off.
    private static let onTrailMeters = 50.0

    /// Orientation from the nearest car park: is the stored START the end you
    /// would set off from? nil when no lot is close enough to an endpoint, or
    /// when the lot is no closer to one end than the other.
    ///
    /// Ranked ABOVE the user's own position because where you are standing is
    /// only a proxy for where you will set off from, and the car park is the
    /// thing itself. Browsing from home the proxy is near enough a coin flip —
    /// Mormon Trail opened from its southeast end, 1,588 m from the only lot,
    /// while its northwest end sits 32 m from that lot.
    ///
    /// Uses the same merged set as the pins and the parking line below: the
    /// area's own lots plus the global pool.
    private var startIsNearerParking: Bool? {
        TrailProfile.startIsNearerParking(
            segments: trail.segments,
            lots: ParkingPoolService.shared.merged(with: areaParking, for: trail)
        )
    }

    /// Orientation to latch when the profile opens: is the stored START the
    /// trail end nearer the user? nil when we have no location at all, in
    /// which case stored order stands and no direction is implied.
    private var startIsNearerNow: Bool? {
        guard let here = location.liveLocation ?? location.userLocation else { return nil }
        return TrailProfile.startIsNearer(lat: here.latitude, lon: here.longitude,
                                          segments: trail.segments)
    }

    /// VoiceOver can't read a chart, so state the shape in words: the range,
    /// and where the hiker sits in it when we know.
    private var profileAccessibilityLabel: String {
        guard let profile = trail.profileFt, let lo = profile.min(), let hi = profile.max()
        else { return "Elevation profile" }
        let range = "Elevation profile, "
            + UnitFormatter.elevation(feet: Double(lo), units: units)
            + " to " + UnitFormatter.elevation(feet: Double(hi), units: units)
        guard let f = snappedFraction,
              let here = TrailProfile.elevationFt(profile, at: f)
        else { return range }
        return range + ", you are at "
            + UnitFormatter.elevation(feet: here, units: units)
            + ", \(Int((f * 100).rounded())) percent along"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
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
                            // An up-and-down arrow, not an up arrow. `gainFt` is
                            // `max(ascent, descent)` over the trail — the climb
                            // in the HARDER direction — because OSM way order is
                            // arbitrary and a one-way "gain" would be a coin
                            // flip. Measured over a 400-area sample: 46.7% of
                            // trails with a profile fall further than they
                            // climb in stored order, and 26.8% of all of them
                            // had this badge claiming at least twice the climb
                            // the drawn direction actually has. Shaughnessey
                            // Connector is the example that surfaced it: 238 ft
                            // of pure descent, badged as a 220 ft ascent right
                            // above a chart that visibly only goes down.
                            Label(UnitFormatter.elevation(feet: Double(gain), units: units),
                                  systemImage: "arrow.up.and.down")
                                .accessibilityLabel(
                                    "\(UnitFormatter.elevation(feet: Double(gain), units: units)) of elevation change"
                                )
                        }
                        Text("·")
                        Text(trail.difficulty.rawValue)
                            .foregroundStyle(difficultyColor)
                        // Loop/Linear removed from the row: the trail's own
                        // shape thumbnail on the left already shows whether it
                        // closes on itself, so the label was restating the
                        // picture and crowding the caption line. Still available
                        // as a FILTER in the menu.
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
                    // SAME SIZE in both states — an icon, never a labelled
                    // capsule. Selecting a row used to swap the checkmark for a
                    // wide "Record" pill, which reflowed the whole row and
                    // shoved the trail name around on every selection. Now the
                    // checkmark simply becomes a record light: red and filled
                    // while recording this trail, accent-coloured when it's the
                    // selected trail and ready to start.
                    Image(systemName: recordControlSymbol)
                        .font(.title3)
                        .foregroundStyle(recordControlStyle)
                        .symbolEffect(.pulse, isActive: isRecordingThis)
                }
                .buttonStyle(.plain)
            }

        // The profile expands INTO the selected row rather than opening a
        // sheet, matching this screen's existing no-popup pattern (tap a
        // trail, hit Record right there). Areas published before the DEM
        // profile pass have no `profileFt`, so the row simply stays compact.
        if isSelected, let profile = trail.profileFt, profile.count >= 2 {
            TrailElevationProfileView(
                profileFt: profile,
                totalDistanceMi: trail.distanceMi,
                position: snappedFraction,
                startIsNearer: profileStartIsNearer,
                startEndLabel: TrailProfile.startEndCompassLabel(
                    segments: trail.segments,
                    startIsNearer: profileStartIsNearer
                ),
                profileGaps: trail.profileGaps ?? [],
                // Flipping writes the choice for THIS trail and redraws. The
                // automatic answer stays the default everywhere else — see
                // ProfileDirectionStore for why this is an override, not a
                // replacement.
                onFlip: {
                    let flipped = !profileStartIsNearer
                    profileStartIsNearer = flipped
                    ProfileDirectionStore.set(flipped, trailId: trail.id)
                    ActivityLogService.shared.log(
                        category: "trail",
                        action: "profile-flip",
                        context: ["areaId": areaId, "trailId": trail.id,
                                  "startIsNearer": String(flipped)]
                    )
                }
            )
            .frame(height: 96)
            .padding(.trailing, 4)
            .transition(.opacity.combined(with: .move(edge: .top)))
            .accessibilityLabel(profileAccessibilityLabel)
        }

        // Parking line for the selected trail. The map draws near lots as pins,
        // but a FAR fallback lot lands off-screen — so state it in words here.
        if isSelected, let pk = nearestParkingInfo {
            HStack(spacing: 6) {
                Image(systemName: pk.isNear ? "p.circle.fill" : "figure.walk.circle")
                    .font(.caption)
                    .foregroundStyle(pk.isNear ? Color.blue : Color.orange)
                Text(pk.text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            .padding(.top, 3)
            .transition(.opacity)
            .accessibilityLabel(pk.text)
        }
        }
        .padding(.horizontal)
        .padding(.vertical, 9)
        .animation(.easeInOut(duration: 0.2), value: isSelected)
        .onChange(of: isSelected, initial: true) { _, nowSelected in
            // Latch orientation at OPEN and never while it's up. "Which end is
            // nearer" inverts at the trail's midpoint, so live recomputation
            // would mirror the chart mid-hike on every point-to-point trail.
            guard nowSelected else { return }
            // A saved flip wins over the automatic answer — the user has told
            // us which end they set off from, and unlike "which end is nearer"
            // that doesn't change as they travel. Falls back to the latch when
            // they haven't chosen, and leaves the previous value alone when
            // there's no geometry to compute one from.
            if let saved = ProfileDirectionStore.override(trailId: trail.id) {
                profileStartIsNearer = saved
            } else if let byParking = startIsNearerParking {
                // The trailhead beats the sofa. See `startIsNearerParking`.
                profileStartIsNearer = byParking
            } else if let s = startIsNearerNow {
                profileStartIsNearer = s
            }
        }
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
