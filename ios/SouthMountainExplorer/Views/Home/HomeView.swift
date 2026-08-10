import SwiftUI

private enum LengthFilter: String, CaseIterable, Identifiable {
    case all
    case quick
    case half
    case full

    var id: String { rawValue }

    var label: String {
        switch self {
        case .all:   return "All"
        case .quick: return "Quick · <10 mi"
        case .half:  return "Half-day · 10–30"
        case .full:  return "Full-day · 30+"
        }
    }

    func matches(_ totalMi: Double?) -> Bool {
        if self == .all { return true }
        guard let m = totalMi else { return false }
        switch self {
        case .all:   return true
        case .quick: return m < 10
        case .half:  return (10..<30).contains(m)
        case .full:  return m >= 30
        }
    }
}

struct HomeView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(AreaSilhouetteService.self) private var silhouettes
    @Environment(LocationService.self) private var location
    @Environment(FavoritesService.self) private var favorites
    @Environment(AuthService.self) private var auth
    @Environment(RecordingService.self) private var recording

    @State private var selectedArea: AreaSummary? = nil
    @State private var showLocationPrompt = false
    @State private var showAllAreasMap = false
    @State private var showWalk = false
    @State private var history: [SavedRecording] = []
    @State private var lengthFilter: LengthFilter = .all

    // MARK: - Cached section content
    //
    // These were computed properties read by `body`. Because `body` also reads
    // `location.userLocation` — which LocationService republishes on EVERY GPS
    // fix (~1 Hz while tracking, no distance filter) — the whole set re-ran at
    // GPS rate, and several were read 2-4 times per pass. `unvisitedAreas`
    // alone sorted ~29,850 areas calling haversine INSIDE the comparator, i.e.
    // ~890,000 haversine calls per evaluation, to keep 10 rows. That is the
    // main reason Explore felt sticky. They are now plain state, recomputed
    // only when a real input changes (see `refreshSections`).

    @State private var continueArea: AreaSummary? = nil
    @State private var nearbyAreas: [AreaSummary] = []
    @State private var unvisitedAreas: [AreaSummary] = []
    /// User is far enough from coverage that we should set expectations.
    @State private var farFromCoverage = false

    /// Inputs the cached sections depend on. `.task(id:)` recomputes only when
    /// this changes. The location is rounded to ~1 km so ordinary GPS jitter
    /// doesn't retrigger the work.
    private var sectionsKey: String {
        let locKey = location.userLocation
            .map { String(format: "%.2f,%.2f", $0.latitude, $0.longitude) } ?? "-"
        return [
            locKey,
            String(areas.summaries.count),
            String(history.count),
            history.first?.areaId ?? "-",
            lengthFilter.rawValue,
        ].joined(separator: "|")
    }

    private func refreshSections() {
        let loc = location.userLocation

        continueArea = history.first.flatMap { areas.summary(id: $0.areaId) }

        if let loc {
            let pool = areas.nearby(lat: loc.latitude, lon: loc.longitude, limit: 30)
            nearbyAreas = Array(pool.filter { lengthFilter.matches($0.totalMi) }.prefix(10))
        } else {
            nearbyAreas = []
        }

        // Nearest covered area: one pass, no intermediate 29,850-element array.
        if let loc {
            var nearest = Double.greatestFiniteMagnitude
            for s in areas.summaries {
                let d = haversine(s, lat: loc.latitude, lon: loc.longitude)
                if d < nearest { nearest = d }
            }
            farFromCoverage = nearest != .greatestFiniteMagnitude && nearest > 50
        } else {
            farFromCoverage = false
        }

        let visited = Set(history.map { $0.areaId })
        let candidates = areas.summaries.lazy.filter { !visited.contains($0.id) }
        if let loc {
            // Decorate-sort-undecorate: haversine once per area, not twice per
            // comparison.
            unvisitedAreas = candidates
                .map { (area: $0, d: haversine($0, lat: loc.latitude, lon: loc.longitude)) }
                .sorted { $0.d < $1.d }
                .prefix(10)
                .map(\.area)
        } else {
            unvisitedAreas = Array(candidates.prefix(10))
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    // Out-of-region users: a waitlist prompt above the
                    // normal content (which still lists US/CA parks, so
                    // they can browse/plan). See RegionSupport.
                    if !RegionSupport.isSupported {
                        WaitlistCard(countryName: RegionSupport.currentCountryName,
                                     regionCode: RegionSupport.currentRegionCode)
                    }
                    if let pickup = continueArea {
                        continueSection(area: pickup)
                    }
                    // "Try Something New" is ordered by distance, so without a
                    // location fix it degrades to raw index order — which is
                    // sorted by state, i.e. ten Alabama parks headlining the
                    // app for every first-run user who declines the location
                    // prompt. Show the location CTA instead of a meaningless list.
                    if location.userLocation != nil {
                        if !unvisitedAreas.isEmpty {
                            areaSection(title: "Try Something New", items: unvisitedAreas)
                        }
                    } else {
                        emptyState
                    }
                    if !favorites.favoriteAreas.isEmpty {
                        areaSection(title: "Saved Areas", items: favorites.favoriteAreas)
                    }
                    if location.userLocation != nil {
                        nearYouSection
                    }
                }
                .padding()
            }
            .refreshable {
                // Pull-to-refresh: re-load history (so a hike completed
                // mid-session shows up in Pick Up / Try Something New),
                // re-poke the location service so Near You can recompute.
                history = await recording.loadHistory()
                if location.isAuthorized {
                    location.startLiveTracking()
                }
            }
            .trailMeshBackground()
            .navigationTitle("Explore")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showAllAreasMap = true
                    } label: {
                        Image(systemName: "map")
                    }
                    .accessibilityLabel("All Areas Map")
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        surpriseMe()
                    } label: {
                        Image(systemName: "dice")
                    }
                    .accessibilityLabel("Surprise Me")
                    .disabled(areas.summaries.isEmpty)
                }
                ToolbarItem(placement: .topBarLeading) {
                    // Area-less walk: no picking an area, no fumbling —
                    // opens a map of every trail within 20 mi and one
                    // Start button. See WalkView.
                    Button {
                        showWalk = true
                    } label: {
                        Image(systemName: "figure.walk")
                    }
                    .accessibilityLabel("Start a Walk")
                    .disabled(areas.summaries.isEmpty)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if !auth.isSignedIn {
                        NavigationLink(destination: AuthView()) {
                            Text("Sign In")
                                .fontWeight(.medium)
                        }
                    }
                }
            }
        }
        .onAppear {
            // In UI-test seed mode, never present the location prompt.
            // The CI simulator has no location permission, so this sheet
            // came up on every screenshot run — and it silently hijacked
            // the whole test: it floats over the tab bar (swallowing the
            // test's tab taps) and, as HomeView's presented sheet, blocks
            // every other sheet/cover in the app from presenting.
            var promptForLocation = !location.isAuthorized
            #if DEBUG
            if UITestSupport.isSeedRequested { promptForLocation = false }
            #endif
            if promptForLocation {
                showLocationPrompt = true
            } else if location.isAuthorized {
                location.startLiveTracking()
            }
            Task { history = await recording.loadHistory() }
            prefetchVisibleAreas()
        }
        .onChange(of: location.userLocation?.latitude) { _, _ in prefetchVisibleAreas() }
        .onChange(of: lengthFilter) { _, _ in prefetchVisibleAreas() }
        // Recompute the cached sections only when a real input changes, instead
        // of implicitly on every body evaluation (which happens at GPS rate).
        .task(id: sectionsKey) { refreshSections() }
        .sheet(isPresented: $showLocationPrompt) {
            LocationPromptView()
        }
        .sheet(isPresented: $showAllAreasMap) {
            AllAreasMapView()
        }
        // fullScreenCover, not a sheet: the walk is a map-first surface
        // (sheets would also collide with HomeView's three existing
        // sheet slots — one sheet per presenter).
        .fullScreenCover(isPresented: $showWalk) {
            WalkView()
        }
        .sheet(item: $selectedArea) { area in
            AreaView(areaId: area.id, areaName: area.name)
        }
        .onChange(of: selectedArea?.id) { _, newId in
            // One log site for every path that sets selectedArea —
            // visible cards, continue card, recommendations.
            // AreaView's .task fires its own area/opened entry on
            // mount, but this one captures the tab + source row at
            // tap time, before any async load. Keyed on .id so we
            // don't need AreaSummary to conform to Equatable.
            if let id = newId {
                ActivityLogService.shared.log(
                    category: "area",
                    action: "openFromHome",
                    context: ["areaId": id]
                )
            }
        }
    }

    private func areaSection(title: String, items: [AreaSummary]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title2)
                .fontWeight(.bold)
                .padding(.horizontal, 4)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 14) {
                    ForEach(items) { area in
                        AreaCard(area: area)
                            .onTapGesture { selectedArea = area }
                    }
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 2)
            }
        }
    }

    private var nearYouSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(farFromCoverage ? "Closest Areas" : "Near You")
                        .font(.title2)
                        .fontWeight(.bold)
                    Spacer()
                }
                if farFromCoverage {
                    Text("Nothing within 50 miles of you — here are the closest parks we have.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 4)

            // Caption first so the chips can't be misread as "distance from
            // me" — they filter by total trail miles inside each area.
            Text("Filter by total trail miles in the area")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 4)
                .padding(.top, 2)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(LengthFilter.allCases) { filter in
                        Button {
                            withAnimation(.easeInOut(duration: 0.15)) { lengthFilter = filter }
                        } label: {
                            Text(filter.label)
                                .font(.caption.weight(.medium))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                        }
                        .buttonStyle(.plain)
                        .compatibleGlassTinted(isSelected: lengthFilter == filter, in: .capsule)
                        .foregroundStyle(lengthFilter == filter ? .white : .primary)
                    }
                }
                .padding(.horizontal, 4)
            }

            if nearbyAreas.isEmpty {
                Text("No areas match this filter near you.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 4)
                    .padding(.vertical, 12)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 14) {
                        ForEach(nearbyAreas) { area in
                            AreaCard(area: area)
                                .onTapGesture { selectedArea = area }
                        }
                    }
                    .padding(.horizontal, 4)
                    .padding(.vertical, 2)
                }
            }
        }
    }

    private func continueSection(area: AreaSummary) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pick Up Where You Left Off")
                .font(.title2)
                .fontWeight(.bold)
                .padding(.horizontal, 4)

            Button {
                selectedArea = area
            } label: {
                ContinueCard(area: area)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("continue-card")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 20) {
            Image(systemName: "mountain.2")
                .font(.system(size: 56))
                .foregroundStyle(.secondary)
            Text("Trails near you")
                .font(.title2)
                .fontWeight(.semibold)
            Text("Turn on location and we'll show parks within driving distance. Otherwise, search thousands of parks in Browse.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            VStack(spacing: 10) {
                Button("Enable Location") { location.requestPermission() }
                    .buttonStyle(.borderedProminent)
                Button("Browse All Areas") { showAllAreasMap = true }
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }

    /// Warm the per-area cache for every card currently rendered on Explore so
    /// tapping in opens AreaView instantly instead of showing the 1–3s
    /// "Loading…" spinner. `areas.area(id:)` is idempotent: it short-circuits
    /// on a cache hit (memory or disk), refreshes silently if stale, and
    /// dedupes concurrent fetches per id, so calling it for the same set of
    /// ids on repeated appears or filter changes is safe and free.
    private func prefetchVisibleAreas() {
        var seen = Set<String>()
        var ids: [String] = []
        let pools: [[AreaSummary]] = [
            continueArea.map { [$0] } ?? [],
            unvisitedAreas,
            favorites.favoriteAreas,
            nearbyAreas
        ]
        for pool in pools {
            for area in pool where seen.insert(area.id).inserted {
                ids.append(area.id)
            }
        }
        for id in ids {
            Task { _ = await areas.area(id: id) }
            // Silhouettes live on R2 too now (no longer bundled in
            // the iOS binary). Kick the per-area fetch in parallel
            // with the full-area fetch so cards have artwork ready
            // by the time they hit the visible scroll window.
            // AreaSilhouetteService dedupes in-flight requests, so
            // racing the per-card `.task` is harmless.
            Task { _ = await silhouettes.silhouette(for: id) }
        }
    }

    /// Pick a random area the user hasn't recorded in yet and open it.
    /// Falls back to any area when every area has been visited (or
    /// when there's no history yet — then "unvisited" is everything).
    private func surpriseMe() {
        let visited = Set(history.map { $0.areaId })
        let unvisited = areas.summaries.filter { !visited.contains($0.id) }
        let pool = unvisited.isEmpty ? areas.summaries : unvisited
        guard let pick = pool.randomElement() else { return }
        selectedArea = pick
    }

    private func haversine(_ a: AreaSummary, lat: Double, lon: Double) -> Double {
        let R = 3958.8
        let dLat = (a.centerLat - lat) * .pi / 180
        let dLon = (a.centerLon - lon) * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(lat * .pi / 180) * cos(a.centerLat * .pi / 180)
            * sin(dLon / 2) * sin(dLon / 2)
        return R * 2 * atan2(sqrt(h), sqrt(1 - h))
    }
}

struct LocationPromptView: View {
    @Environment(LocationService.self) private var location
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Image(systemName: "location.circle.fill")
                    .font(.system(size: 72))
                    .foregroundStyle(.green)
                    .symbolEffect(.pulse)

                Text("Find Trails Near You")
                    .font(.title)
                    .fontWeight(.bold)

                Text("Share your location to discover nearby trails and see your position on the map while hiking.")
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)

                VStack(spacing: 12) {
                    Button("Allow Location Access") {
                        location.requestPermission()
                        dismiss()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .frame(maxWidth: .infinity)

                    Button("Not Now") { dismiss() }
                        .foregroundStyle(.secondary)
                }
            }
            .padding(32)
            .presentationDetents([.medium])
            .presentationDragIndicator(.visible)
        }
    }
}
