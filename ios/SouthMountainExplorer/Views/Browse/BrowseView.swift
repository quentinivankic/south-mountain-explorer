import SwiftUI
import CoreLocation

extension Notification.Name {
    /// Posted by ContentView every time the user taps the Browse tab's
    /// tab-bar icon — including re-taps while the tab is already
    /// selected. BrowseView responds by focusing the search field so
    /// the keyboard opens immediately.
    static let browseSearchTabTapped = Notification.Name("summit.browseSearchTabTapped")

    /// Posted by the out-of-region WaitlistCard's "look around" button so
    /// an EU/etc. user can jump straight into the served parks list.
    /// ContentView responds by switching the root TabView to Browse.
    static let showBrowseTab = Notification.Name("summit.showBrowseTab")
}

private enum BrowseSort: String, CaseIterable, Identifiable {
    case alphabetic, nearest, mostTrails, longest

    var id: String { rawValue }
    var label: String {
        switch self {
        case .alphabetic: return "A → Z"
        case .nearest:    return "Nearest"
        case .mostTrails: return "Most trails"
        case .longest:    return "Total miles"
        }
    }
    var systemImage: String {
        switch self {
        case .alphabetic: return "textformat.abc"
        case .nearest:    return "location"
        case .mostTrails: return "map"
        case .longest:    return "ruler"
        }
    }
}

/// Drive-time bands estimated from straight-line distance. Real routing would
/// be a per-area MKDirections call (too expensive for a list filter), so we
/// approximate at ~1.3 min/mi — a mix of highway + local roads.
private enum BrowseDriveTime: String, CaseIterable, Identifiable {
    case any, t30, t60, t120

    var id: String { rawValue }
    var label: String {
        switch self {
        case .any:  return "Any distance"
        case .t30:  return "< 30 min"
        case .t60:  return "< 1 hr"
        case .t120: return "< 2 hr"
        }
    }
    /// Max straight-line miles that fit under this band, given our 1.3 min/mi
    /// estimate. nil means no cap.
    var maxMiles: Double? {
        switch self {
        case .any:  return nil
        case .t30:  return 30.0 / 1.3
        case .t60:  return 60.0 / 1.3
        case .t120: return 120.0 / 1.3
        }
    }
}

struct BrowseView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(FavoritesService.self) private var favorites
    @Environment(LocationService.self) private var location
    /// Global trail-name index — makes trail search cover EVERY trail, not just
    /// areas already loaded. Falls back to the local search when unloaded.
    @Environment(TrailSearchService.self) private var trailSearch

    @State private var query = ""
    @State private var selectedArea: AreaSummary? = nil
    /// Trail id to pre-select in the area sheet — set when the sheet is
    /// opened from a trail search result, nil when opened from an area
    /// row. Cleared on sheet dismiss.
    @State private var pendingTrailId: String? = nil
    @State private var sort: BrowseSort = .alphabetic
    @State private var driveTime: BrowseDriveTime = .any
    @FocusState private var searchFocused: Bool

    /// CACHED results of the sort/filter pipeline and the trail-name search.
    ///
    /// These used to be computed properties read by `body`, which meant the
    /// whole pipeline — including an alphabetical sort of ~29,850 areas using
    /// locale-aware compare — re-ran on EVERY body evaluation, i.e. on every
    /// keystroke and every unrelated state change, on the main thread. That is
    /// what made Browse feel sticky. Now they are plain state, recomputed only
    /// when an actual input changes (see `refreshResults`).
    @State private var results: [AreaSummary] = []
    @State private var trailResults: [AreaDataService.TrailSearchHit] = []
    /// areaId → area name, built ONCE per summaries load instead of once per
    /// keystroke (it was a 29,850-entry Dictionary rebuilt inside the search
    /// path). Rebuilt in `refreshResults` when the summaries count changes.
    @State private var areaNameCache: [String: String] = [:]
    @State private var areaNameCacheCount = -1

    /// Trail-name matches for the current query, across every area with
    /// a full payload available locally (favorites, prefetched nearby,
    /// previously opened — trail names don't exist in the lightweight
    /// index). Capped so a one-letter query doesn't dump thousands of
    /// rows above the area results.
    private func computeTrailResults() -> [AreaDataService.TrailSearchHit] {
        guard !query.isEmpty else { return [] }
        let q = query.lowercased()
        // Global index (every trail) when loaded; else fall back to the local
        // loaded-areas search, so search never regresses below today's.
        let global = trailSearch.search(query, limit: 25)
        if !global.isEmpty {
            // areaNameCache is built once per summaries load, NOT per keystroke.
            return global.compactMap { e in
                guard let areaName = areaNameCache[e.areaId] else { return nil }
                return AreaDataService.TrailSearchHit(
                    trailId: e.trailId, trailName: e.trailName, searchKey: e.searchKey,
                    difficulty: e.difficulty, distanceMi: e.distanceMi,
                    areaId: e.areaId, areaName: areaName)
            }
        }
        return Array(areas.trailSearchHits().filter { $0.searchKey.contains(q) }.prefix(25))
    }

    /// Sort + filter pipeline. Search happens first (cheap string match), then
    /// drive-time cap (silently skipped when we don't have a user location —
    /// hiding everything would be more surprising than showing the unfiltered
    /// list), then sort. Nearest falls back to alphabetic when location is
    /// unavailable rather than producing an arbitrary order.
    private func computeResults() -> [AreaSummary] {
        var pool = query.isEmpty ? areas.summaries : areas.search(query)
        if let cap = driveTime.maxMiles, let loc = location.userLocation {
            pool = pool.filter { haversine($0, loc) <= cap }
        }
        switch sort {
        case .alphabetic:
            return Self.sortedAlphabetically(pool)
        case .nearest:
            guard let loc = location.userLocation else {
                return Self.sortedAlphabetically(pool)
            }
            // Decorate-sort-undecorate: haversine ONCE per area (~29,850 calls)
            // instead of once per comparison inside the comparator (~430,000 for
            // a list this size). Same order, a fraction of the work.
            return pool.map { (area: $0, d: haversine($0, loc)) }
                .sorted { $0.d < $1.d }
                .map(\.area)
        case .mostTrails:
            return pool.sorted { ($0.trailCount ?? 0) > ($1.trailCount ?? 0) }
        case .longest:
            return pool.sorted { ($0.totalMi ?? 0) > ($1.totalMi ?? 0) }
        }
    }

    /// Alphabetical sort with the case-folded key computed ONCE per element.
    /// `localizedCaseInsensitiveCompare` inside a comparator runs O(n log n)
    /// times and each call is expensive (it bridges to NSString and consults the
    /// current locale); precomputing the key makes the comparisons plain string
    /// compares.
    private static func sortedAlphabetically(_ pool: [AreaSummary]) -> [AreaSummary] {
        pool.map { (area: $0, key: $0.name.lowercased()) }
            .sorted { $0.key < $1.key }
            .map(\.area)
    }

    /// Every input the cached pipeline depends on, in one value. `.task(id:)`
    /// re-runs `refreshResults` only when this changes — so an unrelated body
    /// evaluation no longer re-sorts the whole area list.
    private var refreshKey: String {
        // Coarse location key (~1 km): a few metres of GPS drift must not
        // trigger a full re-sort of the list.
        let locKey = location.userLocation
            .map { String(format: "%.2f,%.2f", $0.latitude, $0.longitude) } ?? "-"
        return [
            query,
            sort.rawValue,
            driveTime.rawValue,
            locKey,
            String(areas.summaries.count),
            String(trailSearch.entries.count),
        ].joined(separator: "|")
    }

    /// Recompute the cached pipeline. Called when an input actually changes,
    /// instead of implicitly on every body evaluation.
    private func refreshResults() {
        if areaNameCacheCount != areas.summaries.count {
            areaNameCache = Dictionary(areas.summaries.map { ($0.id, $0.name) },
                                       uniquingKeysWith: { first, _ in first })
            areaNameCacheCount = areas.summaries.count
        }
        results = computeResults()
        trailResults = computeTrailResults()
    }

    var body: some View {
        NavigationStack {
            Group {
                if areas.isLoadingIndex {
                    ProgressView("Loading areas...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List {
                        let trailHits = trailResults
                        if !trailHits.isEmpty {
                            Section("Trails") {
                                ForEach(trailHits) { hit in
                                    Button {
                                        pendingTrailId = hit.trailId
                                        selectedArea = areas.summaries.first { $0.id == hit.areaId }
                                    } label: {
                                        TrailHitRow(hit: hit)
                                    }
                                    .listRowBackground(Color.clear)
                                }
                            }
                        }
                        Section {
                            ForEach(results) { area in
                                Button {
                                    pendingTrailId = nil
                                    selectedArea = area
                                } label: {
                                    BrowseRow(area: area)
                                }
                                .listRowBackground(Color.clear)
                            }
                        } header: {
                            // Only label the section when a Trails
                            // section sits above it — an unqualified
                            // list doesn't need a header.
                            if !trailHits.isEmpty {
                                Text("Areas")
                            }
                        }
                    }
                    .listStyle(.plain)
                    // A query that matches nothing used to render a bare empty
                    // List — no message at all.
                    .overlay {
                        if results.isEmpty && trailResults.isEmpty {
                            if !query.isEmpty {
                                ContentUnavailableView.search(text: query)
                            } else {
                                ContentUnavailableView(
                                    "No parks loaded",
                                    systemImage: "antenna.radiowaves.left.and.right.slash",
                                    description: Text("Pull to refresh, or check your connection.")
                                )
                            }
                        }
                    }
                    .searchable(text: $query, prompt: "Search trails and parks")
                    .searchFocused($searchFocused)
                    .task { await trailSearch.loadIfNeeded() }
                }
            }
            .trailMeshBackground()
            .navigationTitle("Browse")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Sort", selection: $sort) {
                            ForEach(BrowseSort.allCases) { option in
                                Label(option.label, systemImage: option.systemImage).tag(option)
                            }
                        }
                        Divider()
                        // Actually disabled without a location fix. The comment
                        // here used to claim it was "disabled-styled" but no
                        // .disabled() existed, so picking "< 30 min" changed the
                        // toolbar icon and silently filtered nothing.
                        Picker("Drive time", selection: $driveTime) {
                            ForEach(BrowseDriveTime.allCases) { option in
                                Label(option.label, systemImage: "car").tag(option)
                            }
                        }
                        .disabled(location.userLocation == nil)
                        if location.userLocation == nil {
                            Text("Turn on location to filter by drive time.")
                        }
                    } label: {
                        Image(systemName: driveTime == .any
                              ? "arrow.up.arrow.down.circle"
                              : "line.3.horizontal.decrease.circle.fill")
                    }
                    .accessibilityLabel("Sort and filter areas")
                }
            }
        }
        .fullScreenCover(item: $selectedArea) { area in
            NavigationStack {
                AreaView(
                    areaId: area.id,
                    areaName: area.name,
                    initialSelectedTrailId: pendingTrailId
                )
            }
        }
        .onChange(of: selectedArea?.id) { _, newId in
            if let id = newId {
                ActivityLogService.shared.log(
                    category: "area",
                    action: "openFromBrowse",
                    context: ["areaId": id, "trailId": pendingTrailId ?? "nil"]
                )
            } else {
                pendingTrailId = nil
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .browseSearchTabTapped)) { _ in
            Task { @MainActor in
                // Let the tab-switch transition settle before grabbing focus —
                // focus requested mid-transition gets dropped by the system,
                // which would make the first tap into the tab do nothing.
                // Trimmed 350ms -> 120ms: the original was a conservative guess
                // and it read as "the keyboard takes a beat to appear."
                try? await Task.sleep(for: .milliseconds(120))
                searchFocused = true
            }
        }
        // Recompute the cached pipeline only when an input actually changes.
        .task(id: refreshKey) { refreshResults() }
    }

    private func haversine(_ a: AreaSummary, _ loc: CLLocationCoordinate2D) -> Double {
        let R = 3958.8
        let dLat = (a.centerLat - loc.latitude) * .pi / 180
        let dLon = (a.centerLon - loc.longitude) * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(loc.latitude * .pi / 180) * cos(a.centerLat * .pi / 180)
            * sin(dLon / 2) * sin(dLon / 2)
        return R * 2 * atan2(sqrt(h), sqrt(1 - h))
    }
}

struct BrowseRow: View {
    let area: AreaSummary
    @Environment(FavoritesService.self) private var favorites
    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    private var distanceMi: Double? {
        guard let loc = location.userLocation else { return nil }
        let R = 3958.8
        let dLat = (area.centerLat - loc.latitude) * .pi / 180
        let dLon = (area.centerLon - loc.longitude) * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(loc.latitude * .pi / 180) * cos(area.centerLat * .pi / 180)
            * sin(dLon / 2) * sin(dLon / 2)
        return R * 2 * atan2(sqrt(h), sqrt(1 - h))
    }

    var body: some View {
        HStack(spacing: 14) {
            SilhouetteThumb(areaId: area.id)

            VStack(alignment: .leading, spacing: 2) {
                Text(area.name)
                    .font(.body)
                    .foregroundStyle(.primary)
                HStack(spacing: 4) {
                    Text(area.subtitle)
                    if let count = area.trailCount, let mi = area.totalMi {
                        Text("·")
                        Text("\(count) trails · \(UnitFormatter.distance(miles: mi, units: units)) total")
                    }
                    if let d = distanceMi {
                        Text("·")
                        Text("\(UnitFormatter.distance(miles: d, units: units)) away")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                // Without a line limit the HStack resolves a too-wide
                // caption by WRAPPING one of its Texts mid-row ("88.6
                // / mi total" on two lines) instead of truncating.
                .lineLimit(1)
            }

            Spacer()

            if favorites.isFavorite(area.id) {
                Image(systemName: "heart.fill")
                    .foregroundStyle(.red)
                    .font(.caption)
            }

            Image(systemName: "chevron.right")
                .foregroundStyle(.tertiary)
                .font(.caption)
        }
        .contentShape(Rectangle())
    }
}

/// A trail search result: the trail's name over its area, length, and
/// difficulty, fronted by the area's silhouette thumbnail.
private struct TrailHitRow: View {
    let hit: AreaDataService.TrailSearchHit
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    var body: some View {
        HStack(spacing: 14) {
            // Just THIS trail's linework — not the whole area (which is what
            // the area rows below show). See TrailThumb.
            TrailThumb(areaId: hit.areaId, trailId: hit.trailId, difficulty: hit.difficulty)

            VStack(alignment: .leading, spacing: 2) {
                Text(hit.trailName)
                    .font(.body)
                    .foregroundStyle(.primary)
                HStack(spacing: 4) {
                    Text(hit.areaName)
                    Text("·")
                    Text(UnitFormatter.distance(miles: hit.distanceMi, units: units))
                    Text("·")
                    Text(hit.difficulty.rawValue)
                        .foregroundStyle(difficultyColor)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }

            Spacer()

            Image(systemName: "chevron.right")
                .foregroundStyle(.tertiary)
                .font(.caption)
        }
        .contentShape(Rectangle())
    }

    private var difficultyColor: Color {
        switch hit.difficulty {
        case .easy: return .green
        case .moderate: return .orange
        case .hard: return .red
        }
    }
}

/// 44×44 thumbnail of an area's trail-line silhouette — the same neon
/// linework as the Explore cards, shrunk to a list swatch. Falls back
/// to a neutral placeholder while the silhouette loads; the `.task`
/// kicks the R2 fetch (deduped by the service) for rows as they appear.
private struct SilhouetteThumb: View {
    let areaId: String
    @Environment(AreaSilhouetteService.self) private var silhouettes

    var body: some View {
        // Bare linework — no backing box or border. The lines ARE the
        // icon; a container plate around them just read as clutter.
        Group {
            if let silhouette = silhouettes.cachedSilhouette(for: areaId) {
                ThumbCanvas(silhouette: silhouette)
            } else {
                Image(systemName: "mountain.2.fill")
                    .foregroundStyle(.tertiary)
                    .font(.subheadline)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: 44, height: 44)
        .task(id: areaId) {
            await silhouettes.silhouette(for: areaId)
        }
    }
}

/// 44×44 thumbnail of a SINGLE trail's linework — used by trail search
/// results so the swatch shows the trail itself, not the whole area it
/// lives in. The full area is already cached (trail search only surfaces
/// cached areas), so the geometry is a synchronous lookup.
private struct TrailThumb: View {
    let areaId: String
    let trailId: String
    let difficulty: Difficulty
    @Environment(AreaDataService.self) private var areas
    @Environment(TrailShapeService.self) private var shapes

    var body: some View {
        Group {
            if let trail = areas.cachedArea(id: areaId)?.trails
                .first(where: { $0.id == trailId }), !trail.segments.isEmpty {
                // Full local geometry — an area we've already fetched.
                TrailThumbCanvas(trail: trail)
            } else if let shape = shapes.shape(areaId: areaId, trailId: trailId) {
                // Simplified shape from the background-loaded R2 file — lets an
                // un-visited area's trail still show its linework.
                ShapeThumbCanvas(shape: shape, difficulty: difficulty)
            } else {
                Image(systemName: "figure.hiking")
                    .foregroundStyle(.tertiary)
                    .font(.subheadline)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: 44, height: 44)
    }
}

/// Paints a Douglas-Peucker-simplified polyline (flat `[x0,y0,x1,y1,…]` in a
/// 0–255 box, from TrailShapeService) into the 44 px thumbnail, self-framed to
/// its own bounds and coloured by difficulty — the compact-shape twin of
/// TrailThumbCanvas.
private struct ShapeThumbCanvas: View {
    let shape: [UInt8]
    let difficulty: Difficulty

    var body: some View {
        Canvas { context, size in
            guard shape.count >= 4 else { return }
            var minX = 255.0, maxX = 0.0, minY = 255.0, maxY = 0.0
            var i = 0
            while i + 1 < shape.count {
                let x = Double(shape[i]), y = Double(shape[i + 1])
                minX = min(minX, x); maxX = max(maxX, x)
                minY = min(minY, y); maxY = max(maxY, y)
                i += 2
            }
            let pad: CGFloat = 5
            let drawW = size.width - 2 * pad, drawH = size.height - 2 * pad
            guard drawW > 0, drawH > 0 else { return }
            let sw = max(maxX - minX, 1e-6), sh = max(maxY - minY, 1e-6)
            let scale = min(drawW / sw, drawH / sh)
            let cw = sw * scale, ch = sh * scale
            let ox = pad + (drawW - cw) / 2, oy = pad + (drawH - ch) / 2

            var path = Path()
            var started = false
            i = 0
            while i + 1 < shape.count {
                let x = Double(shape[i]), y = Double(shape[i + 1])
                // Flip y — the shape stores latitude increasing upward.
                let p = CGPoint(x: ox + (x - minX) * scale,
                                y: oy + ch - (y - minY) * scale)
                if started { path.addLine(to: p) } else { path.move(to: p); started = true }
                i += 2
            }
            let color: Color
            switch difficulty {
            case .easy: color = .green
            case .moderate: color = .orange
            case .hard: color = .red
            }
            context.stroke(path, with: .color(color),
                           style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round))
        }
    }
}

/// Paints one trail's polyline(s) into the thumbnail, self-framed to the
/// trail's own bounds and coloured by its difficulty — same projection as
/// ThumbCanvas but scoped to a single trail.
private struct TrailThumbCanvas: View {
    let trail: Trail

    var body: some View {
        Canvas { context, size in
            var w = Double.infinity, s = Double.infinity
            var e = -Double.infinity, n = -Double.infinity
            for seg in trail.segments {
                for pt in seg where pt.count >= 2 {
                    if pt[1] < w { w = pt[1] }; if pt[1] > e { e = pt[1] }
                    if pt[0] < s { s = pt[0] }; if pt[0] > n { n = pt[0] }
                }
            }
            guard w.isFinite else { return }
            let pad: CGFloat = 5
            let drawW = size.width - 2 * pad
            let drawH = size.height - 2 * pad
            guard drawW > 0, drawH > 0 else { return }
            let centerLat = (s + n) / 2
            let lonScale = cos(centerLat * .pi / 180)
            let xRange = max((e - w) * lonScale, .leastNonzeroMagnitude)
            let yRange = max(n - s, .leastNonzeroMagnitude)
            let scale = min(drawW / xRange, drawH / yRange)
            let canvasW = xRange * scale
            let canvasH = yRange * scale
            let xOffset = pad + (drawW - canvasW) / 2
            let yOffset = pad + (drawH - canvasH) / 2

            var path = Path()
            for seg in trail.segments {
                var started = false
                for pt in seg where pt.count >= 2 {
                    let x = xOffset + (pt[1] - w) * lonScale * scale
                    let y = yOffset + canvasH - (pt[0] - s) * scale
                    let p = CGPoint(x: x, y: y)
                    if started { path.addLine(to: p) } else { path.move(to: p); started = true }
                }
            }
            let color: Color
            switch trail.difficulty {
            case .easy: color = .green
            case .moderate: color = .orange
            case .hard: color = .red
            }
            context.stroke(
                path,
                with: .color(color),
                style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round)
            )
        }
    }
}

/// Minimal silhouette painter for the 44pt thumbnails — the same
/// equirectangular projection as the card artwork, single stroke pass,
/// no glow (it wouldn't read at this size).
private struct ThumbCanvas: View {
    let silhouette: AreaSilhouette

    var body: some View {
        Canvas { context, size in
            guard let bbox = silhouette.bbox else { return }
            let pad: CGFloat = 5
            let drawW = size.width - 2 * pad
            let drawH = size.height - 2 * pad
            guard drawW > 0, drawH > 0 else { return }
            let centerLat = (bbox.s + bbox.n) / 2
            let lonScale = cos(centerLat * .pi / 180)
            let xRange = max((bbox.e - bbox.w) * lonScale, .leastNonzeroMagnitude)
            let yRange = max(bbox.n - bbox.s, .leastNonzeroMagnitude)
            let scale = min(drawW / xRange, drawH / yRange)
            let canvasW = xRange * scale
            let canvasH = yRange * scale
            let xOffset = pad + (drawW - canvasW) / 2
            let yOffset = pad + (drawH - canvasH) / 2

            for line in silhouette.l {
                guard line.p.count >= 2 else { continue }
                var path = Path()
                for (i, pt) in line.p.enumerated() {
                    guard pt.count >= 2 else { continue }
                    let x = xOffset + (pt[1] - bbox.w) * lonScale * scale
                    let y = yOffset + canvasH - (pt[0] - bbox.s) * scale
                    let p = CGPoint(x: x, y: y)
                    if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
                }
                let color: Color
                switch line.d {
                case "e": color = .green
                case "m": color = .orange
                case "h": color = .red
                default:  color = .gray
                }
                context.stroke(
                    path,
                    with: .color(color),
                    style: StrokeStyle(lineWidth: 1.1, lineCap: .round, lineJoin: .round)
                )
            }
        }
    }
}
