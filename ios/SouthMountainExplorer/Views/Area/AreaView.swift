import SwiftUI
import CoreLocation
import UIKit
import OSLog

/// Logger for `AreaView` lifecycle and decision events — area
/// loads, suggestion-banner mounts, trail-completion celebrations.
/// Lands in the Send Diagnostics bundle so a field report carries
/// the sequence of UI events the user saw, not just the recording
/// state.
private let log = Logger(subsystem: "com.trekdex.app", category: "area")

struct AreaView: View {
    let areaId: String
    let areaName: String
    /// When set on init, the view plays a one-shot trail-completion
    /// celebration overlay on first appear. Used by the notification-tap
    /// deep-link from ContentView so a user opening the "Trail Complete!"
    /// notification gets a celebratory beat instead of a silent jump in.
    var initialCelebrationTrailName: String? = nil
    /// Set when the view is opened from a trail search result — the
    /// trail is pre-selected once the area loads, so the map highlights
    /// it and the trail list scrolls it into view.
    var initialSelectedTrailId: String? = nil
    /// Exact display name paired with the requested raw ID. Optional so
    /// existing local/deep-link callers that only know an ID keep working.
    var initialSelectedTrailName: String? = nil

    @Environment(AreaDataService.self) private var areas
    @Environment(AreaSilhouetteService.self) private var silhouettes
    @Environment(RecordingService.self) private var recording
    @Environment(FavoritesService.self) private var favorites
    @Environment(LocationService.self) private var location
    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(ActivityService.self) private var activity
    @Environment(\.dismiss) private var dismiss
    /// Map style binding lives on AreaView so the user can flip
    /// it from the per-map "•••" menu rather than digging into
    /// Settings. Same `@AppStorage` key MapKitMapView reads, so
    /// changes propagate immediately to the open map.
    @AppStorage(StorageKeys.mapStyle) private var mapStyle: MapStylePreference = .standard
    @AppStorage(StorageKeys.showAllParking) private var showAllParking = false
    /// Units preference so the header's area total renders in mi/km and
    /// reacts to the Settings toggle. Was previously hardcoded to miles.
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var area: Area? = nil
    @State private var isLoading = true
    @State private var loadError: String? = nil
    /// Bumped by the failure screen's Try Again button to re-run the load task.
    @State private var loadAttempt = 0

    /// Whether the load failed for lack of a connection, so the failure screen
    /// can say so plainly instead of echoing a system string.
    private var isOfflineError: Bool {
        guard let e = loadError?.lowercased() else { return false }
        return e.contains("offline") || e.contains("internet")
            || e.contains("network") || e.contains("connection")
    }
    /// Loading view always plays for at least 1.5 s so the silhouette
    /// reveal animation finishes even when real data lands in under
    /// half a second. Flipped by a task timer that starts on
    /// `.task(id: areaId)` and ends 1.5 s later.
    @State private var minLoadingTimeElapsed = false

    // MARK: - Measured blocks behind the smallest sheet stop
    //
    // The smallest stop used to be one constant for every situation, so it was
    // wrong in most of them: an expanded trail row adds a 96pt elevation chart
    // and a parking line, and RecordingPanel grows and shrinks on its own as the
    // GPS capsule and the live elevation strip come and go. The stop is now the
    // sum of the blocks that have to be WHOLE right now.
    //
    // Every number below is measured from the laid-out view, never derived from
    // font metrics — that keeps it right at any Dynamic Type size and on any
    // device. The values here are only seeds for the first frame.

    /// Area name + summary line, both single-line by design.
    @State private var headerHeightFull: CGFloat = 91
    // The list's search-and-filter chrome no longer has a height here: it
    // renders ONLY at the browse stop (see `trailList`), so it is absent from
    // the fit arithmetic entirely — and a text field the keyboard could grab
    // no longer exists at the stop the keyboard used to yank taller.
    /// The recording context's LIVE height: camera controls plus the live
    /// recording panel. Seeded roughly and corrected on the first layout pass —
    /// unlike the browse measurements this one is never a high-water mark,
    /// because the dashboard must be allowed to get shorter again when the
    /// panel's GPS capsule goes away.
    @State private var recordingHeight: CGFloat = 170
    /// The idle context's toolbar block above the trail list — camera controls
    /// plus the Start/Collection action row — measured live like every other
    /// block the smallest stop must show whole.
    @State private var idleToolbarHeight: CGFloat = 108
    // Seeds err TALL on purpose. Every measured value here is used until its
    // real one lands, and the two failure directions are not equal: too tall is
    // one slightly roomy frame nobody notices, too short CLIPS — which is what
    // this screen has been reported for over and over.
    /// An ordinary, unexpanded trail row.
    @State private var collapsedRowHeight: CGFloat = 62
    /// The selected row with its chart and parking line expanded into it.
    /// Seeded for a LONG name that wraps to two lines plus a parking line, not
    /// for the average row — see the note on the seeds above.
    @State private var selectedRowHeight: CGFloat = 240
    /// The sheet's REAL top edge, in points up from the physical screen bottom,
    /// reported by the sheet's own content rather than derived from what a
    /// detent is believed to mean.
    ///
    /// Every version of this before was a model of UIKit's behaviour: does
    /// `.height(x)` include the home indicator, is `.fraction(0.5)` half of the
    /// screen or half of the sheet's maximum. Each model was wrong in a
    /// different place, and the floating controls sat at a different distance
    /// from the sheet at each stop because each stop used a different guess.
    /// A measurement has no stops to get individually wrong.
    @State private var measuredSheetTop: CGFloat? = nil

    /// Trail-list sheet detents — exactly TWO stops, and the MAP IS VISIBLE
    /// AT BOTH. There is no full-screen stop: this is a map screen, and a
    /// menu that can cover the map defeats the reason the screen exists.
    ///   - fit: `minSheetHeight` — precisely as tall as the current context
    ///     needs to show its whole content, no taller.
    ///   - browse: `browseHeight` — a tall list stop capped so roughly the top
    ///     40% of the screen always stays map. Search and filters live here.
    ///
    /// There is deliberately no middle stop. Three stops meant the bottom two
    /// sat arbitrarily close on real devices, and the middle one silently
    /// disappeared whenever the measured fit height grew past it — the sheet
    /// sometimes had three stops and sometimes two, for no reason the user
    /// could see. Two stops with honest jobs replaces that: peek at the data,
    /// or browse the list — with the map still in view above it.
    /// Seed for `committedMinHeight` AND the initial detent selection — the
    /// two must agree on the first frame or the selection points at a stop
    /// that is not in the set. Seeded at the TYPICAL idle fit (header +
    /// toolbar + three rows − band + air) so the open animation rises to
    /// roughly the right place in one stage instead of visibly retargeting
    /// once real measurements land ~140 ms in.
    static let seedMinHeight: CGFloat = 360

    /// Air between the fit stop's last visible content and the physical
    /// screen bottom. Flush-to-the-edge read as clipping on device.
    static let fitBottomAir: CGFloat = 6

    /// Currently-active detent of the trail-list sheet. Drives
    /// `effectiveBottomInset` so the map's user-dot shift compensates
    /// for whatever portion of the screen the sheet covers.
    ///
    /// Replaces the previous custom drag implementation (showTrailList
    /// + trailListHeight + DragGesture + per-frame height @State) that
    /// was burning frames on every drag tick. The native sheet drags
    /// in UIKit, so SwiftUI's body never re-evaluates for the gesture
    /// itself — only when the detent SETTLES (at most once per
    /// release).
    ///
    /// Opens at the fit stop: the map is the star of this screen, and the
    /// sheet's opening job is context, not takeover.
    @State private var sheetDetent: PresentationDetent = .height(AreaView.seedMinHeight)
    /// Is the user parked on the smallest stop?
    ///
    /// **Not** `sheetDetent == minDetent`, and that distinction is the entire
    /// reason this exists. `minDetent` is `.height(minSheetHeight)`, and
    /// `minSheetHeight` is computed from live measurements — so comparing
    /// against `minDetent` made every measurement able to flip any decision
    /// keyed on "are we at the smallest stop", which closed a loop:
    ///
    ///   measure -> minSheetHeight changes -> minDetent is a NEW value ->
    ///   `sheetDetent == minDetent` goes false for a pass -> the header
    ///   switches variant -> measures -> minSheetHeight changes -> ...
    ///
    /// It is NOT on its own what crashed build 297 — `git show` on both build
    /// shas has the identical comparison, and 296 did not crash. What it does is
    /// make the sheet re-measure over and over, which is the load the re-entrant
    /// `scrollTo` in the commit before this one then dies under. Amplifier, not
    /// trigger.
    ///
    /// Tracked as its own boolean, it changes only when the USER moves the
    /// sheet. A measurement can no longer reach it, so there is no cycle left to
    /// damp — this is a fix, not another deadband.
    /// Seeded TRUE because the sheet opens at the fit stop — a false seed let
    /// the browse-only chrome flash for the first frame of every area open.
    @State private var atMinStop = true
    /// See `minSheetHeight`. Seeded at the shared constant so the first frame
    /// is sane before anything has been measured.
    @State private var committedMinHeight: CGFloat = AreaView.seedMinHeight
    @State private var minHeightCommit: Task<Void, Never>? = nil
    /// Collection is a deliberate secondary destination, not a hidden page.
    @State private var showCollection = false
    /// Bumped by the toolbar search button after expanding to browse, so the
    /// just-mounted search field grabs focus and the keyboard is ready.
    @State private var searchFocusTick = 0
    /// Set when selecting a trail from the browse stop dropped the sheet to
    /// fit so the map could show the trail. Deselecting then returns the sheet
    /// to browse — unless the user has dragged it themselves since, which
    /// clears this and leaves the sheet where they put it.
    @State private var collapsedForSelection = false
    /// Bumped after the sheet settles with a trail selected, so the map
    /// re-frames that trail in whatever area the sheet now leaves visible.
    @State private var fitSelectedTrailTick = 0
    @State private var selectedTrailRefit: Task<Void, Never>? = nil
    /// Bumped on any deselect that does NOT raise the sheet to browse, so the
    /// list snaps away from the stale half-row offset a shrunken card leaves
    /// behind. Not bumped when the deselect returns the sheet to browse — there
    /// the offset is the user's place in the list, not an artefact. AreaView
    /// owns this decision outright so no cross-view observer ordering is
    /// involved: one tick, one observer, and "keep the offset" is simply the
    /// absence of a tick.
    @State private var scrollListToTopTick = 0
    @State private var selectedTrailId: String? = nil
    /// Trail being reported via the overflow menu — drives the report sheet.
    @State private var reportingTrail: Trail? = nil
    @State private var finishedRecording: FinishedRecording? = nil
    @State private var showSummary = false
    @State private var showAreaComplete = false
    /// Past hikes in this area, with timestamps so TrailMapView can
    /// filter "walked since last completion" for the orange overlay.
    /// Previously this was just `pastPaths: [[GpsPoint]]`; the
    /// halo render only needs paths but the overlay needs dates.
    @State private var pastHikes: [PastHike] = []
    @State private var recenterTick: Int = 0
    /// Bumped when the user taps Switch on the retarget or
    /// suggestion banner. Tells `TrailMapView` to re-fit the camera
    /// around the new active trail PLUS the user's current
    /// location. We need a separate signal from `selectedTrailId`
    /// because the banner shows up precisely because the user
    /// already tapped a different trail — i.e. `selectedTrailId`
    /// is ALREADY pointing at the new trail by the time Switch is
    /// tapped, so SwiftUI's `.onChange(of:)` would not fire.
    @State private var centerOnSwitchedTrailTick: Int = 0
    /// Owns the camera tracking cycle for the map. The rotation button
    /// in `controlBar` cycles this; TrailMapView observes via Binding
    /// and swaps `MapCameraPosition` accordingly. Tapping the recenter
    /// button forces this back to `.free` so a one-shot recenter isn't
    /// immediately undone by re-engaged tracking.
    @State private var trackingMode: MapTrackingMode = .free
    /// Ephemeral hint that pops above the controlBar when the user
    /// taps the rotation cycle button. Set to the new mode's
    /// `toastLabel`; auto-clears after ~2 s. Self-documents the
    /// otherwise-cryptic three-state cycle.
    @State private var trackingModeToast: String? = nil
    @State private var trackingModeToastTask: Task<Void, Never>? = nil

    // Pre-flight checks before kicking off a recording.
    @State private var showConflictAlert = false
    @State private var conflictAreaName: String = ""
    /// Captured by tryStartRecording when a confirmation dialog interrupts
    /// the start. The dialog's "proceed" button reads this so a trail-mode
    /// request survives the round-trip — without it, "Start Anyway" /
    /// "Stop & Start Here" silently downgraded to .roam mode and the
    /// recording-trail highlight never engaged.
    @State private var pendingRecordTrailId: String? = nil
    @State private var recordingSaveFailureMessage: String? = nil
    /// Name of the trail to celebrate over the map. Auto-clears after a
    /// short delay so the overlay doesn't sit forever.
    @State private var celebrationTrailName: String? = nil
    /// Half-sheet that presents the area-wide multi-track GPX
    /// share. Wraps URL because URL itself isn't Identifiable.
    @State private var areaGpxShareURL: IdentifiedURL? = nil
    /// Set when a GPX export throws, so the tap gets an answer instead of
    /// nothing at all. See `exportFailureAlert`.
    @State private var exportFailure: String? = nil

    // Trail-list filters live up here so the map and the list share the
    // same source of truth — flipping a filter hides the corresponding
    // polylines from the map too, not just the list rows.
    @State private var statusFilter: TrailStatusFilter = .all
    @State private var difficultyFilter: TrailDifficultyFilter = .all
    @State private var lengthFilter: TrailLengthFilter = .all
    @State private var routeFilter: TrailRouteFilter = .all
    @State private var trailSort: TrailSort = .standard
    /// Free-text search over trail names. Lives alongside the
    /// existing filter state so the filtered-trails computed
    /// property can fold it into a single pass.
    @State private var trailSearchQuery: String = ""

    private var isRecording: Bool {
        // Walks are excluded even when their primary area is this one:
        // AreaView's RecordingPanel stops through the single-area path,
        // which would save the walk as a plain hike and throw away its
        // multi-area credits. Walks render their own panel in WalkView.
        guard let rec = recording.activeRecording else { return false }
        return rec.areaId == areaId && rec.mode != .walk
    }

    /// Trail set after applying the user's filters. Single source of
    /// truth shared between TrailListView (which renders the rows) and
    /// TrailMapView (which renders the polylines).
    private func computeFilteredTrails(_ area: Area) -> [Trail] {
        Self.sorted(filterTrails(area), by: trailSort,
                    from: location.userLocation,
                    coverage: coverage.coverage(for: areaId))
    }

    /// Order the filtered set. Each comparator's key is computed ONCE per trail
    /// (decorate-sort-undecorate) rather than inside the comparator — the
    /// nearest sort would otherwise run its distance math O(n log n) times.
    private static func sorted(_ trails: [Trail],
                               by sort: TrailSort,
                               from userLocation: CLLocationCoordinate2D?,
                               coverage: [String: Double]) -> [Trail] {
        switch sort {
        case .standard:
            return trails
        case .alphabetical:
            return trails.map { ($0, $0.name.lowercased()) }
                .sorted { $0.1 < $1.1 }.map(\.0)
        case .shortest:
            return trails.sorted { $0.distanceMi < $1.distanceMi }
        case .longest:
            return trails.sorted { $0.distanceMi > $1.distanceMi }
        case .progress:
            return trails.map { ($0, coverage[$0.id] ?? 0) }
                .sorted { $0.1 > $1.1 }.map(\.0)
        case .nearest:
            // No fix: leave the order alone rather than inventing one.
            guard let loc = userLocation else { return trails }
            return trails.map { trail -> (Trail, Double) in
                var best = Double.greatestFiniteMagnitude
                for seg in trail.segments {
                    for p in seg where p.count >= 2 {
                        let d = haversineDistanceM(lat1: loc.latitude, lon1: loc.longitude,
                                                   lat2: p[0], lon2: p[1])
                        if d < best { best = d }
                    }
                }
                return (trail, best)
            }
            .sorted { $0.1 < $1.1 }
            .map(\.0)
        }
    }

    private func filterTrails(_ area: Area) -> [Trail] {
        area.trails.filter { trail in
            // Only ask about completion when the status filter actually needs
            // it. This used to run for `.all` too and throw the answer away —
            // and since it recomputes on every trail-search keystroke, that was
            // a full completion sweep of the area per character typed.
            switch statusFilter {
            case .all: break
            case .incomplete: if progress.isComplete(trail, areaId: areaId) { return false }
            case .complete:   if !progress.isComplete(trail, areaId: areaId) { return false }
            }
            if !difficultyFilter.matches(trail.difficulty) { return false }
            if !lengthFilter.matches(trail.distanceMi) { return false }
            if !routeFilter.matches(trail.routeType) { return false }
            let q = trailSearchQuery.trimmingCharacters(in: .whitespaces)
            if !q.isEmpty && !trail.name.localizedCaseInsensitiveContains(q) {
                return false
            }
            return true
        }
    }

    /// Cached output of `computeFilteredTrails` so the trail-list
    /// drag (which thrashes `trailListHeight` at the display's
    /// frame rate and forces this body to re-evaluate) doesn't
    /// re-run the O(N trails) filter on every frame. Recomputed
    /// only on the actual inputs via `.onChange(of: filterKey)`
    /// below. Same story for `visibleTrailIds`, which would also
    /// allocate a fresh Set every body eval otherwise — and that
    /// Set drives a `lastVisibleTrailIds != visibleTrailIds` check
    /// inside MapKitMapView.updateUIView, so a fresh instance each
    /// frame triggered repeated Set comparisons on the map side too.
    @State private var filtered: [Trail] = []
    @State private var visibleTrailIds: Set<String>? = nil

    /// Cached `Set` of valid trail IDs for the loaded area. Used by
    /// `filteredCompletedCount`, which previously allocated this Set
    /// inline every body eval — and was fed into a `.onChange` that
    /// SwiftUI evaluates on every body pass, so it allocated 60-120 ×/sec
    /// during the trail-list drag. Recomputed only when the area's
    /// trail set actually changes (rare — only on initial load).
    @State private var areaTrailIds: Set<String> = []

    /// Inputs that change `filtered`. Bundled into a single Equatable
    /// value so a single `.onChange` covers all of them. Per-area
    /// completion count is observed separately (it's an `Int` so the
    /// comparison stays O(1) per body eval, vs. comparing the full
    /// completions dictionary which would be O(N)).
    private struct FilterKey: Equatable {
        let areaId: String
        let statusFilter: TrailStatusFilter
        let difficultyFilter: TrailDifficultyFilter
        let lengthFilter: TrailLengthFilter
        let routeFilter: TrailRouteFilter
        let sort: TrailSort
        let searchQuery: String
    }

    private var filterKey: FilterKey {
        FilterKey(
            areaId: area?.id ?? "",
            statusFilter: statusFilter,
            difficultyFilter: difficultyFilter,
            lengthFilter: lengthFilter,
            routeFilter: routeFilter,
            sort: trailSort,
            searchQuery: trailSearchQuery
        )
    }

    private var areaCompletionsCount: Int {
        progress.completions[areaId]?.count ?? 0
    }

    private func recomputeFiltered() {
        guard let area else {
            filtered = []
            visibleTrailIds = nil
            areaTrailIds = []
            return
        }
        filtered = computeFilteredTrails(area)
        visibleTrailIds = hasActiveFilter ? Set(filtered.map(\.id)) : nil
        // Same area? Skip the Set rebuild. Trail list within an area
        // is stable for the area's lifetime.
        if areaTrailIds.isEmpty || areaTrailIds.count != area.trails.count {
            areaTrailIds = Set(area.trails.map(\.id))
        }
    }

    /// Whether any non-default filter is active. When false we pass
    /// `nil` to TrailMapView so it skips the per-trail filter check
    /// entirely, since the unfiltered render is the common case.
    private var hasActiveFilter: Bool {
        statusFilter != .all
            || difficultyFilter != .all
            || lengthFilter != .all
            || routeFilter != .all
            || !trailSearchQuery.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            if let area, minLoadingTimeElapsed {
                // Full-screen map. The trail-list sheet (presented
                // via .sheet below) covers the bottom portion and
                // is system-native, so we hand TrailMapView the
                // current sheet detent's height as `bottomInset` and
                // it shifts the user dot upward to clear the visible
                // sheet area.
                TrailMapView(
                    area: area,
                    activeRecording: isRecording ? recording.activeRecording : nil,
                    pastHikes: pastHikes,
                    recenterTick: recenterTick,
                    centerOnSwitchedTrailTick: centerOnSwitchedTrailTick,
                    fitSelectedTrailTick: fitSelectedTrailTick,
                    selectedTrailId: $selectedTrailId,
                    visibleTrailIds: visibleTrailIds,
                    bottomInset: effectiveBottomInset,
                    trackingMode: $trackingMode
                )
                .ignoresSafeArea()

                floatingRetargetBanner(area: area)
                    .animation(.easeInOut(duration: 0.25), value: selectedTrailId)

                // iOS 26 presents the native sheet as a floating card: its
                // custom presentation background is opaque, but the host itself
                // still stops 8pt above the screen and rounds away as much as
                // 20pt at the lower corners. Because the map is full-bleed, that
                // host margin shows through at the physical bottom edge.
                //
                // Bridge only that measured lower curve behind the sheet. The
                // explicit secondary-system color matches the sheet's resolved
                // surface (unlike the old 80pt system-background workaround,
                // which resolved to pure black outside the presentation host).
                // This view is outside the sheet's measured content, so it cannot
                // change detents, scrolling, or the map's camera inset.
                Color(.secondarySystemBackground)
                    .frame(height: 20)
                    .frame(maxHeight: .infinity, alignment: .bottom)
                    .ignoresSafeArea()
                    .allowsHitTesting(false)

                // The camera controls used to float over the map here,
                // anchored to the sheet's top edge and riding up and down with
                // it. They live on the Record page now.
                //
                // That anchoring was its own running defect: the controls sat at
                // a different distance from the sheet at each stop, clipped
                // behind it, and dropped when a trail was selected — because
                // their position was derived from a model of what a detent
                // means. On a page they are simply laid out, and there is no
                // distance to compute.

            } else if isLoading || (area != nil && !minLoadingTimeElapsed) {
                loadingState
            } else {
                // Human copy + a way out. This used to render the raw error
                // straight through — including engineer-facing strings like
                // "Fetch already in progress but returned no data." — with no
                // retry, so a failed load meant closing and reopening.
                ContentUnavailableView {
                    Label(isOfflineError ? "You're offline" : "Couldn't load this park",
                          systemImage: isOfflineError
                            ? "wifi.slash" : "exclamationmark.triangle")
                } description: {
                    Text(isOfflineError
                         ? "This park's trails aren't downloaded yet. Reconnect and try again."
                         : "Something went wrong fetching the trails. Try again in a moment.")
                } actions: {
                    Button("Try Again") { loadAttempt += 1 }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .sheet(isPresented: trailSheetPresented) {
            // Trail-list sheet. Presented as soon as the area has
            // loaded (so we never flash an empty sheet during the
            // loading silhouette animation) and never dismissed
            // afterward — `interactiveDismissDisabled` blocks the
            // swipe-to-dismiss, so the user can't accidentally
            // close it. They can drag down to the small peek detent for a
            // near-full-map view instead.
            //
            // This replaces the previous hand-rolled bottom panel
            // (DragGesture + per-frame @State + Material backdrop
            // re-rendering at varying size) — that custom path was
            // dropping frames every drag because the resize cascaded
            // into AreaView.body re-evals, MapKitMapView.updateUIView
            // calls, and Material blur rerenders. UISheetPresentation-
            // Controller handles all of that natively in UIKit /
            // Core Animation; SwiftUI only re-renders when the detent
            // SETTLES (at most once per release), not per drag tick.
            if let area {
                sheetContent(area: area)
                    // Report where the sheet ACTUALLY starts, so the floating
                    // map controls can be pinned to a fact instead of to a
                    // model of what a detent means. `.global` here is the
                    // window, so screen height minus this is the panel's height
                    // as drawn — at every stop and mid-drag, with no per-stop
                    // arithmetic to get individually wrong.
                    .onGeometryChange(for: CGFloat.self) { proxy in
                        UIScreen.main.bounds.height - proxy.frame(in: .global).minY
                    } action: { top in
                        // Ignore sub-point noise so this can't thrash the map's
                        // bottomInset on every frame of a drag.
                        if abs((measuredSheetTop ?? -1) - top) >= 1 {
                            measuredSheetTop = top
                        }
                    }
                    // THE SHEET-LEVEL safe-area ignore, and the one earlier
                    // attempts kept missing the consequence of.
                    //
                    // If the sheet's root content stops at the safe area, its
                    // frame ends ~34pt above the screen. Nothing inside can grow
                    // past a parent that already ended — the trail list's own
                    // bottom-edge ignore is a no-op whenever THIS one fails,
                    // because a child has no safe area left to ignore once its
                    // parent has already been cut short.
                    //
                    // `.container` narrowed this to one safe-area region.
                    // Dropping the region argument ignores ALL of them at the
                    // bottom edge, which is what was wanted. Bottom edge only,
                    // so the header can never slide under the notch.
                    .ignoresSafeArea(edges: .bottom)
                    .presentationDetents(sheetDetentSet, selection: $sheetDetent)
                    // `.height(190)` and `.height(240)` are DIFFERENT detents,
                    // so a smallest stop that changes height would leave the
                    // selection binding pointing at a stop that no longer
                    // exists and strand the sheet. Re-point it, but only when
                    // the user was actually sitting on the old minimum —
                    // someone parked at browse is never yanked down
                    // because they selected a trail.
                    .onChange(of: minSheetHeight) { _, new in
                        // ONLY when the user was sitting on the fit stop —
                        // someone parked at browse is never yanked down because
                        // content resized underneath them.
                        //
                        // The test was `sheetDetent == .height(old)`, which
                        // fails whenever two measurements land between one body
                        // evaluation and the next: `old` is then a height the
                        // selection never held, so the re-point is skipped and
                        // `sheetDetent` is left naming a stop that is no longer
                        // in `sheetDetentSet` at all. A boolean cannot miss.
                        if atMinStop { sheetDetent = .height(new) }
                    }
                    // The one place `atMinStop` is written, and it deliberately
                    // does NOT consult `minSheetHeight`.
                    //
                    // The browse stop's height is a per-device constant, so
                    // "are we on the fit stop" is answerable by ruling browse
                    // out, without comparing against the measured fit height
                    // that the answer would then go on to change.
                    .onChange(of: sheetDetent, initial: true) { old, detent in
                        atMinStop = detent != browseDetent
                        guard old != detent else { return }
                        // The user dragged to browse themselves: deselecting
                        // later must not move the sheet on them.
                        if detent == browseDetent { collapsedForSelection = false }
                        // The visible map just changed size. If a trail is
                        // selected, re-frame it in the new visible area once
                        // the sheet's motion has landed.
                        if selectedTrailId != nil, !isRecording {
                            scheduleSelectedTrailRefit()
                        }
                    }
                    .onDisappear { selectedTrailRefit?.cancel() }
                    // The browse stop's height is derived from live screen
                    // geometry. A rotation while parked there would otherwise
                    // leave the selection naming a stop no longer in the set —
                    // a stranding `.large` could never suffer.
                    .onChange(of: browseHeight) { _, _ in
                        if !atMinStop { sheetDetent = browseDetent }
                    }
                    // Commit the smallest stop's height once the layout has
                    // stopped moving, never mid-animation. Each new value
                    // cancels the pending commit, so a 0.2 s row expansion
                    // resizes the sheet ONCE — at the end — instead of on every
                    // frame of it.
                    //
                    // The commit also lands outside the layout pass by
                    // construction, which is the other half of what has been
                    // going wrong here: a height written while layout is being
                    // resolved forces the pass to start over.
                    .onChange(of: desiredMinSheetHeight, initial: true) { _, wanted in
                        // CANCEL BEFORE THE EQUALITY CHECK, not after. Guarding
                        // first leaves a pending commit alive: go 200 -> 240 ->
                        // back to 200 inside the window and the early return
                        // skips the cancel, so the sheet lands on 240 and
                        // nothing ever fires again to correct it.
                        minHeightCommit?.cancel()
                        guard wanted != committedMinHeight else { return }
                        minHeightCommit = Task { @MainActor in
                            try? await Task.sleep(for: .milliseconds(140))
                            guard !Task.isCancelled else { return }
                            committedMinHeight = wanted
                        }
                    }
                    .onDisappear { minHeightCommit?.cancel() }
                    .presentationDragIndicator(.visible)
                    // The map is visible at BOTH stops, so it stays interactive
                    // (and undimmed) at both — including panning the visible
                    // strip above a browse-height list. Unconditional, not
                    // `upThrough: browseDetent`: that form undims by HEIGHT,
                    // and a recording dashboard at a large text size can
                    // legitimately outgrow the browse stop — which would have
                    // dimmed and deadened the map for the whole hike.
                    .presentationBackgroundInteraction(.enabled)
                    .presentationContentInteraction(.scrolls)
                    .presentationCornerRadius(20)
                    // Opaque system background at EVERY detent. By default the
                    // sheet is translucent (glass) at non-large detents, which
                    // read as "glass on glass" over the map; this forces the
                    // solid surface everywhere.
                    //
                    // THE `.ignoresSafeArea()` IS THE POINT, and its absence is
                    // the bottom gap this screen has had for six builds.
                    //
                    // `presentationBackground` REPLACES the sheet's own
                    // background view with this one. The shape-style form gets
                    // laid out inside the sheet's safe area, so the fill stopped
                    // one home indicator short of the screen and you saw
                    // straight through the sheet to whatever was behind it.
                    //
                    // The proof is the user's own black bar. #560 painted a
                    // black rectangle on the map underneath the sheet; the strip
                    // that had been showing MAP then showed BLACK. A hole that
                    // changes colour with what is behind it is a hole in the
                    // background, not a short frame — and every fix before this
                    // one worked on the sheet's CONTENT, which was never where
                    // the hole was.
                    //
                    // Corroboration in-repo: this is the only sheet in the app
                    // that overrides `presentationBackground`, and the only one
                    // ever reported as stopping short. TrailDetailSheet,
                    // HomeView's and AreaCompletionView's sheets all take the
                    // default full-bleed background and reach the bottom.
                    .presentationBackground {
                        Color(.systemBackground).ignoresSafeArea()
                    }
                    .interactiveDismissDisabled()
            }
        }
        .navigationBarHidden(true)
        .overlay(alignment: .top) {
            HStack {
                Button {
                    ActivityLogService.shared.log(
                        category: "area",
                        action: "closed",
                        context: ["areaId": areaId]
                    )
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .compatibleGlass(in: .circle)
                }
                .accessibilityIdentifier("area-close-button")
                Spacer()
                // Area-level overflow menu. Currently hosts only
                // "Export All Trails as GPX" but the chrome is
                // sized to grow as more area-wide actions land
                // (Stats / heatmap export, area download, etc.).
                Menu {
                    Picker("Map Style", selection: $mapStyle) {
                        ForEach(MapStylePreference.allCases) { style in
                            Text(style.label).tag(style)
                        }
                    }
                    // Parking is normally gated to the selected trail (#429) so
                    // a busy map stays readable. That gate also makes an area's
                    // parking undiscoverable: Helena-Lewis and Clark NF has 31
                    // lots, but only 16% of its trails have one within the 805 m
                    // endpoint radius, so browsing trail by trail reads as "no
                    // parking here". This answers the area-level question
                    // without loosening the per-trail association.
                    Toggle(isOn: $showAllParking) {
                        Label("Show All Parking", systemImage: "parkingsign.circle")
                    }
                    .disabled((area?.parking?.isEmpty ?? true))
                    Divider()
                    // Selection-aware GPX export: with a trail selected in the
                    // list, export just that one trail (small, what people
                    // actually want); otherwise export the whole area as a
                    // multi-track GPX (the bulk/backup path).
                    if let area, let tid = selectedTrailId,
                       let trail = area.trails.first(where: { $0.id == tid }) {
                        Button {
                            exportTrailGpx(trail)
                        } label: {
                            Label("Export \u{201C}\(trail.name)\u{201D} as GPX",
                                  systemImage: "square.and.arrow.up")
                        }
                        Button {
                            reportingTrail = trail
                        } label: {
                            Label("Report a problem with this trail",
                                  systemImage: "exclamationmark.bubble")
                        }
                    } else {
                        Button {
                            exportAreaGpx()
                        } label: {
                            Label("Export All Trails as GPX", systemImage: "square.and.arrow.up")
                        }
                        .disabled(area == nil)
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .compatibleGlass(in: .circle)
                }
                Button {
                    Task { await favorites.toggle(areaId: areaId) }
                } label: {
                    Image(systemName: favorites.isFavorite(areaId) ? "heart.fill" : "heart")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .compatibleGlass(in: .circle)
                        .foregroundStyle(favorites.isFavorite(areaId) ? .red : .primary)
                }
                .accessibilityLabel(
                    favorites.isFavorite(areaId) ? "Remove from Saved Areas" : "Save Area"
                )
            }
            .padding(.horizontal, 20)
            .padding(.top, 8)
            .safeAreaPadding(.top)
        }
        // Keyed on `loadAttempt` so the Try Again button can re-run the whole
        // load. Before this the area loaded exactly once and a failure was a
        // dead end — the only exit was closing and reopening the screen.
        .task(id: loadAttempt) {
            isLoading = true
            loadError = nil
            // Telemetry: log "user opened this area" so we can later
            // surface "you haven't visited X in a while" reminders.
            activity.recordAreaOpened(areaId)
            ActivityLogService.shared.log(
                category: "area",
                action: "opened",
                context: ["areaId": areaId]
            )
            AnalyticsService.shared.capture(.areaOpened(areaId: areaId))
            let result = await areas.areaWithError(id: areaId)
            area = result.area
            loadError = result.error
            isLoading = false
            if let loadedArea = result.area {
                // Backfill the completion fingerprint index from this area's
                // geometry, so progress recorded here credits a duplicate-area
                // twin, and a twin's progress credits here. Cheap + idempotent.
                progress.indexArea(areaId: areaId, trails: loadedArea.trails)
                log.notice("areaOpened areaId=\(self.areaId, privacy: .public) trails=\(loadedArea.trails.count) rawTrails=\(loadedArea.rawTrails?.count ?? 0) parking=\(loadedArea.parking?.count ?? -1)")
            } else if let err = result.error {
                log.error("areaOpenFailed areaId=\(self.areaId, privacy: .public) error=\(err, privacy: .public)")
            }
            await loadHistoryDerivedState()
            // Pop the celebration overlay if the view was opened via a
            // trail-complete push notification. Done after the area loads
            // so the overlay sits over the map, not a spinner.
            if let name = initialCelebrationTrailName {
                showCelebration(name: name)
            }
            // Trail-search/notification deep link: resolve against the trails
            // the area actually loaded. Exact ID+name wins; a unique exact name
            // safely bridges a raw-ID/canonical-ID mismatch; ambiguous data
            // leaves selection nil. Only run on first load so a user's own
            // subsequent selection isn't overridden.
            if let requestedId = initialSelectedTrailId,
               selectedTrailId == nil,
               let loadedArea = result.area {
                selectedTrailId = TrailSelectionResolver.resolve(
                    requestedRawTrailId: requestedId,
                    requestedTrailName: initialSelectedTrailName,
                    candidates: loadedArea.trails.map {
                        TrailSelectionCandidate(id: $0.id, name: $0.name)
                    }
                )
            }
        }
        .task(id: areaId) {
            // Floor the loading view at 1.5 s so the silhouette reveal
            // animation always completes — even when the area's data is
            // already on disk and lands in microseconds.
            minLoadingTimeElapsed = false
            try? await Task.sleep(for: .seconds(1.5))
            minLoadingTimeElapsed = true
        }
        .task(id: isRecording) {
            // While a recording is active for this area, recompute coverage
            // every 30s so partial progress visibly fills in (trail-list
            // progress bars tick up, trails crossing 90% turn cyan live).
            guard isRecording else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled, isRecording, let area else { break }
                // Coverage measurement uses the raw (pre-decimation)
                // trail node set when available — decimation drops the
                // node-count denominator in the fraction calc and
                // inflates coverage, so prefer rawTrails here.
                await recording.applyLiveCoverage(trails: area.rawTrails ?? area.trails)
            }
        }
        .overlay {
            if let name = celebrationTrailName {
                trailCompletionOverlay(name: name)
                    .transition(.scale(scale: 0.8).combined(with: .opacity))
            }
        }
        .onChange(of: filterKey, initial: true) { _, _ in
            // filterKey embeds `area?.id`, so a nil→loaded area
            // transition recomputes too — no separate onChange needed
            // for the load.
            recomputeFiltered()
        }
        .onChange(of: areaCompletionsCount) { _, _ in
            // Status filter depends on per-trail completion; refresh
            // when the user completes a trail in this area.
            recomputeFiltered()
        }
        .onChange(of: filteredCompletedCount) { old, new in
            // Trigger the celebration when the area transitions into 100%.
            // Suppress while the recording summary is up — the trophy state
            // there is enough acknowledgement, and stacking sheets is messy.
            guard let area, area.resolvedTrailCount > 0 else { return }
            let total = area.resolvedTrailCount
            if old < total && new >= total && !showSummary {
                showAreaComplete = true
            }
        }
        .onChange(of: selectedTrailId) { old, new in
            handleSelectionHandoff(from: old, to: new)
        }
    }

    /// The list → map handoff. Tapping a trail is a question about WHERE it
    /// is, and the answer is on the map — so selecting from the browse stop
    /// drops the sheet to the fit stop (the whole card stays visible) and the
    /// camera frames the trail in the map area that opens up. Deselecting
    /// returns the sheet to browse if that drop is why it is at fit, so a
    /// peek-at-the-map never costs the user their place in the list.
    ///
    /// Mid-hike the dashboard owns the fit stop and the card would not be
    /// visible there, so a selection leaves the sheet alone: the map strip
    /// above browse still frames the trail, and the floating retarget banner
    /// answers the tap.
    private func handleSelectionHandoff(from old: String?, to new: String?) {
        if new == nil {
            // EVERY deselect consumes the flag — including one made mid-hike
            // by dismissing the retarget banner — so a drop made from browse
            // before a recording can never raise the sheet unprompted after
            // an unrelated select/deselect later.
            let shouldReturn = collapsedForSelection && !isRecording && atMinStop
            collapsedForSelection = false
            if shouldReturn {
                // The sheet grows to browse around the list, so the user's
                // place in it is kept: no scroll-to-top tick.
                sheetDetent = browseDetent
            } else {
                // Staying at fit: the card just shrank under a fixed offset,
                // so snap the list back to a whole first row.
                scrollListToTopTick &+= 1
            }
            return
        }
        guard !isRecording, old == nil, sheetDetent == browseDetent else { return }
        collapsedForSelection = true
        // Commit the selected-card fit NOW and select it in the same update,
        // so the sheet animates straight to the card's height instead of
        // dropping to the old fit and rising again 140 ms later when the
        // debounced commit would have landed.
        minHeightCommit?.cancel()
        committedMinHeight = desiredMinSheetHeight
        sheetDetent = .height(committedMinHeight)
    }

    /// Re-frame the selected trail once the sheet's motion has landed. One
    /// pending re-frame at a time: a selection from browse changes the detent
    /// twice within ~350 ms (the drop, then the measured card's re-point), and
    /// without the cancel the camera visibly moved in stages.
    private func scheduleSelectedTrailRefit() {
        selectedTrailRefit?.cancel()
        selectedTrailRefit = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(450))
            guard !Task.isCancelled else { return }
            fitSelectedTrailTick &+= 1
        }
    }

    /// Binding that gates the trail-list sheet's presentation on the
    /// area being loaded. Read-only — `interactiveDismissDisabled`
    /// blocks user-initiated dismissal so the setter is a no-op.
    private var trailSheetPresented: Binding<Bool> {
        Binding(
            get: { self.area != nil && self.minLoadingTimeElapsed },
            set: { _ in }
        )
    }

    /// The `bottomInset` we pass to TrailMapView. Computed from the
    /// trail-list sheet's currently-settled detent, so the map's
    /// user-dot shift always clears the visible sheet area. Detent
    /// transitions are coarse (one event per release), so this only
    /// changes a handful of times per session — no per-frame thrash.
    ///
    /// Heights resolved against UIScreen rather than threading a
    /// GeometryReader value up. iPad multitasking would skew this,
    /// but the app's iPhone-only, so close enough.
    /// Top safe-area inset of the active window (Dynamic Island / notch).
    /// Needed because a sheet's `.fraction` detents are a fraction of the
    /// sheet's MAXIMUM height, which is the screen minus this inset — not a
    /// fraction of the whole screen.
    private static var topSafeInset: CGFloat {
        (UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first?.keyWindow?.safeAreaInsets.top) ?? 47
    }

    /// Bottom safe-area inset (home indicator).
    static var bottomSafeInset: CGFloat {
        (UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first?.keyWindow?.safeAreaInsets.bottom) ?? 34
    }

    /// What UIKit would treat as the sheet's maximum height — the reference
    /// the browse stop is a fraction of. Never offered as a stop itself.
    private var maxDetentHeight: CGFloat {
        UIScreen.main.bounds.height - Self.topSafeInset - 10
    }

    /// The browse stop's `.height()` value. Its visible extent (this plus the
    /// home-indicator band) is ~66% of the sheet's maximum, which leaves the
    /// top ~40% of every supported iPhone as map. A per-device constant, so
    /// comparing a detent against `browseDetent` is stable — unlike the fit
    /// stop, whose height is measured.
    private var browseHeight: CGFloat {
        ((maxDetentHeight * 0.66 - Self.bottomSafeInset) / 4).rounded(.down) * 4
    }

    private var browseDetent: PresentationDetent { .height(browseHeight) }

    /// The FIT stop: precisely as tall as the current context's whole content,
    /// never taller.
    ///
    ///   - recording → the complete live dashboard (camera controls, panel).
    ///     The trail list waits below, revealed by dragging to browse.
    ///   - trail selected → the ENTIRE expanded card — name, stats, elevation
    ///     chart, parking line — so selecting a trail nudges the sheet up by
    ///     exactly what the card needs, whatever its size.
    ///   - browsing → the action toolbar plus three whole rows, the third row
    ///     being the cue that the list continues.
    ///
    /// The terms are measurements, but everything they measure is now FIXED
    /// BY DESIGN for the duration of its context: the dashboard's slots are
    /// permanently reserved, the retarget banner floats over the map instead
    /// of joining this flow, the toast is an overlay, and the list's
    /// search/filter chrome exists only at browse. Nothing at the fit stop can
    /// arrive late and resize it — which retires the whole clipping class the
    /// measure-and-chase versions of this property produced. The band
    /// subtraction pays for the home-indicator strip; `fitBottomAir` leaves a
    /// visible breath between the last row and the physical edge.
    private var desiredMinSheetHeight: CGFloat {
        let h: CGFloat
        let ceiling: CGFloat
        if isRecording {
            h = headerHeightFull + recordingHeight - Self.bottomSafeInset
                + Self.fitBottomAir
            // The dashboard is pinned above the list and CANNOT scroll, so its
            // ceiling must be one it can always meet — a small phone at a
            // large text size needs more than the browse-based cap allows.
            // Clipping the stop button off the bottom is the worse failure.
            ceiling = maxDetentHeight * 0.85
        } else if selectedTrailId != nil {
            let card = max(selectedRowHeight, collapsedRowHeight * 3)
            h = headerHeightFull + idleToolbarHeight + card
                - Self.bottomSafeInset + Self.fitBottomAir
            ceiling = browseHeight - 40
        } else {
            h = headerHeightFull + idleToolbarHeight + collapsedRowHeight * 3
                - Self.bottomSafeInset + Self.fitBottomAir
            ceiling = browseHeight - 40
        }

        // Floor guards a nonsense measurement. The browse-based ceiling keeps
        // the fit stop a clear step below browse — so the two stops never blur
        // together and the map's strip is never smaller than at browse, even
        // under the tallest selected card. A card taller than the ceiling
        // scrolls within the sheet rather than eating the map.
        let clamped = min(max(h, 140), ceiling)
        return (clamped / 4).rounded(.up) * 4
    }

    /// The height the smallest stop is actually PINNED at — `desiredMin`
    /// once it has stopped moving.
    ///
    /// Everything that sizes the sheet reads this and not the computed value,
    /// because the computed one moves on every frame of an animation. Tapping a
    /// trail expands its row from ~62pt to ~240pt over 0.2 s, and each frame of
    /// that is a different `.height()` detent: about a dozen `presentationDetents`
    /// mutations, each re-pointing the selection, while UIKit is already
    /// animating the sheet. The same is true of the header standing down and the
    /// search bar leaving.
    ///
    /// A deadband cannot help here — the frames are genuinely far apart, so
    /// every one of them clears any threshold worth having. What is wrong is
    /// asking the sheet to resize DURING an animation at all. It settles once,
    /// afterwards, which is the only moment the number means anything.
    private var minSheetHeight: CGFloat { committedMinHeight }

    private var minDetent: PresentationDetent { .height(minSheetHeight) }

    /// Exactly two stops, always, and neither covers the map. The fit stop is
    /// capped 40pt below browse, so the two can never blur together — which is
    /// how the old middle detent sometimes vanished and made the sheet's stop
    /// count unpredictable.
    private var sheetDetentSet: Set<PresentationDetent> {
        [minDetent, browseDetent]
    }

    /// Where the sheet's top edge is, in points up from the physical screen
    /// bottom. Its ONE job is the map's user-dot shift, so the dot clears the
    /// visible sheet.
    ///
    /// **Measured, not modelled.** Every earlier version computed this from
    /// what a detent was believed to mean — whether `.height(x)` includes the
    /// home indicator, and similar per-stop guesses, each wrong by a different
    /// amount. The sheet's own content reports where it actually starts
    /// (`measuredSheetTop`), so every stop is right for one reason.
    ///
    /// The computed values stay as the seed for the first frame, before the
    /// sheet has laid out and had a chance to say.
    private var effectiveBottomInset: CGFloat {
        if let measured = measuredSheetTop { return measured }
        if sheetDetent == browseDetent { return browseHeight + Self.bottomSafeInset }
        return minSheetHeight + Self.bottomSafeInset
    }

    /// Loading state. Paints the bundled silhouette so the wait feels
    /// like the screen has already arrived. After the 2 s reveal
    /// completes, the trails wave gently in place until real area data
    /// lands. Plain spinner fallback only when no silhouette is bundled.
    @ViewBuilder
    private var loadingState: some View {
        ZStack {
            Color(.secondarySystemBackground)
                .ignoresSafeArea()
            if let silhouette = silhouettes.cachedSilhouette(for: areaId) {
                LoadingSilhouetteCanvas(silhouette: silhouette)
                    .ignoresSafeArea()
            } else {
                ProgressView()
            }
        }
        // Kick the R2 fetch so the loading-state silhouette
        // shows up if we don't already have it cached. Most of
        // the time HomeView.prefetchVisibleAreas + AreaCard's
        // own `.task` will have populated this before the user
        // navigates in, but this is the safety net.
        .task(id: areaId) {
            await silhouettes.silhouette(for: areaId)
        }
    }

    /// Show the trail-completion celebration overlay for `name` and auto-
    /// dismiss after 3.5s. Tapping the overlay dismisses it sooner.
    private func showCelebration(name: String) {
        log.notice("trailCompletion areaId=\(self.areaId, privacy: .public) trail=\(name, privacy: .public)")
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        withAnimation(.spring(response: 0.45, dampingFraction: 0.7)) {
            celebrationTrailName = name
        }
        Task {
            try? await Task.sleep(for: .seconds(3.5))
            await MainActor.run {
                withAnimation(.easeOut(duration: 0.25)) {
                    if celebrationTrailName == name { celebrationTrailName = nil }
                }
            }
        }
    }

    private func trailCompletionOverlay(name: String) -> some View {
        ZStack {
            Color.black.opacity(0.35).ignoresSafeArea()
            VStack(spacing: 14) {
                Image(systemName: "checkmark.seal.fill")
                    .font(.system(size: 84))
                    .foregroundStyle(.cyan)
                    .symbolEffect(.bounce, options: .repeat(2))
                Text("Trail Complete!")
                    .font(.title.bold())
                Text(name)
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(36)
            .frame(maxWidth: 320)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .padding(24)
        }
        .contentShape(Rectangle())
        .onTapGesture {
            withAnimation(.easeOut(duration: 0.2)) { celebrationTrailName = nil }
        }
    }

    /// Completion count restricted to the current area's trail IDs so orphan
    /// completions (from before the trail-id determinism fix) don't inflate
    /// the celebration trigger or any header counters that reference it.
    private var filteredCompletedCount: Int {
        guard area != nil else { return 0 }
        // areaTrailIds is the cached Set from `recomputeFiltered()`;
        // see `@State areaTrailIds` for why this isn't built inline.
        return progress.completionCount(in: areaId, trails: area?.trails ?? [])
    }

    private func loadPastPaths() async {
        let history = await recording.loadHistory()
        pastHikes = makePastHikes(from: history)
    }

    /// Build the in-memory `PastHike` list from on-disk recordings,
    /// scoped to this area. Shared by `loadPastPaths` (halo-only
    /// refresh after a recording finishes) and
    /// `loadHistoryDerivedState` (full coverage replay on area open).
    private func makePastHikes(from history: [SavedRecording]) -> [PastHike] {
        // touchedAreaIds: a walk that credited trails here renders its
        // GPS track as a cyan halo just like a hike recorded in-area.
        // areaAndTwins: a hike recorded under a now-hidden duplicate twin
        // (docs/adr/0002) draws its trace on this canonical area too.
        let areas = AreaDataService.shared.areaAndTwins(areaId)
        return history
            .filter { !$0.touchedAreaIds.isDisjoint(with: areas) }
            .map { PastHike(path: $0.path, startedAt: $0.startedAt) }
    }

    /// Trail the retarget banner should offer to switch to, or nil
    /// if no banner should render. Nil when there's no recording,
    /// no selected trail, the selected trail IS the recording
    /// trail (no-op), or the selection points at a trail not in
    /// this area's list (defensive — shouldn't happen, but cheap
    /// to guard).
    ///
    /// Roam-mode recordings DO get the banner: tapping a trail on
    /// the map while recording roam-style offers to convert the
    /// recording to a trail-mode recording targeted at the tapped
    /// trail. Build 12's `RecordingService.retargeted` already
    /// handles the roam→trail conversion path; this used to be
    /// gated on `mode == .trail` here, which was the build-12
    /// device-test bug.
    private func retargetCandidate(area: Area) -> Trail? {
        guard let activeRec = recording.activeRecording,
              let selectedId = selectedTrailId,
              selectedId != activeRec.trailId
        else { return nil }
        return area.trails.first(where: { $0.id == selectedId })
    }


    /// Pull recorded hike history once and use it for both:
    ///   - the cyan coverage halo (`pastHikes` → path slice)
    ///   - canonical completions, replayed from saved GPS paths against the
    ///     current trails. This self-heals after a re-fetch that changed
    ///     trail IDs: even if `completedTrailIds` in history points at a
    ///     stale id, replaying the path against the new trails reproduces
    ///     the right coverage and re-marks completion under the new id.
    /// Manual toggles via the trail-row checkbox still live in ProgressService
    /// and union with history-derived completions.
    private func loadHistoryDerivedState() async {
        let history = await recording.loadHistory()
        // touchedAreaIds + the walk-aware accessor: walks credited in
        // this area count exactly like local hikes.
        let local = history.filter { $0.touchedAreaIds.contains(areaId) }
        pastHikes = makePastHikes(from: history)
        // Carry forward any completedTrailIds whose ids still match — cheap
        // path that doesn't need to walk the GPS grid. The path-replay below
        // covers the case where ids changed.
        let stillValid = Set(area?.trails.map(\.id) ?? [])
        let completed = Set(local.flatMap { $0.completedTrailIds(in: areaId) }).intersection(stillValid)
        progress.bulkMarkComplete(areaId: areaId, trailIds: completed)
        if let trails = area?.trails {
            await recording.rebuildCoverageFromHistory(areaId: areaId, trails: trails)
        }
    }

    /// Pre-flight gate before kicking off a hike. Walks through the
    /// permission, conflict, and distance checks in order and either
    /// starts immediately or surfaces the appropriate confirmation.
    /// Pass a trailId to start in `.trail` mode (history will label the
    /// hike with that trail's name and TrailMapView lights it up as a
    /// Build a single multi-track GPX of every trail in the area
    /// and present the share sheet. Loaded into Garmin Connect
    /// the file produces one course per trail — useful for
    /// planning a multi-trail visit. No-op when the area hasn't
    /// finished loading.
    private func exportAreaGpx() {
        guard let area else { return }
        ActivityLogService.shared.log(
            category: "trail",
            action: "exportAreaGpx",
            context: ["areaId": areaId]
        )
        do {
            let url = try GpxExport.temporaryFile(area: area)
            areaGpxShareURL = IdentifiedURL(url: url)
        } catch {
            exportFailure = ExportFailure.message(for: error,
                                                  what: "this park's trails")
        }
    }

    /// Single-trail GPX (the selected trail) — small, one clean track, the
    /// common "load this hike onto my watch" case. Shares the same share-sheet
    /// state as the area export.
    private func exportTrailGpx(_ trail: Trail) {
        ActivityLogService.shared.log(
            category: "trail",
            action: "exportTrailGpx",
            context: ["areaId": areaId, "trailId": trail.id]
        )
        do {
            let url = try GpxExport.temporaryFile(trail: trail, areaName: area?.name)
            areaGpxShareURL = IdentifiedURL(url: url)
        } catch {
            exportFailure = ExportFailure.message(for: error,
                                                  what: "\u{201C}\(trail.name)\u{201D}")
        }
    }

    /// purple stroke). The trailId is preserved through the conflict /
    /// far-warning dialogs via `pendingRecordTrailId`.
    private func tryStartRecording(trailId: String? = nil) {
        guard let area else { return }
        if !location.isAuthorized {
            location.requestPermission()
            return
        }

        // A recording ALREADY RUNNING IN THIS AREA is not a conflict — it is a
        // change of mind about which trail you are on. Retarget it and keep
        // every metre already walked.
        //
        // This case used to fall straight through to `startRecordingNow`, which
        // overwrote `activeRecording` and with it the whole GPS path. On
        // 2026-08-16 that discarded 25 minutes and 457 fixes of a real hike:
        // recording Mormon Loop, tapped a different trail's record button, and
        // the hike was gone. The preflight below only ever treated a DIFFERENT
        // area as a conflict, so same-area-different-trail had nothing standing
        // in front of it.
        //
        // Retargeting is what the "Switch active trail" banner already does, and
        // it is unambiguously what tapping Record on another trail means: I am
        // on THIS trail now. Nothing is thrown away, so nothing needs asking.
        if let active = recording.activeRecording,
           active.areaId == areaId, active.mode != .walk {
            if let trailId, trailId != active.trailId,
               recording.retargetTrail(trailId) {
                ActivityLogService.shared.log(
                    category: "recording",
                    action: "retarget",
                    context: ["source": "recordButton", "trailId": trailId]
                )
                selectedTrailId = trailId
                centerOnSwitchedTrailTick &+= 1
                showTrackingModeToast("Now tracking this trail")
            }
            // Same trail, or the roam button while already recording: nothing
            // to do. Certainly not a restart.
            return
        }

        // Item 1 — concurrent recording prevention. An active WALK
        // always conflicts, even when its primary area happens to be
        // this one: starting an area recording here would silently
        // replace the walk and throw away its multi-area credit scope.
        if let active = recording.activeRecording,
           active.areaId != areaId || active.mode == .walk {
            pendingRecordTrailId = trailId
            conflictAreaName = active.mode == .walk
                ? "your walk"
                : areas.cachedArea(id: active.areaId)?.name
                    ?? areas.summaries.first { $0.id == active.areaId }?.name
                    ?? "another area"
            showConflictAlert = true
            return
        }

        // The "you're N miles from this area" confirmation is GONE. It stood
        // between the user and starting a hike on the strength of a distance to
        // the area's CENTRE — which is miles from the trailhead in any large
        // park — and its answer was always Start Anyway. A dialog everyone
        // dismisses is not a safeguard, it is a tax on the common case.

        startRecordingNow(trailId: trailId)
    }

    /// Actually start the recording. Used both directly from
    /// `tryStartRecording` (no preflight conflicts) and from the dialog
    /// "proceed" buttons after preflight resolves.
    private func startRecordingNow(trailId: String?) {
        let mode: RecordingMode = trailId == nil ? .roam : .trail
        let result = recording.startRecording(areaId: areaId, mode: mode, trailId: trailId)
        guard result == .started else {
            pendingRecordTrailId = trailId
            conflictAreaName = recording.activeRecording?.mode == .walk
                ? "your walk"
                : "another hike"
            showConflictAlert = true
            return
        }
        // Mirror the trail-row tap flow exactly (direct assignment, no
        // withAnimation wrapper) so TrailMapView's existing selected-trail
        // styling kicks in on top of the purple recording-trail render.
        if let trailId {
            selectedTrailId = trailId
        }
    }

    private func stopOtherRecordingThenStart(trailId: String?) async {
        guard let active = recording.activeRecording, !recording.isStopping else { return }
        do {
            // A walk stops through the multi-area path so every nearby area
            // gets its coverage credit before this area's recording starts.
            if active.mode == .walk {
                var trailsByArea: [String: [Trail]] = [:]
                for aid in active.nearbyAreaIds ?? [active.areaId] {
                    // if/else, not `??` — its autoclosure can't host an await.
                    let a: Area?
                    if let cached = areas.cachedArea(id: aid) {
                        a = cached
                    } else {
                        a = await areas.area(id: aid)
                    }
                    if let a {
                        trailsByArea[aid] = a.rawTrails ?? a.trails
                    }
                }
                guard try await recording.stopWalk(trailsByArea: trailsByArea) != nil else { return }
            } else {
                // Split out of `??` because `??` takes an autoclosure that can't
                // host an `await`.
                let trails: [Trail]
                // Prefer raw trails for stopRecording so coverage finalization
                // uses the dense node set — see the live-coverage call above.
                if let cached = areas.cachedArea(id: active.areaId) {
                    trails = cached.rawTrails ?? cached.trails
                } else {
                    let loaded = await areas.area(id: active.areaId)
                    trails = loaded?.rawTrails ?? loaded?.trails ?? []
                }
                guard try await recording.stopRecording(trails: trails) != nil else { return }
            }
            pendingRecordTrailId = nil
            recordingSaveFailureMessage = nil
            startRecordingNow(trailId: trailId)
        } catch {
            // Keep the requested destination so Retry Save can finish the old
            // recording and only then start here.
            pendingRecordTrailId = trailId
            recordingSaveFailureMessage = error.localizedDescription
        }
    }

    /// Content of the trail-list sheet (always-presented, native
    /// `UISheetPresentationController` via `.sheet` / `.presentation
    /// Detents`). Top-to-bottom:
    ///
    ///   1. (system drag indicator — rendered by SwiftUI at the top
    ///      edge of the sheet via `.presentationDragIndicator(.visible)`)
    ///   2. Area-name headline
    ///   3. Tracking-mode toast capsule (transient)
    ///   4. Control bar (map-style picker, recenter, tracking cycle)
    ///   5. Retarget / suggestion banner (only while recording)
    ///   6. RecordingPanel (only while recording)
    ///   7. TrailListView (fills remaining; scrolls within at the
    ///      browse stop)
    ///
    /// All nested modal flows (GPX share, recording summary, area-
    /// completion celebration) live INSIDE this content so SwiftUI
    /// can present them over the always-on trail-list sheet — you
    /// can only have one `.sheet` modifier active per ancestor view,
    /// so attaching them at AreaView's body would conflict with the
    /// trail-list sheet itself.
    /// One-line area summary for the sheet header: trails · total
    /// distance (honors the units toggle via UnitFormatter) · completion.
    private func areaSummaryLine(area: Area, completed: Int) -> String {
        var parts = [
            "\(area.resolvedTrailCount) trails",
            UnitFormatter.distance(miles: area.resolvedTotalMi, units: units),
        ]
        if area.resolvedTrailCount > 0 {
            parts.append("\(completed) of \(area.resolvedTrailCount) completed")
        }
        return parts.joined(separator: " · ")
    }

    /// The live recording dashboard: camera controls, retarget banner, and the
    /// recording panel, measured as one block. It is pinned ABOVE the trail
    /// list rather than replacing it, so the fit stop shows exactly the
    /// dashboard while dragging to browse reveals the list underneath — the list
    /// stays reachable mid-hike, and the dashboard can never be swiped away.
    ///
    /// Measured LIVE, not high-water: the GPS capsule and elevation strip come
    /// and go, and the fit stop follows in both directions. The list below is
    /// flexible, so a grown panel briefly borrows from the (invisible at fit)
    /// list region instead of clipping while the 140 ms commit catches up —
    /// which is what let the old ScrollView + scroll-reset machinery retire.
    @ViewBuilder
    private func recordingDashboard(area: Area) -> some View {
        VStack(spacing: 0) {
            // Camera controls stay reachable mid-hike: the map is the thing you
            // are looking at while the dashboard runs.
            controlBar(area: area)
                .padding(.bottom, 10)

            // The retarget banner is NOT in this flow anymore — it floats over
            // the map (see the overlay in `body`), so a banner appearing can
            // never resize the dashboard the fit stop is sized from.
            RecordingPanel(area: area) { finished in
                finishedRecording = finished
                showSummary = finished != nil
                // Refresh the cyan coverage halo with the just-finished
                // hike's path.
                Task { await loadPastPaths() }
            }
            .padding(.bottom, 4)
            // No Spacer. One sat here and the content measured taller than
            // itself, leaving a band of empty sheet under the panel.
        }
        .padding(.top, 8)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { h in
            if abs(recordingHeight - h) >= 2 { recordingHeight = h }
        }
        // Names the tracking mode you just cycled into, for ~2 s. An overlay,
        // not flow content: a transient pill must not resize the fit stop.
        .overlay(alignment: .top) {
            if let toast = trackingModeToast {
                trackingToastPill(toast)
                    .padding(.top, 58)
            }
        }
    }

    /// The transient tracking-mode pill, shared by both contexts.
    private func trackingToastPill(_ label: String) -> some View {
        Text(label)
            .font(.caption.weight(.medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 14)
            .padding(.vertical, 7)
            .background(Color.accentColor, in: Capsule())
            .transition(.opacity.combined(with: .scale(scale: 0.9)))
            .allowsHitTesting(false)
    }

    /// The searchable, filterable trail list, rendered by both contexts.
    /// Filters, search text, and the selection are AreaView-owned bindings, so
    /// they survive a recording starting or ending around the list; the list's
    /// own scroll offset does not (the context branches give it fresh
    /// structural identity), and its on-appear scroll-to-selection covers that.
    @ViewBuilder
    private func trailList(area: Area) -> some View {
        TrailListView(
            area: area,
            selectedTrailId: $selectedTrailId,
            statusFilter: $statusFilter,
            difficultyFilter: $difficultyFilter,
            lengthFilter: $lengthFilter,
            routeFilter: $routeFilter,
            sort: $trailSort,
            searchQuery: $trailSearchQuery,
            filteredTrails: filtered,
            // Search and filters exist only at the browse stop. At fit there is
            // no text field for the keyboard to grab, so focusing search can
            // never yank the sheet — when you can type, you are already at browse.
            showsChrome: !atMinStop,
            focusSearchTick: searchFocusTick,
            scrollToTopTick: scrollListToTopTick,
            onRecordTrail: { trail in tryStartRecording(trailId: trail.id) },
            onCollapsedRowHeight: { h in
                if abs(collapsedRowHeight - h) >= 2 { collapsedRowHeight = h }
            },
            onSelectedRowHeight: { h in
                if abs(selectedRowHeight - h) >= 2 { selectedRowHeight = h }
            }
        )
    }

    /// The idle context's primary actions: start a hike and open the
    /// Collection. Explicit and labeled — both used to be pages behind an
    /// unlabeled horizontal swipe, which is why neither was discoverable.
    /// The Start button states what it will record so there is no way to
    /// press it and be surprised.
    @ViewBuilder
    private func areaActionRow(area: Area) -> some View {
        let selected = selectedTrailId.flatMap { id in area.trails.first { $0.id == id } }
        HStack(spacing: 10) {
            Button {
                tryStartRecording(trailId: selected?.id)
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "record.circle")
                        .font(.body.weight(.semibold))
                    Text(selected == nil ? "Start a Hike" : "Record This Trail")
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .compatibleGlass(in: .capsule)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("area-record-button")
            .accessibilityLabel(selected.map { "Record \($0.name)" } ?? "Start a hike")

            // Search lives at the browse stop; this button is the intentional
            // way there — expand, then focus once the chrome has mounted.
            // While a search or filter is ACTIVE, the icon swaps to a filled
            // filter glyph in the accent color: the fit stop hides the chrome,
            // so this constant-size badge is how a filtered 3-row list says
            // it's filtered.
            Button {
                sheetDetent = browseDetent
                Task { @MainActor in
                    try? await Task.sleep(for: .milliseconds(400))
                    searchFocusTick &+= 1
                }
            } label: {
                Image(systemName: hasActiveFilter
                      ? "line.3.horizontal.decrease.circle.fill"
                      : "magnifyingglass")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(hasActiveFilter ? Color.accentColor : .primary)
                    .padding(.horizontal, 13)
                    .padding(.vertical, 12)
                    .compatibleGlass(in: .capsule)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("area-search-button")
            .accessibilityLabel(hasActiveFilter
                                ? "Search and filters — filters active"
                                : "Search trails")

            Button {
                showCollection = true
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "trophy")
                        .font(.body.weight(.semibold))
                    Text("Collection")
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .compatibleGlass(in: .capsule)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("area-collection-button")
            .accessibilityLabel("Collection — badges earned in this area")
        }
        .padding(.horizontal, 20)
    }

    @ViewBuilder
    private func sheetContent(area: Area) -> some View {
        VStack(spacing: 0) {
            // Title + summary block — centered under the drag
            // indicator, reads as one header. The trail count /
            // completion line used to live in TrailListView's own
            // summary header; with the area name now sitting above
            // it, the two read as redundant stacked headers — moved
            // up here so the user gets the whole "where am I, what's
            // here" pitch in one block.
            VStack(spacing: 4) {
                // The name and summary NEVER stand down. They used to hide at
                // the smallest stop while a trail was selected, to buy the
                // expanded row ~40pt — and the audit photographed the price
                // (sheet-06-min-trail-deselected, run 32203094649): the header
                // growing back on deselect while the sheet resized under it
                // left the search field tucked under the divider. A header
                // with one height cannot do that, and hiding the park's name
                // was the same "control that vanishes" mistake as the search
                // bar's — twice reported as breakage, never read as intent.
                Text(areaName)
                    .font(.title3.weight(.semibold))
                    .lineLimit(1)

                // Single summary line: trails · total distance (unit-aware)
                // · completion. `areaTrailIds` is the cached Set from
                // recomputeFiltered() — avoids a per-eval O(N) rebuild.
                // Whole line turns green at 100% as an area-complete cue.
                let completed = progress.completionCount(in: area.id, trails: area.trails)
                let areaComplete = area.resolvedTrailCount > 0 && completed >= area.resolvedTrailCount
                Text(areaSummaryLine(area: area, completed: completed))
                    .font(.subheadline)
                    .foregroundStyle(areaComplete ? .green : .secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)

                // OSM attribution is NOT repeated here — the required
                // "© OpenStreetMap contributors" credit lives in
                // Settings → About (with the licence link), which
                // satisfies the ODbL. Keeping the header uncluttered.
            }
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.horizontal, 20)
            .padding(.top, 10)
            .padding(.bottom, 12)
            // Measured with a deadband like every other block the fit stop
            // must contain, so a sub-2pt wobble cannot thrash the detent.
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { h in
                if abs(headerHeightFull - h) >= 2 { headerHeightFull = h }
            }

            // The line the pages scroll under. The pager's top edge clips
            // whatever is scrolled past it, and with no rule there the cut
            // floats in black and reads as a defect. Every scrolling screen in
            // iOS marks this edge; ours was the only one that did not.
            Divider()

            // One trail list, always present. What sits ABOVE it is the
            // context: the live recording dashboard while a hike runs, or the
            // camera-controls + Start/Collection toolbar while browsing. The
            // fit stop sizes to that block, so mid-hike the list waits just
            // below the dashboard — drag up to browse it, and the
            // dashboard stays pinned where it cannot be swiped away.
            if isRecording {
                VStack(spacing: 0) {
                    recordingDashboard(area: area)
                    trailList(area: area)
                }
                .frame(maxHeight: .infinity, alignment: .top)
            } else {
                VStack(spacing: 0) {
                    // Camera controls plus the labeled Start/Collection row.
                    // Measured as one block so the fit stop always shows the
                    // whole toolbar — these actions were pages behind an
                    // undiscoverable horizontal swipe.
                    VStack(spacing: 10) {
                        controlBar(area: area)
                        areaActionRow(area: area)
                    }
                    .padding(.top, 8)
                    .padding(.bottom, 10)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { h in
                        if abs(idleToolbarHeight - h) >= 2 { idleToolbarHeight = h }
                    }

                    trailList(area: area)
                        // The tracking-mode toast floats over the list instead
                        // of living in the measured toolbar, so a 2 s pill
                        // cannot resize the sheet's fit stop twice per tap.
                        .overlay(alignment: .top) {
                            if let toast = trackingModeToast {
                                trackingToastPill(toast)
                                    .padding(.top, 6)
                            }
                        }
                }
                .frame(maxHeight: .infinity, alignment: .top)
            }
        }
        // Nested modal sheets — must live inside the always-on trail-
        // list sheet so SwiftUI lets them present on top instead of
        // conflicting with each other.
        .sheet(item: $areaGpxShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
        .exportFailureAlert($exportFailure)
        .sheet(item: $reportingTrail) { trail in
            ReportTrailView(trail: trail, areaId: areaId, areaName: areaName)
        }
        .sheet(isPresented: $showSummary) {
            if let finished = finishedRecording {
                RecordingSummarySheet(
                    finished: finished,
                    areaName: areaName,
                    trails: area.trails
                )
            }
        }
        .sheet(isPresented: $showAreaComplete) {
            AreaCompletionView(area: area)
                .presentationDetents([.large])
        }
        // Collection is an explicit secondary destination now, not a swipe
        // page. Presented over the always-on area sheet, so dismissing it
        // returns to the exact map camera, selection, and detent underneath.
        .sheet(isPresented: $showCollection) {
            NavigationStack {
                DexView(area: area)
                    .navigationTitle("Collection")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Done") { showCollection = false }
                        }
                    }
            }
            .presentationDetents([.large])
            .onAppear {
                AnalyticsService.shared.capture(.dexOpened(areaId: area.id))
            }
        }
        // Confirmation dialogs ALSO nest inside the sheet — same
        // one-presentation-per-ancestor rule that put the modal sheets
        // here. With them attached to AreaView's body, tapping "Record
        // Hike" set a dialog flag, SwiftUI tried to present
        // the dialog from AreaView, found the trail-list sheet already
        // owning that slot, and bounced — dismissing the sheet to
        // present the dialog, then re-presenting the sheet (because
        // its binding stays true), which clobbered the dialog. Net:
        // dialog flashed for ~0.1s then vanished, and recording could
        // never start.
        .confirmationDialog(
            "You're already recording at \(conflictAreaName)",
            isPresented: $showConflictAlert,
            titleVisibility: .visible
        ) {
            Button("Stop That Hike & Start Here", role: .destructive) {
                let trailId = pendingRecordTrailId
                Task { await stopOtherRecordingThenStart(trailId: trailId) }
            }
            Button("Cancel", role: .cancel) {
                pendingRecordTrailId = nil
            }
        } message: {
            Text("Starting a new hike here will save and end your hike at \(conflictAreaName).")
        }
        .alert(
            "Couldn't Save Current Recording",
            isPresented: Binding(
                get: { recordingSaveFailureMessage != nil },
                set: { if !$0 { recordingSaveFailureMessage = nil } }
            )
        ) {
            Button("Retry Save") {
                let trailId = pendingRecordTrailId
                Task { await stopOtherRecordingThenStart(trailId: trailId) }
            }
            Button("Keep Current Recording", role: .cancel) {
                pendingRecordTrailId = nil
            }
        } message: {
            Text(recordingSaveFailureMessage ?? "The current recording is still safe, location observation resumed, and no new hike was started.")
        }
    }

    /// Retarget vs suggestion banner, only shown while a recording is
    /// active. Extracted so `sheetContent` reads cleanly — the
    /// inline form has ~70 lines of logging / dismiss closures that
    /// dwarf the rest of the sheet layout.
    /// The retarget banner, floating over the MAP just above the sheet — not
    /// in the sheet's flow, where its arrival used to resize the dashboard the
    /// fit stop is sized from. The map is visible at both stops, so the banner
    /// rides the sheet's top edge at either; the selected row's record button
    /// at browse performs the same retarget through `tryStartRecording`.
    ///
    /// The heuristic SUGGESTION banner stays gone. This one only appears
    /// because you TAPPED a different trail, so it is answering a question you
    /// just asked rather than starting a conversation.
    @ViewBuilder
    private func floatingRetargetBanner(area: Area) -> some View {
        if isRecording, let retargetTrail = retargetCandidate(area: area) {
            RetargetTrailBanner(
                selectedTrail: retargetTrail,
                onSwitch: {
                    guard recording.retargetTrail(retargetTrail.id) else { return }
                    ActivityLogService.shared.log(
                        category: "recording",
                        action: "retarget",
                        context: ["source": "retargetBanner", "trailId": retargetTrail.id]
                    )
                    // Re-assigning selectedTrailId to its current
                    // value is a SwiftUI no-op, so bump
                    // centerOnSwitchedTrailTick separately to force
                    // TrailMapView to re-fit the camera around the
                    // user + the new active trail.
                    selectedTrailId = retargetTrail.id
                    centerOnSwitchedTrailTick &+= 1
                },
                onDismiss: { selectedTrailId = nil }
            )
            // No horizontal padding here — the banner carries its own 16pt.
            .padding(.bottom, effectiveBottomInset + 10)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    /// Show a brief "Following your direction" / similar pill above
    /// the controlBar so users learn what each tracking-mode icon
    /// means without us cluttering the UI with permanent labels.
    /// Cancels any in-flight dismiss timer so rapid cycle-taps don't
    /// fight each other.
    private func showTrackingModeToast(_ label: String) {
        trackingModeToastTask?.cancel()
        withAnimation(.spring(response: 0.3, dampingFraction: 0.75)) {
            trackingModeToast = label
        }
        trackingModeToastTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            await MainActor.run {
                withAnimation(.easeOut(duration: 0.3)) {
                    trackingModeToast = nil
                }
            }
        }
    }

    private func controlBar(area: Area) -> some View {
        HStack(spacing: 14) {
            // Camera tracking cycle — Apple Maps style. Cycles
            // free → follow → follow-with-heading → free. Icon
            // reflects the current mode. Toast under the controlBar
            // (rendered in body) names the new mode for ~2 s so users
            // learn the cycle without permanent on-screen labels.
            //
            // No glass on these in-sheet icon buttons: the sheet
            // itself is the glass surface, and glass-on-glass reads
            // muddy (Apple's HIG calls this out — Liquid Glass is a
            // single layer between content and surface). A subtle
            // adaptive fill gives a tappable affordance without
            // competing with the sheet material. The record button
            // below keeps its glass because it's the primary CTA and
            // is meant to stand proud.
            Button {
                if !location.isAuthorized { location.requestPermission(); return }
                trackingMode = trackingMode.next
                showTrackingModeToast(trackingMode.toastLabel)
            } label: {
                Image(systemName: trackingMode.symbol)
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .compatibleGlass(in: .circle)
            }
            .accessibilityLabel(trackingMode.accessibilityLabel)

            // Recenter on user — one-shot center. Doesn't engage
            // tracking; cycle button above is the way to opt into
            // continuous follow. Distinct viewfinder icon so it
            // doesn't collide with the cycle's `location.fill` state.
            Button {
                if !location.isAuthorized { location.requestPermission(); return }
                recenterTick &+= 1
            } label: {
                Image(systemName: "location.fill.viewfinder")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .compatibleGlass(in: .circle)
            }
            .accessibilityLabel("Recenter on my location")
            .accessibilityIdentifier("area-recenter-button")

            Spacer()

            // Map-camera controls only. Starting a hike and opening the
            // Collection live on the labeled action row rendered beneath this
            // bar in the idle context.
        }
        .padding(.horizontal, 20)
    }
}

/// Full-screen silhouette behind the AreaView loading state. Trails light
/// up one-by-one driven by elapsed time so the wait reads as the area
/// arriving instead of dead air. Long trails are drawn first (they carry
/// the most visual weight); then medium, then short. Total animation is
/// capped so a 200-trail area still finishes in ~2.5s.
private struct LoadingSilhouetteCanvas: View {
    let silhouette: AreaSilhouette

    @Environment(\.colorScheme) private var colorScheme
    @State private var startDate = Date()

    /// Draw order is difficulty priority so where trails cross, the harder
    /// color wins (red over orange over green) instead of two translucent
    /// strokes blending. Within a difficulty, longest spines lead so the reveal
    /// still fills big-to-small.
    private static func priority(_ d: String) -> Int { d == "h" ? 2 : (d == "m" ? 1 : 0) }
    /// Sorted ONCE per silhouette, not per animation frame. This was a computed
    /// property read inside the Canvas closure of a 60 fps TimelineView, so it
    /// re-allocated and re-sorted the line list every frame — up to ~1,300 lines
    /// on the largest areas, during the loading reveal that is meant to look
    /// smooth.
    @State private var orderedLines: [SilhouetteLine] = []

    private func computeOrderedLines() -> [SilhouetteLine] {
        silhouette.l.filter { $0.p.count >= 2 }.sorted {
            let pa = Self.priority($0.d), pb = Self.priority($1.d)
            return pa == pb ? $0.p.count > $1.p.count : pa < pb
        }
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60.0, paused: false)) { context in
            Canvas { ctx, size in
                guard let bbox = silhouette.bbox else { return }
                let lines = orderedLines
                guard !lines.isEmpty else { return }

                let pad: CGFloat = 24
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

                let totalAnimation: TimeInterval = 1.0
                // Single-line silhouettes get the full duration to
                // themselves so a tiny area doesn't snap-in in 0.4 s and
                // look broken; everything else uses 0.4 s per line with
                // the stagger sized to land the last line exactly at
                // totalAnimation. AreaView holds the loading view for
                // 1.5 s total — the reveal lands at 1.0 s, leaving
                // ~0.5 s of "all trails visible + gentle wave" before
                // the loaded view takes over. That settled window is
                // what stops the eye from registering trails as
                // "cut off mid-reveal" on hundred-trail areas.
                let perLineDuration: TimeInterval = lines.count == 1 ? totalAnimation : 0.4
                let stagger: TimeInterval = lines.count > 1
                    ? (totalAnimation - perLineDuration) / Double(lines.count - 1)
                    : 0
                let elapsed = context.date.timeIntervalSince(startDate)

                // Post-reveal subtle wave: once the reveal is done, displace
                // each path point vertically by a small sine of its x
                // position + time so the silhouette feels alive instead of
                // frozen while we wait for trail data. Ramped in over 0.6 s
                // so it doesn't pop on the moment reveal completes.
                let waveActive = max(0, elapsed - totalAnimation)
                let waveRamp = min(1.0, waveActive / 0.6)
                let waveAmp = 2.0 * waveRamp
                let waveOmegaT = waveActive * 1.4

                for (i, line) in lines.enumerated() {
                    guard line.p.count >= 2 else { continue }
                    let lineStart = Double(i) * stagger
                    let raw = (elapsed - lineStart) / perLineDuration
                    let progress = max(0, min(1, raw))
                    if progress <= 0 { continue }

                    var path = Path()
                    for (j, pt) in line.p.enumerated() {
                        guard pt.count >= 2 else { continue }
                        let lat = pt[0], lon = pt[1]
                        let x = xOffset + (lon - bbox.w) * lonScale * scale
                        let yBase = yOffset + canvasH - (lat - bbox.s) * scale
                        let y = yBase + waveAmp * sin(x / 60.0 + waveOmegaT)
                        let p = CGPoint(x: x, y: y)
                        if j == 0 { path.move(to: p) } else { path.addLine(to: p) }
                    }
                    let trimmed = progress >= 1 ? path : path.trimmedPath(from: 0, to: progress)

                    let color: Color
                    switch line.d {
                    case "e": color = .green
                    case "m": color = .orange
                    case "h": color = .red
                    default:  color = .gray
                    }
                    // Opaque: overlapping trails must NOT accumulate alpha — that
                    // additive blend is what pushed a dimmed orange up into gold.
                    // The faint-backdrop wash is applied once to the whole Canvas
                    // via `.opacity` below, AFTER overlaps resolve by priority, so
                    // orange stays orange instead of collapsing to gold.
                    ctx.stroke(
                        trimmed,
                        with: .color(color),
                        style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round)
                    )
                }
            }
            .opacity(colorScheme == .dark ? 0.9 : 0.82)
        }
        .onAppear {
            startDate = Date()
            orderedLines = computeOrderedLines()
        }
    }
}
