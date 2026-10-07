import SwiftUI
import MapKit

enum RecenterLocationState: Equatable {
    case idle
    case locating
    case available
    case fallback
    case denied
    case unavailable
}

enum RecenterCameraOwner: CaseIterable {
    case openingFraming
    case selection
    case switchedTrail
    case fitSelectedTrail
    case follow
    case gesture
    case recording
    case viewDisappearance
}

enum RecenterRequestGate {
    static func mayApply(request: Int, current: Int) -> Bool {
        request == current
    }

    static func invalidatedGeneration(
        current: Int,
        owner _: RecenterCameraOwner
    ) -> Int {
        current &+ 1
    }
}

/// Three-state camera tracking cycle for the map. Mirrors Apple Maps'
/// own location button — outline (free), filled (follow), filled-with-
/// heading (follow + rotate). Owned by `AreaView` so the rotation
/// button there can both cycle and read the current mode for its icon.
enum MapTrackingMode: Int, CaseIterable {
    /// Camera doesn't follow the user; map stays where the user last
    /// panned / where it was framed on open.
    case free
    /// Camera pans to follow the user; north stays up.
    case follow
    /// Camera follows AND rotates so the user's heading is "up".
    case followHeading

    var next: MapTrackingMode {
        switch self {
        case .free: return .follow
        case .follow: return .followHeading
        case .followHeading: return .free
        }
    }

    var symbol: String {
        switch self {
        case .free: return "location"
        case .follow: return "location.fill"
        case .followHeading: return "location.north.fill"
        }
    }

    var accessibilityLabel: String {
        switch self {
        case .free: return "Map tracking off"
        case .follow: return "Follow user location"
        case .followHeading: return "Follow user location and heading"
        }
    }

    /// Short user-facing label for the toast that pops up when the
    /// rotation cycle button advances. Self-documenting on first use.
    var toastLabel: String {
        switch self {
        case .free: return "Map unlocked"
        case .follow: return "Following your location"
        case .followHeading: return "Following your direction"
        }
    }
}

/// Screen-space obstructions around a selected route. Values are physical
/// points measured from the map's edges, not model or content identifiers.
/// Keeping this as a value lets AreaView report layout without granting it
/// camera ownership; TrailMapView still authorizes every move through its
/// existing camera target/tick pair.
struct MapViewportInsets: Equatable, Sendable {
    var top: CGFloat = 0
    var leading: CGFloat = 0
    var bottom: CGFloat = 0
    var trailing: CGFloat = 0

    static let zero = MapViewportInsets()
}

/// SwiftUI shell that owns the map's state (camera target, tracking
/// mode, halo cache) and hands the actual rendering off to a UIKit
/// `MKMapView` via `MapKitMapView`. The wrapper handles the heavy
/// lifting (overlay reconciliation, custom renderers, viewport
/// culling) — this view just drives camera moves and refreshes the
/// halo cache when a new hike finishes.
///
/// Previously this view rendered overlays directly through SwiftUI's
/// `Map { ... }` content builder, which re-diffed the entire overlay
/// tree on every camera-end change. At 200+ overlays the diff
/// dominated frame time and the Map view would intermittently drop
/// its content under render pressure (the "map disappears" symptom
/// from the build-8 device test). Build 9 moves to `MKMapView`.
struct TrailMapView: View {
    let area: Area
    let activeRecording: ActiveRecording?
    /// Past hikes in this area with timestamps. Cyan halo uses
    /// just the paths; the orange walked-since-completion overlay
    /// (computed for whichever trail is currently selected) needs
    /// the timestamps to filter against `ProgressService.completionDate`.
    let pastHikes: [PastHike]
    let recenterTick: Int
    /// Bump from `AreaView` when the user taps Switch on the retarget
    /// or suggestion banner. Forces a re-fit of the camera around
    /// `selectedTrailId` + the user's current location, even when
    /// `selectedTrailId` is unchanged (the banner shows up because
    /// the user already tapped that trail, so the binding is
    /// already pointing at it and `.onChange(of: selectedTrailId)`
    /// would not fire).
    let centerOnSwitchedTrailTick: Int
    /// Bump from `AreaView` once the sheet has settled at a new stop with a
    /// trail selected. Re-frames that trail — alone, no user location — in
    /// whatever map area the sheet now leaves visible, so a selection made
    /// from the tall browse stop lands framed in the larger area the sheet
    /// opens up when it drops to fit.
    let fitSelectedTrailTick: Int
    @Binding var selectedTrailId: String?
    @AppStorage(StorageKeys.showAllParking) private var showAllParking = false
    /// nil = render every trail. Non-nil = only render trails whose id is
    /// in this set (plus the recording trail and the selected trail, which
    /// always render so the user can see what they tapped or what they're
    /// recording even if a filter would otherwise hide it).
    let visibleTrailIds: Set<String>?
    /// Height (in points) of UI chrome covering the bottom of the map —
    /// recording panel, trail list sheet, etc. Used by `centerOnUser` to
    /// shift the camera south so the user dot lands in the geometric
    /// middle of the *visible* map instead of the full screen.
    let bottomInset: CGFloat
    /// Measured control/sheet obstructions used only when framing a selected
    /// route (and the explicit user + retarget action). Changes to this value
    /// never move the camera by themselves.
    let selectedViewportInsets: MapViewportInsets
    /// Physical control-to-sheet viewport used to audit rendered near-marker
    /// bounds after a selected-route camera move. Unlike the framing insets,
    /// this carries no aesthetic horizontal margin.
    let selectedVisibleInsets: MapViewportInsets
    /// Three-state camera tracking cycle. AreaView owns the state via
    /// `@State`; the rotation button there reads it for the icon and
    /// flips it on tap. TrailMapView reacts via `.onChange` to apply
    /// the new mode (and to update the camera as live location /
    /// heading samples arrive).
    @Binding var trackingMode: MapTrackingMode

    @Environment(ProgressService.self) private var progress
    @Environment(LocationService.self) private var location

    /// Trail-snapped runs for the cyan lifetime "walked here" halo
    /// (`trailSnappedHaloRuns`), wrapped in a single outer group.
    /// Rebuilt on appear and whenever `pastHikes.count` or the set of
    /// completed trails changes. Passed straight through to
    /// `MapKitMapView`, which renders each run as an `MKPolyline`.
    @State private var cachedHaloSegments: [[[CLLocationCoordinate2D]]] = []
    @State private var locationConsumer = LocationConsumerID()
    /// On-trail-filtered segments of the **live** recording's GPS
    /// path. Recomputed at most once per second from
    /// Trail-polyline-snapped runs covered by the in-progress
    /// recording. Each element is a polyline ALONG a trail (not
    /// along the GPS scatter) — same "run of ≥ 2 consecutive
    /// covered nodes at 10m" rule used by the post-completion
    /// orange overlay. Rendered in purple over the trail
    /// polyline so the user sees segments snap to the trail as
    /// they walk them, instead of a jittery line drawn along the
    /// raw GPS path. The raw GPS path is still drawn (also in
    /// purple, slimmer) so off-trail portions remain visible.
    /// Empty when no recording is active.
    @State private var liveHaloSegments: [[CLLocationCoordinate2D]] = []
    @State private var lastLiveHaloRecomputeAt: TimeInterval = 0
    /// Segments of the selected trail's polyline that the user has
    /// walked *since the last completion of that trail*. Rendered
    /// in orange overlaid on the existing blue trail highlight —
    /// blue = "still to do for the next completion," orange =
    /// "already covered this cycle." Recomputed on selection or
    /// when pastHikes changes. Empty when no trail is selected
    /// or when the post-completion path slice doesn't touch the
    /// trail.
    @State private var selectedTrailWalkedSegments: [[CLLocationCoordinate2D]] = []

    /// Where the camera should be. Applied by `MapKitMapView`
    /// whenever `cameraTick` changes — the tick is the "go!" signal,
    /// the target is the payload. This indirection keeps unrelated
    /// view-state updates from accidentally re-framing the map.
    @State private var cameraTarget: MapTarget
    @State private var cameraTick: Int = 0
    @State private var recenterState: RecenterLocationState = .idle
    @State private var recenterGeneration = 0
    /// Extra edge room accumulated only when MapKit reports that a rendered
    /// near-marker crosses the real control-to-sheet viewport. It is reset on
    /// every selection and never reacts to far fallback annotations.
    @State private var selectedMarkerCorrection: MapViewportInsets = .zero
    @State private var selectedMarkerAuditGeneration = 0
    @State private var allowsSelectedMarkerCorrection = false

    /// Does the camera still show the framing this view chose on open — the
    /// whole-area overview, or the trail the area was opened on — rather
    /// than something the user has done since?
    ///
    /// While it does, that framing is kept FIT to whatever the sheet leaves
    /// visible: each change to `bottomInset` re-frames the same target once
    /// the inset stops moving (`scheduleOpeningRefit`). It has to work that
    /// way because the inset arrives in stages — the seed the view mounts
    /// with, the height the sheet actually presents at, then the measured
    /// fit height the sheet commits to ~140 ms later and resizes to — and
    /// the overview is only right for the LAST of them. The previous design
    /// re-fitted exactly once, on the first change, so it spent itself on
    /// the presentation and never saw the commit: any park whose measured
    /// fit differed from the 360 pt seed opened framed for the wrong sheet,
    /// centred too high or too low by half the difference — the "sometimes
    /// off when you first open an area" report.
    ///
    /// Flips false for the rest of this appearance the moment the camera
    /// gets another owner: the user pans / pinches / rotates the map, a
    /// trail is selected or deselected, a recording is in progress, a follow
    /// mode engages, or a recenter / switch-trail framing runs. From then on
    /// a sheet move never touches the camera — which is the guarantee the
    /// old one-shot was really there to keep: a map the user has framed is
    /// never yanked back to the overview.
    @State private var holdsOpeningFraming = true
    /// The refit waiting for `bottomInset` to stop moving. Each change
    /// cancels and replaces it, so a burst lands ONE refit, at the end.
    ///
    /// Held in a reference box rather than as a `@State` Task directly:
    /// `bottomInset` changes on every frame of a sheet drag, and each change
    /// replaces this. Assigning a `@State` value that often would invalidate
    /// the view once more per frame for nothing the body could show — the
    /// box's contents can change without SwiftUI hearing about it.
    @State private var openingRefit = PendingRefit()

    /// Mutable holder for `openingRefit` — see that property.
    @MainActor
    final class PendingRefit {
        var task: Task<Void, Never>?
    }

    init(
        area: Area,
        activeRecording: ActiveRecording?,
        pastHikes: [PastHike],
        recenterTick: Int,
        centerOnSwitchedTrailTick: Int,
        fitSelectedTrailTick: Int = 0,
        selectedTrailId: Binding<String?>,
        visibleTrailIds: Set<String>? = nil,
        bottomInset: CGFloat = 0,
        selectedViewportInsets: MapViewportInsets = .zero,
        selectedVisibleInsets: MapViewportInsets = .zero,
        trackingMode: Binding<MapTrackingMode>
    ) {
        self.area = area
        self.activeRecording = activeRecording
        self.pastHikes = pastHikes
        self.recenterTick = recenterTick
        self.centerOnSwitchedTrailTick = centerOnSwitchedTrailTick
        self.fitSelectedTrailTick = fitSelectedTrailTick
        self._selectedTrailId = selectedTrailId
        self.visibleTrailIds = visibleTrailIds
        self.bottomInset = bottomInset
        self.selectedViewportInsets = selectedViewportInsets
        self.selectedVisibleInsets = selectedVisibleInsets
        self._trackingMode = trackingMode
        // Compute the initial camera target synchronously so the
        // first frame paints the right region — no flash to a
        // default location before .onAppear fires.
        self._cameraTarget = State(initialValue: Self.regionCoveringArea(
            area: area,
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height,
            screenWidth: UIScreen.main.bounds.width
        ))
    }

    /// Developer-mode HUD toggle. Off by default; flipped from
    /// Settings → Developer. When on, an overlay in the top-right
    /// corner shows FPS / overlay count / last update duration /
    /// memory footprint, drawn over the map.
    @AppStorage(StorageKeys.debugHUD) private var showDebugHUD: Bool = false

    var body: some View {
        // ZStack anchored top-LEADING — the HUD goes on the left
        // side because the top-trailing area is occupied by
        // AreaView's favorite-heart button (SwiftUI overlay) AND
        // MapKit's built-in compass (MKMapView UIKit control).
        // Top-leading is clear except for the close-X button which
        // we offset around via padding below.
        ZStack(alignment: .topLeading) {
            MapKitMapView(
                area: area,
                activeRecording: activeRecording,
                haloSegments: cachedHaloSegments,
                liveHaloSegments: liveHaloSegments,
                selectedTrailWalkedSegments: selectedTrailWalkedSegments,
                selectedTrailId: $selectedTrailId,
                showAllParking: showAllParking,
                // Read HERE, in a View body, so observation is registered and the
                // map re-renders the moment the pool lands.
                parkingPoolCount: ParkingPoolService.shared.lots.count,
                visibleTrailIds: visibleTrailIds,
                completedTrailIds: completedTrailIdsForArea,
                cameraTarget: cameraTarget,
                cameraTick: cameraTick,
                showsUserLocation: true,
                // We always pass `.none` here: the bottom-inset shift
                // means we need custom camera math for tracking modes
                // (MKMapView's built-in tracking centers the dot at the
                // geometric middle of the view, which sits behind the
                // recording panel / trail list sheet). The `.onChange`
                // handlers below imperatively re-frame on each location
                // / heading update.
                userTrackingMode: .none,
                userHeading: location.liveHeading,
                demoUserDot: demoUserDot,
                selectedMarkerVisibleInsets: selectedVisibleInsets,
                selectedMarkerAuditGeneration: selectedMarkerAuditGeneration,
                onSelectedNearMarkerOcclusion: applySelectedMarkerCorrection,
                onUserGestureRegionChange: {
                    // User gesture-driven region change (pinch / pan).
                    // In follow modes, snap the camera back to the
                    // user immediately so the dot doesn't drift
                    // off-center waiting for the next GPS update
                    // (which never comes if the user is stationary).
                    // `updateTrackedPosition()` uses .followCenter
                    // which preserves whatever zoom the user just
                    // pinched to. In .free mode, do nothing — the
                    // user's pan / pinch is the new camera state.
                    if trackingMode != .free {
                        updateTrackedPosition()
                    }
                },
                onUserCameraGestureBegan: {
                    // A finger moved on the map: the camera is the user's now.
                    // Whatever the sheet does from here, the opening framing
                    // is never re-applied over where they put it. It also
                    // invalidates a pending one-shot recenter so a late fix
                    // cannot take the camera back.
                    allowsSelectedMarkerCorrection = false
                    releaseOpeningFraming()
                    cancelPendingRecenter(for: .gesture)
                }
            )

            if showDebugHUD {
                // Top-leading, offset past the close-X button which
                // sits at ~(20, 8) above the safe area in AreaView's
                // overlay. 60pt leading gets us clear of the 36×36
                // glass-effect button + a comfortable gap.
                DebugHUDView(diagnostics: MapDiagnostics.shared)
                    .padding(.top, 56)
                    .padding(.leading, 60)
            }
        }
        .overlay(alignment: .top) {
            recenterStatusOverlay
                .padding(.top, 12)
                .padding(.horizontal, 64)
        }
        .onChange(of: showDebugHUD, initial: true) { _, on in
            // The FPS counter runs a CADisplayLink — pause it when
            // the HUD is off so the display-link callback isn't
            // sitting in main's run loop doing nothing useful.
            if on {
                FPSCounter.shared.start()
            } else {
                FPSCounter.shared.stop()
            }
        }
        .onAppear {
            // Compass on as soon as the map is up, so the dot's facing cone is
            // right from the first frame rather than only after the user cycles
            // into a follow mode.
            location.acquireHeading(for: locationConsumer)
            if trackingMode != .free {
                location.acquireLocation(for: locationConsumer, accuracy: .precise)
            }
            // Snap every past hike's GPS onto the trail network so the
            // cyan "walked here" overlay follows the trail polylines
            // exactly and overlapping passes collapse into one line
            // (see trailSnappedHaloRuns), instead of drawing the raw
            // GPS scatter.
            cachedHaloSegments = [trailSnappedHaloRuns()]
            // If the view was re-entered with a recording already in
            // progress (e.g. app foregrounded after backgrounding
            // mid-hike), populate the live halo immediately so the
            // first frame shows it instead of waiting for the next
            // GPS sample to fire `.onChange(of: liveLocation)`.
            if let path = activeRecording?.path, path.count >= 2 {
                liveHaloSegments = liveTrailSnappedRuns(path: path)
                lastLiveHaloRecomputeAt = Date().timeIntervalSince1970
            }
            // Opening the area with a hike already in progress frames the
            // camera zoomed on where you are on the trail — the same
            // ~1500 m as the recenter button — rather than the whole-park
            // overview. Uses the recording's own last GPS sample, so it
            // works even before a fresh live fix lands.
            // Any opening target must remain authoritative over a one-shot
            // recenter result that was requested before this appearance.
            cancelPendingRecenter(for: .openingFraming)
            if (activeRecording?.path.count ?? 0) >= 2 {
                centerOnActiveRecording()
            } else if let id = selectedTrailId,
                      let trail = area.trails.first(where: { $0.id == id }) {
                // Opened from a trail-search result (or any pre-selected
                // trail): frame the selected trail, not the whole area.
                // The selection is set by AreaView during the loading
                // floor — often BEFORE this view mounts — so the
                // `.onChange(of: selectedTrailId)` below never fires for
                // it and `onAppear` is the only place that can catch it.
                // `reapplyOpeningFraming` re-frames this same trail as the
                // sheet settles, for the same reason.
                centerOn(trail: trail)
            } else {
                centerOnArea()
            }
            // A hike already in progress owns the camera from the first
            // frame — even before its first GPS fix, when the overview is
            // shown as a stand-in. A sheet move must never re-fit it.
            if activeRecording != nil { releaseOpeningFraming() }
        }
        .onChange(of: pastHikes.count) { _, _ in
            // New hike finished and AreaView reloaded pastHikes —
            // re-snap the walked-here overlay to include it.
            cachedHaloSegments = [trailSnappedHaloRuns()]
            // Same trigger — refresh the walked-since-completion
            // overlay so the just-finished hike contributes.
            recomputeWalkedSinceCompletion()
        }
        .onChange(of: completedTrailIdsForArea) { _, _ in
            // A trail just completed (live, mid-hike, or on area open):
            // re-snap so the walked-here overlay stops painting the
            // now-completed trail (trailSnappedHaloRuns excludes it).
            cachedHaloSegments = [trailSnappedHaloRuns()]
        }
        .onChange(of: selectedTrailId) { _, newId in
            // A new selection gets a fresh measured-marker correction after
            // the native sheet settles; a deselection discards the old one.
            selectedMarkerCorrection = .zero
            allowsSelectedMarkerCorrection = false
            // Any change of selection — a tap on the map or in the list, or a
            // banner clearing it — ends the opening framing. A selected trail
            // has its own framing (below, plus `fitSelectedTrailTick` as the
            // sheet moves); a deselect leaves the camera where the user was
            // looking, and a later sheet move must not pull it back to the
            // overview.
            releaseOpeningFraming()
            cancelPendingRecenter(for: .selection)
            // Recompute the orange walked-since-completion overlay
            // for the newly-selected trail. Cheap — one trail at a
            // time. Clears to empty when nothing's selected.
            recomputeWalkedSinceCompletion()
            guard let id = newId,
                  let trail = area.trails.first(where: { $0.id == id }) else {
                // Deselecting (tap on empty map, or a banner clears the
                // selection). Leave the camera exactly where it is. Auto-
                // zooming back to the whole-area overview on every deselect
                // was disorienting — the user is usually still looking at the
                // spot they just tapped. Only the opening framing (initial
                // load, and its refits while the sheet settles) frames the
                // whole area on purpose; a plain deselect never moves the map.
                return
            }
            centerOn(trail: trail)
        }
        .onChange(of: bottomInset) { _, _ in
            // The visible map just changed size. While the camera still holds
            // the opening framing, keep that framing fit to the new visible
            // area — once the inset has stopped moving, not per change. On
            // open the inset lands in a burst (the presented height, then the
            // committed fit height ~140 ms later), and during a sheet drag it
            // changes every frame; a refit per change would animate the
            // camera against itself.
            scheduleOpeningRefit()
        }
        .onChange(of: centerOnSwitchedTrailTick) { _, _ in
            // Fired by AreaView when Switch is tapped on the retarget
            // or suggestion banner. Fit the camera around the new
            // active trail PLUS the user's current location so they
            // can see both. Falls back to centerOn(trail:) if we
            // don't have a fresh location fix yet.
            allowsSelectedMarkerCorrection = false
            releaseOpeningFraming()
            cancelPendingRecenter(for: .switchedTrail)
            guard let id = selectedTrailId,
                  let trail = area.trails.first(where: { $0.id == id }) else {
                return
            }
            centerOnUserAndTrail(trail)
        }
        .onChange(of: fitSelectedTrailTick) { _, _ in
            // The sheet settled at a new stop with a trail selected: frame
            // that trail in the map area now visible above it. Reads the
            // CURRENT bottomInset, which is why AreaView waits for the sheet's
            // motion to land before bumping this.
            //
            // While the opening framing is still held, the selected trail is
            // the one the area was opened on, and the inset refit is already
            // keeping it framed for every sheet move — a second animated
            // camera move to the same region would only cut the first short.
            cancelPendingRecenter(for: .fitSelectedTrail)
            guard !holdsOpeningFraming,
                  let id = selectedTrailId,
                  let trail = area.trails.first(where: { $0.id == id }) else {
                return
            }
            allowsSelectedMarkerCorrection = true
            selectedMarkerAuditGeneration &+= 1
            centerOn(trail: trail)
        }
        .onChange(of: recenterTick) { _, _ in
            allowsSelectedMarkerCorrection = false
            performRecenter()
        }
        .onChange(of: trackingMode, initial: false) { _, newMode in
            // Engaging a follow mode hands the camera to the user's position;
            // the overview and selected-marker correction must not come back
            // when the sheet moves.
            if newMode != .free {
                allowsSelectedMarkerCorrection = false
                cancelPendingRecenter(for: .follow)
                releaseOpeningFraming()
            }
            applyTrackingMode(newMode)
        }
        // While in a tracking mode, push every new GPS sample (and
        // every heading change in followHeading) through the same
        // shifted-center math the recenter button uses, so the user
        // dot lands above the bottom panel — not behind it like
        // MapKit's built-in .userLocation camera does.
        .onChange(of: location.liveLocation) { _, _ in
            if trackingMode != .free { updateTrackedPosition() }
            recomputeLiveHaloIfNeeded()
        }
        .onChange(of: activeRecording?.path.count ?? 0) { _, _ in
            // Backup trigger — `location.liveLocation` updates the
            // path indirectly via RecordingService's polling loop,
            // but if SwiftUI coalesces the location change with the
            // path-append (same render pass) we'd otherwise miss
            // the new sample. Path-count change guarantees a recompute
            // whenever a fresh sample lands.
            recomputeLiveHaloIfNeeded()
        }
        .onChange(of: activeRecording == nil) { _, ended in
            // Clear the live halo when recording stops. The just-
            // finished hike's segments will land in `cachedHaloSegments`
            // on the next `pastPaths.count` change and render in the
            // standard cyan past-hike style.
            if ended {
                liveHaloSegments = []
                lastLiveHaloRecomputeAt = 0
            } else {
                // A hike just started: the camera follows the hike from
                // here, never the overview or a pending marker correction.
                allowsSelectedMarkerCorrection = false
                cancelPendingRecenter(for: .recording)
                releaseOpeningFraming()
            }
        }
        .onChange(of: location.liveHeading) { _, _ in
            if trackingMode == .followHeading { updateTrackedPosition() }
        }
        .onDisappear {
            // Tear down the FPS sampler when leaving the area so the
            // CADisplayLink isn't sitting in the main run loop on
            // every other screen for no benefit. Idempotent — safe
            // even when the HUD was never enabled.
            FPSCounter.shared.stop()
            openingRefit.task?.cancel()
            cancelPendingRecenter(for: .viewDisappearance)
            location.releaseLocation(for: locationConsumer)
            location.releaseHeading(for: locationConsumer)
        }
    }

    @ViewBuilder
    private var recenterStatusOverlay: some View {
        switch recenterState {
        case .locating:
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Finding your location…")
            }
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(.regularMaterial, in: Capsule())
            .accessibilityIdentifier("area-recenter-locating")
        case .fallback, .denied, .unavailable:
            HStack(spacing: 8) {
                Image(systemName: "location.slash")
                    .foregroundStyle(.orange)
                Text(recenterStatusMessage)
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                Button(recenterState == .denied ? "Settings" : "Retry") {
                    if recenterState == .denied {
                        location.requestPermission()
                    } else {
                        performRecenter()
                    }
                }
                .font(.caption.weight(.semibold))
                .buttonStyle(.bordered)
            }
            .padding(10)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("area-recenter-status")
        case .idle, .available:
            EmptyView()
        }
    }

    private var recenterStatusMessage: String {
        switch recenterState {
        case .fallback:
            return "Fresh location unavailable; showing your last fix."
        case .denied:
            return "Location access is off."
        case .unavailable:
            return "Location is unavailable."
        case .idle, .locating, .available:
            return ""
        }
    }

    private func performRecenter() {
        // Manual recenter immediately releases opening framing and follow
        // ownership. A stale location may be shown while the authorized fresh
        // one-shot runs, but only this generation may apply its late result.
        releaseOpeningFraming()
        trackingMode = .free
        recenterGeneration += 1
        let request = recenterGeneration
        recenterState = .locating
        if location.userLocation != nil {
            centerOnUser()
        }

        Task { @MainActor in
            let result = await location.requestOneShotFix(
                for: locationConsumer,
                accuracy: .precise
            )
            guard RecenterRequestGate.mayApply(
                request: request,
                current: recenterGeneration
            ) else { return }

            switch result {
            case .success:
                recenterState = .available
                centerOnUser()
            case .denied:
                recenterState = .denied
            case .unavailable:
                recenterState = location.userLocation == nil ? .unavailable : .fallback
            }
        }
    }

    private func cancelPendingRecenter(for owner: RecenterCameraOwner) {
        recenterGeneration = RecenterRequestGate.invalidatedGeneration(
            current: recenterGeneration,
            owner: owner
        )
        if recenterState == .locating {
            recenterState = .idle
        }
    }

    // MARK: - Opening framing

    /// How long `bottomInset` must hold still before the opening framing is
    /// re-fit to it. On open the sheet commits its measured fit height 140 ms
    /// after presenting (AreaView's `minHeightCommit`), and the resize that
    /// follows reports the new inset a frame or three later still — roughly
    /// 160–190 ms after the presented height. This window outlasts that by a
    /// few frames, so the two heights collapse into ONE camera move instead
    /// of two back-to-back, while still firing inside the sheet's ~400 ms
    /// settle animation so map and sheet read as a single motion. If the
    /// commit ever does slip past it, the cost is a second short redirect of
    /// the in-flight animation, not a wrong frame.
    private static let openingRefitSettle: Duration = .milliseconds(250)

    /// Re-fit the opening framing to the current `bottomInset` once it has
    /// stopped changing. Cancels any refit already waiting, so a burst of
    /// inset changes lands one refit, `openingRefitSettle` after the last of
    /// them. Nothing is scheduled once the camera has another owner.
    private func scheduleOpeningRefit() {
        openingRefit.task?.cancel()
        guard holdsOpeningFraming else { return }
        openingRefit.task = Task { @MainActor in
            try? await Task.sleep(for: Self.openingRefitSettle)
            guard !Task.isCancelled else { return }
            reapplyOpeningFraming()
        }
    }

    /// The framing chosen on open, recomputed for the inset the sheet has
    /// NOW: the trail the area was opened on if there is one, else the whole
    /// area. Re-checks the hold and the open's own preconditions at fire
    /// time — a recording or follow mode that began during the wait, or a
    /// gesture that released the hold, means the camera is no longer ours to
    /// move.
    private func reapplyOpeningFraming() {
        guard holdsOpeningFraming, activeRecording == nil, trackingMode == .free else { return }
        cancelPendingRecenter(for: .openingFraming)
        if let id = selectedTrailId,
           let trail = area.trails.first(where: { $0.id == id }) {
            centerOn(trail: trail)
        } else {
            centerOnArea()
        }
    }

    /// The camera now belongs to something other than the opening framing:
    /// the user's own pan / pinch, a selection, a recording, a follow mode,
    /// or a recenter. Idempotent. Also drops any refit still waiting on the
    /// inset, so it cannot fire over the new owner.
    private func releaseOpeningFraming() {
        openingRefit.task?.cancel()
        openingRefit.task = nil
        // Write the flag only when it changes: this runs on every pan for the
        // rest of the appearance, and a no-op must not invalidate the view.
        if holdsOpeningFraming { holdsOpeningFraming = false }
    }

    /// Set of trail ids in this area that ProgressService considers
    /// complete. Recomputed each body eval — the dict is small (<50
    /// completed trails per area in practice) so the allocation is
    /// negligible, and going through @Observable here means the
    /// MapKitMapView gets a stable Equatable input that detects
    /// toggles without manual plumbing.
    private var completedTrailIdsForArea: Set<String> {
        // Credit duplicate-area twins by geometry (not raw id keys), so a trail
        // completed under an identical twin still draws cyan here. Raw keys were
        // empty under the twin the user was viewing → no cyan lines.
        progress.completedTrailIds(in: area.id, among: area.trails)
    }

    // MARK: - Camera control

    /// Heading now runs in EVERY tracking mode, because the user dot draws a
    /// facing cone at all times (see MapKitMapView.userHeading) — not just when
    /// the camera rotates with you. It used to be stopped in .free and .follow,
    /// which would leave that cone pointing nowhere in the two modes you spend
    /// most of a hike in. The compass is cheap next to the GPS fix that's
    /// already running.
    private func applyTrackingMode(_ mode: MapTrackingMode) {
        switch mode {
        case .free:
            location.releaseLocation(for: locationConsumer)
            location.acquireHeading(for: locationConsumer)
            centerOnUser()
        case .follow:
            // Ensure live location is pumping (idempotent — no-op if
            // already running, e.g. during a recording).
            location.acquireLocation(for: locationConsumer, accuracy: .precise)
            location.acquireHeading(for: locationConsumer)
            updateTrackedPosition(resetZoom: true)
        case .followHeading:
            location.acquireLocation(for: locationConsumer, accuracy: .precise)
            location.acquireHeading(for: locationConsumer)
            updateTrackedPosition(resetZoom: true)
        }
    }

    /// Position the camera on the user with the bottomInset shift
    /// (same math the previous SwiftUI-Map version used). For
    /// followHeading the shift is rotated into the camera's frame so
    /// "above the panel" stays above the panel after rotation, and
    /// the target uses MKMapCamera with heading rather than a region
    /// (regions can't carry a heading).
    ///
    /// `resetZoom`: pass `true` on the initial mode entry to apply the
    /// default 1500m / 6000m framing; pass `false` (default) for the
    /// continuous live-tracking pans triggered by GPS / heading
    /// updates. Live-tracking pans use `.followCenter` which preserves
    /// the user's current pinch-zoom — without that, every GPS sample
    /// would re-apply the default zoom and undo any pinch the user
    /// just performed.
    private func updateTrackedPosition(resetZoom: Bool = false) {
        guard let coord = location.liveLocation ?? location.userLocation else { return }
        let heading: CLLocationDirection = trackingMode == .followHeading
            ? (location.liveHeading ?? 0)
            : 0

        let latMeters = 1500.0
        let b = UIScreen.main.bounds
        let shortDim = min(b.width, b.height)
        let metersPerPoint = latMeters / max(shortDim, 1)
        let shiftMeters = (bottomInset / 2) * metersPerPoint
        // Shift "screen down" relative to the camera. For heading=0
        // (north up) that's south. For arbitrary heading θ, screen-
        // down is the bearing (θ + 180°) measured from north.
        let radians = heading * .pi / 180
        let dLat = -shiftMeters * cos(radians) / 111_000.0
        let cosLat = max(0.0001, cos(coord.latitude * .pi / 180))
        let dLon = -shiftMeters * sin(radians) / (111_000.0 * cosLat)
        let shifted = CLLocationCoordinate2D(
            latitude: coord.latitude + dLat,
            longitude: coord.longitude + dLon
        )

        if resetZoom {
            if trackingMode == .followHeading {
                // distance ~6000 m roughly matches the vertical span of
                // MKCoordinateRegion(latitudinalMeters: 1500) in portrait
                // (region fits the SHORTER axis = width, so vertical span
                // is ~3.3 km on a typical phone).
                //
                // Pass the bottom-inset-shifted center so the dot lands
                // at the VISIBLE center (above the panel), matching the
                // steady-state .followCenter behavior below.
                setCameraTarget(.camera(
                    centerLat: shifted.latitude,
                    centerLon: shifted.longitude,
                    distance: 6000,
                    heading: heading
                ))
            } else {
                let latDelta = latMeters / 111_000.0
                let lonDelta = latMeters / (111_000.0 * cosLat)
                setCameraTarget(.region(
                    centerLat: shifted.latitude,
                    centerLon: shifted.longitude,
                    latDelta: latDelta,
                    lonDelta: lonDelta
                ))
            }
        } else {
            // Continuous live-tracking pan — preserve user's zoom.
            // Pass the RAW user coord and let `applyCameraTarget`
            // compute the bottom-inset shift using the live map
            // view's projection. The pre-shifted `shifted` value
            // above assumes the default 1500m zoom; reusing it here
            // mis-positions the dot once the user has pinched to
            // a different zoom.
            setCameraTarget(.followCenter(
                rawLat: coord.latitude,
                rawLon: coord.longitude,
                heading: trackingMode == .followHeading ? heading : nil,
                bottomInset: bottomInset
            ))
        }
    }

    private func centerOnUser() {
        guard let coord = location.userLocation else {
            centerOnArea()
            return
        }
        // 1500 m square region around the user, fed through fittedRegion
        // so the dot lands in the visible (above-panel) center.
        let latDelta = 1500.0 / 111_000.0
        let cosLat = max(0.0001, cos(coord.latitude * .pi / 180))
        let lonDelta = 1500.0 / (111_000.0 * cosLat)
        setCameraTarget(Self.fittedRegion(
            centerLat: coord.latitude,
            centerLon: coord.longitude,
            latDelta: latDelta,
            lonDelta: lonDelta,
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height,
            screenWidth: UIScreen.main.bounds.width
        ))
    }

    private func centerOnArea() {
        #if DEBUG
        // Screenshot-only hand-frame override (see `uitestMapRegion`). Routed
        // through fittedRegion so it still centers in the visible area above
        // the sheet, just from a chosen center/span instead of the bbox.
        if let r = Self.uitestMapRegion {
            setCameraTarget(Self.fittedRegion(
                centerLat: r.0, centerLon: r.1, latDelta: r.2, lonDelta: r.3,
                bottomInset: bottomInset,
                screenHeight: UIScreen.main.bounds.height,
                screenWidth: UIScreen.main.bounds.width
            ))
            return
        }
        #endif
        setCameraTarget(Self.regionCoveringArea(
            area: area,
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height,
            screenWidth: UIScreen.main.bounds.width
        ))
    }

    #if DEBUG
    /// Screenshot-only override for the area-overview camera. Pass
    /// `--uitest-map-region "lat,lon,latSpan,lonSpan"` to hand-frame a shot —
    /// used for the wide, two-lobed South Mountain completion map, whose true
    /// geometric center lands in the low-density gap between its lobes so the
    /// automatic bbox fit doesn't read as "centered." nil in every real build.
    static var uitestMapRegion: (Double, Double, Double, Double)? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--uitest-map-region"), i + 1 < args.count
        else { return nil }
        let parts = args[i + 1].split(separator: ",").compactMap { Double($0) }
        guard parts.count == 4 else { return nil }
        return (parts[0], parts[1], parts[2], parts[3])
    }
    #endif

    /// Frame the camera on an in-progress recording's current position
    /// (its last GPS sample), zoomed to the same ~1500 m as the recenter
    /// button. Used on area-open when a hike is already running so you
    /// land looking at where you are on the trail, not the whole park.
    /// Falls back to the area overview if the path has no usable point.
    private func centerOnActiveRecording() {
        guard let last = activeRecording?.path.last(where: { $0.count >= 2 }) else {
            centerOnArea()
            return
        }
        let lat = last[0], lon = last[1]
        let cosLat = max(0.0001, cos(lat * .pi / 180))
        setCameraTarget(Self.fittedRegion(
            centerLat: lat,
            centerLon: lon,
            latDelta: 1500.0 / 111_000.0,
            lonDelta: 1500.0 / (111_000.0 * cosLat),
            bottomInset: bottomInset,
            screenHeight: UIScreen.main.bounds.height,
            screenWidth: UIScreen.main.bounds.width
        ))
    }

    /// Trail geometry plus the same near-only parking set rendered for a
    /// selected trail. Reject the entire payload if any coordinate is malformed:
    /// silently dropping one endpoint could make an unsafe frame look valid.
    private func selectedFramingPoints(for trail: Trail) -> [(lat: Double, lon: Double)]? {
        // Pooled too, so the camera frames the same nearby lots the map pins.
        // Far fallback lots remain excluded: including one miles away would
        // shrink the selected route into an unreadable speck.
        let parking = Area.nearestParking(
            lots: ParkingPoolService.shared.merged(with: area.parking, for: trail),
            for: trail
        ).map { (lat: $0.lat, lon: $0.lon) }
        return Self.selectedRoutePoints(
            segments: trail.segments,
            additionalPoints: parking
        )
    }

    private func centerOn(
        trail: Trail,
        markerCorrection: MapViewportInsets? = nil
    ) {
        let correction = markerCorrection ?? selectedMarkerCorrection
        let insets = MapViewportInsets(
            top: selectedViewportInsets.top + correction.top,
            leading: selectedViewportInsets.leading + correction.leading,
            bottom: selectedViewportInsets.bottom + correction.bottom,
            trailing: selectedViewportInsets.trailing + correction.trailing
        )
        guard let points = selectedFramingPoints(for: trail),
              let target = Self.selectedRouteRegion(
                points: points,
                viewportInsets: insets,
                screenHeight: UIScreen.main.bounds.height,
                screenWidth: UIScreen.main.bounds.width
              ) else { return }
        setCameraTarget(target)
    }

    /// Apply only the additional room proven necessary by rendered near-marker
    /// bounds. Each correction is re-audited after its camera move; far fallback
    /// annotations never enter this path, and a user-owned camera disables it.
    private func applySelectedMarkerCorrection(_ correction: MapViewportInsets) {
        guard allowsSelectedMarkerCorrection,
              correction != .zero,
              let id = selectedTrailId,
              let trail = area.trails.first(where: { $0.id == id }) else { return }
        func accumulated(_ current: CGFloat, _ extra: CGFloat) -> CGFloat {
            // A marker view is smaller than this bound. If 64 additional points
            // still cannot clear the viewport, stop moving the camera and let
            // the explicit UI assertion fail rather than creating a refit loop.
            min(current + extra, 64)
        }
        let updated = MapViewportInsets(
            top: accumulated(selectedMarkerCorrection.top, correction.top),
            leading: accumulated(selectedMarkerCorrection.leading, correction.leading),
            bottom: accumulated(selectedMarkerCorrection.bottom, correction.bottom),
            trailing: accumulated(selectedMarkerCorrection.trailing, correction.trailing)
        )
        guard updated != selectedMarkerCorrection else {
            allowsSelectedMarkerCorrection = false
            return
        }
        selectedMarkerCorrection = updated
        selectedMarkerAuditGeneration &+= 1
        centerOn(trail: trail, markerCorrection: updated)
    }

    /// Like `centerOn(trail:)` but expands the bbox to also include
    /// the user's current location. Used after a retarget Switch so
    /// the camera frames "you + the new active trail" instead of
    /// just the trail (which can leave the user off-screen if they
    /// were standing well outside the trail's extent). Falls back
    /// to `centerOn(trail:)` if we don't have a location fix yet.
    private func centerOnUserAndTrail(_ trail: Trail) {
        guard let userLoc = location.liveLocation ?? location.userLocation else {
            centerOn(trail: trail)
            return
        }
        guard var points = selectedFramingPoints(for: trail) else { return }
        points.append((lat: userLoc.latitude, lon: userLoc.longitude))
        guard let target = Self.selectedRouteRegion(
            points: points,
            viewportInsets: selectedViewportInsets,
            screenHeight: UIScreen.main.bounds.height,
            screenWidth: UIScreen.main.bounds.width
        ) else { return }
        setCameraTarget(target)
    }

    private func setCameraTarget(_ target: MapTarget) {
        cameraTarget = target
        cameraTick &+= 1
    }

    /// Synthetic "you are here" dot for the App Store recording
    /// screenshot, pinned to the demo recording's current position (the
    /// same point `centerOnActiveRecording` frames). The CI simulator's
    /// `simctl privacy grant` doesn't reliably land as authorized before
    /// MKMapView asks CoreLocation for its built-in dot, so the shot
    /// rendered dot-less — this renders one deterministically instead.
    /// Always nil outside DEBUG + `--uitest-recording`, so no production
    /// build can ever show a fake location.
    private var demoUserDot: CLLocationCoordinate2D? {
        #if DEBUG
        guard UITestSupport.isRecordingRequested,
              let last = activeRecording?.path.last(where: { $0.count >= 2 }) else { return nil }
        return CLLocationCoordinate2D(latitude: last[0], longitude: last[1])
        #else
        return nil
        #endif
    }

    // MARK: - Live halo recompute

    /// Throttled rebuild of `liveHaloSegments` from the active
    /// recording's current path. Capped at one recompute per second
    /// so a 1 Hz GPS sample rate does at most one O(N · grid-lookup)
    /// pass per second — N typically <1000 points for the first 30
    /// minutes of a hike. No-op when no recording is active.
    private func recomputeLiveHaloIfNeeded() {
        guard let path = activeRecording?.path, path.count >= 2 else { return }
        let now = Date().timeIntervalSince1970
        guard now - lastLiveHaloRecomputeAt >= 1.0 else { return }
        lastLiveHaloRecomputeAt = now
        liveHaloSegments = liveTrailSnappedRuns(path: path)
    }

    /// Compute trail-polyline-snapped runs covered by the in-progress
    /// recording's GPS path. Runs `trailNodeRuns` (10m buffer, runs of
    /// >= 2 consecutive covered nodes) over the area's trails and
    /// concatenates the results, so the live cyan "you're walking this
    /// segment" overlay follows the trail geometry rather than the GPS
    /// scatter.
    ///
    /// Snaps onto `area.trails`, the DECIMATED render geometry the map
    /// actually draws, NOT the dense `rawTrails`. The two diverge by up to
    /// the decimation epsilon on curves, so snapping to raw emitted nodes
    /// that sit OFF the drawn trail line (the "cyan not snapping to the
    /// trail" report). Same fix, and same reason, as trailSnappedHaloRuns.
    private func liveTrailSnappedRuns(path: [GpsPoint]) -> [[CLLocationCoordinate2D]] {
        var gpsGrid = SpatialGrid()
        for p in path where p.count >= 2 {
            gpsGrid.insert(p)
        }
        let sourceTrails = area.trails
        var all: [[CLLocationCoordinate2D]] = []
        for trail in sourceTrails {
            all.append(contentsOf: trailNodeRuns(coveredBy: gpsGrid, in: trail))
        }
        return all
    }

    // MARK: - Walked-here (lifetime) halo

    /// Trail-polyline-snapped runs for the cyan lifetime "walked here"
    /// halo. Builds ONE GPS grid from every past hike, then walks each
    /// not-yet-completed trail's polyline and emits runs of nodes within
    /// 30 m of any GPS point (the lifetime buffer — looser than the 10 m
    /// since-completion buffer, matching `rebuildCoverageFromHistory` so
    /// the halo and the coverage bar agree on what counts as walked).
    ///
    /// Because it iterates the TRAIL nodes rather than the GPS path, the
    /// result follows the trail exactly and multiple passes over the same
    /// stretch collapse into a single run — no more raw-GPS scatter or
    /// stacked overlapping traces. Completed trails are excluded so their
    /// finished-state styling isn't smeared over.
    private func trailSnappedHaloRuns() -> [[CLLocationCoordinate2D]] {
        var gpsGrid = SpatialGrid()
        for hike in pastHikes {
            for p in hike.path where p.count >= 2 {
                gpsGrid.insert(p)
            }
        }
        let completed = completedTrailIdsForArea
        // Snap onto the DECIMATED render geometry (`area.trails`) — the exact
        // polylines the map draws — NOT the dense `rawTrails`. The two diverge
        // by up to the 5 m decimation epsilon on curves, which is invisible at
        // normal zoom but shows when zoomed in: snapping to raw made the cyan
        // drift OFF the drawn trail line (the "scatter" and the
        // cyan-with-no-line-under-it). Emitting render nodes puts the cyan
        // pixel-on-pixel over the trail; the 30 m buffer keeps detection
        // robust despite the sparser node set.
        let sourceTrails = area.trails
            .filter { !completed.contains($0.id) }
        var all: [[CLLocationCoordinate2D]] = []
        for trail in sourceTrails {
            all.append(contentsOf: trailNodeRuns(coveredBy: gpsGrid, in: trail, bufferM: 30.0))
        }
        return all
    }

    // MARK: - Walked-since-completion overlay

    /// Recompute the orange walked-since-completion segments for
    /// the currently-selected trail.
    ///
    /// "Since last completion" means: filter `pastHikes` to those
    /// ending after the trail's last completion timestamp (or all
    /// hikes when never completed). Then walk THIS trail's
    /// polyline node-by-node, marking runs of consecutive nodes
    /// within 30 m of any post-completion GPS point. Render those
    /// trail-polyline runs in orange.
    ///
    /// Note the inversion: we iterate the trail polyline (not the
    /// GPS path), so the orange line traces the trail exactly
    /// rather than wandering with the user's imperfect walking
    /// path. Single-trail scope keeps the cost down — one walk
    /// over the trail's ~100 nodes against a grid built from
    /// post-completion GPS samples.
    private func recomputeWalkedSinceCompletion() {
        guard let selectedTrailId,
              let trail = area.trails.first(where: { $0.id == selectedTrailId }) else {
            selectedTrailWalkedSegments = []
            return
        }
        let lastCompletion = progress.completionDate(areaId: area.id, trailId: selectedTrailId)
        let relevantPaths: [GpsPoint] = pastHikes
            .filter { hike in
                if let lastCompletion {
                    // Completed trail — every hike that started
                    // AFTER the completion stamp counts toward
                    // the post-completion overlay, regardless of
                    // whether it deliberately targeted this
                    // trail. Tight 10m buffer in `trailNodeRuns`
                    // does the discrimination: drift across the
                    // trail (within 10m of a node) shows orange;
                    // a hike on a far-away trail contributes
                    // nothing. Filter on `startedAt` (not
                    // `endedAt`) so the completing hike — which
                    // has startedAt before and endedAt after the
                    // completion stamp — is excluded.
                    return hike.startedAt > lastCompletion
                }
                // Never completed — all hikes count toward
                // first-completion progress.
                return true
            }
            .flatMap(\.path)
        if relevantPaths.isEmpty {
            selectedTrailWalkedSegments = []
            return
        }
        // Build a GPS-points grid (NOT a trail-nodes grid). The
        // iteration below walks the TRAIL polyline and asks
        // "any GPS point near this node?" — which gives us runs
        // along the trail itself, not segments of the GPS path.
        var gpsGrid = SpatialGrid()
        for p in relevantPaths where p.count >= 2 {
            gpsGrid.insert(p)
        }
        // Walk the trail polyline (use raw geometry when available
        // for the dense node set) and emit on-trail runs.
        let sourceTrails = area.rawTrails ?? area.trails
        let geomTrail = sourceTrails.first(where: { $0.id == trail.id }) ?? trail
        selectedTrailWalkedSegments = trailNodeRuns(coveredBy: gpsGrid, in: geomTrail)
    }

    /// Walk each segment of `trail.segments` node-by-node, emit
    /// runs of consecutive trail nodes that are within 10 m of any
    /// point in `gpsGrid`. The returned polylines are sequences of
    /// TRAIL nodes — so rendering them produces lines that follow
    /// the trail polyline precisely, not the user's GPS scatter.
    /// 10m (tighter than the 30m lifetime buffer) matches the
    /// `sinceCompletionBufferMeters` used by
    /// `rebuildCoverageFromHistory`, so the bar's "% remaining"
    /// and the orange overlay agree on what counts as "drifted
    /// across this trail" post-completion.
    /// Spacing the coverage overlay advances in. The cyan can only move forward
    /// when a node it walks gets covered, so node spacing IS the granularity of
    /// visible progress. Measured on South Mountain's shipped geometry: after
    /// the 5 m render decimation the drawable nodes sit a median 38 m apart,
    /// 86 m at p90 and up to 367 m — so you could walk a hundred metres and see
    /// nothing change, which reads as "it stopped tracking me".
    ///
    /// Interpolating along the DECIMATED line (not falling back to the raw one)
    /// keeps every emitted point exactly on the polyline the map draws, so this
    /// buys granularity without reintroducing the off-the-line drift that came
    /// from snapping to raw geometry.
    private static let coverageStepMeters: Double = 6.0

    /// Insert points along a segment so no two consecutive ones are further
    /// apart than `coverageStepMeters`. Points already closer than that are
    /// left alone.
    private func densified(_ seg: [GpsPoint]) -> [(lat: Double, lon: Double)] {
        var out: [(lat: Double, lon: Double)] = []
        var prev: (lat: Double, lon: Double)? = nil
        for node in seg where node.count >= 2 {
            let p = (lat: node[0], lon: node[1])
            if let a = prev {
                let d = haversineDistanceM(lat1: a.lat, lon1: a.lon, lat2: p.lat, lon2: p.lon)
                if d > Self.coverageStepMeters {
                    let steps = Int(d / Self.coverageStepMeters)
                    if steps > 1 {
                        for k in 1..<steps {
                            let t = Double(k) / Double(steps)
                            out.append((lat: a.lat + (p.lat - a.lat) * t,
                                        lon: a.lon + (p.lon - a.lon) * t))
                        }
                    }
                }
            }
            out.append(p)
            prev = p
        }
        return out
    }

    private func trailNodeRuns(coveredBy gpsGrid: SpatialGrid, in trail: Trail,
                               bufferM: Double = 10.0) -> [[CLLocationCoordinate2D]] {
        var runs: [[CLLocationCoordinate2D]] = []
        for seg in trail.segments {
            var current: [CLLocationCoordinate2D] = []
            for node in densified(seg) {
                let lat = node.lat
                let lon = node.lon
                if gpsGrid.hasNeighbor(lat: lat, lon: lon, withinMeters: bufferM) {
                    current.append(CLLocationCoordinate2D(latitude: lat, longitude: lon))
                } else if !current.isEmpty {
                    if current.count >= 2 { runs.append(current) }
                    current.removeAll(keepingCapacity: true)
                }
            }
            if current.count >= 2 { runs.append(current) }
        }
        return runs
    }

    // MARK: - Fitted-region math

    /// Frame a target lat/lon bbox in the visible portion of the map
    /// (above the bottom panel). Inflates the latitudinal span so the
    /// bbox fits in the `(1 - p)` fraction of the screen that's not
    /// covered, then shifts the center south so the bbox sits in
    /// that visible top portion. Without this, the bottom of the
    /// framed region was hidden behind the recording panel or trail
    /// list sheet.
    ///
    /// `screenHeight` is plumbed in by the caller rather than read
    /// from `UIScreen.main` inside this function so the math stays
    /// `nonisolated` and unit-testable — `UIScreen.main` is itself
    /// `@MainActor`, and pulling it into a pure helper would force
    /// every caller (including XCTests) onto the main actor for no
    /// real reason. Call sites in TrailMapView already run on the
    /// main actor, so reading `UIScreen.main` at the call site is
    /// free for them.
    nonisolated static func fittedRegion(
        centerLat: Double, centerLon: Double,
        latDelta: Double, lonDelta: Double,
        bottomInset: CGFloat,
        screenHeight: CGFloat,
        screenWidth: CGFloat
    ) -> MapTarget {
        // Cap p at 0.7 so a worst-case panel-covers-everything state
        // still leaves a sane minimum visible area.
        let p = min(0.7, bottomInset / max(screenHeight, 1))
        let visibleFraction = max(0.3, 1 - p)

        // Inflate the latitudinal span so the requested content fits
        // in the visible (top) portion. Longitudinal needs no
        // inflation — the panel doesn't constrain horizontally.
        let regionLatDelta = max(latDelta / visibleFraction, 0.005)
        let regionLonDelta = max(lonDelta, 0.005)

        // Shift center south so the content sits centered in the VISIBLE
        // (top, un-occluded) portion of the map. The shift must be half the
        // occluded fraction of the latitude MapKit will actually DISPLAY —
        // not of `regionLatDelta`. MapKit shows at least the region and fits
        // by the more-constrained axis: a WIDE area (large lonDelta) is
        // width-constrained, so the displayed latitude span balloons to
        // whatever fills the view's height — far bigger than regionLatDelta.
        // Using regionLatDelta for the shift (the old bug) barely nudged a
        // wide park, leaving it near the full-screen center → low, right at
        // the sheet, with the surrounding city filling the top. Basing the
        // shift on the displayed span centers the park in the visible area.
        //
        // How much latitude fills the height is a Mercator question, not a
        // plain aspect ratio. The map draws a degree of latitude 1/cos(lat)
        // times as tall as a degree of longitude, so a width-constrained view
        // holds `lonDelta * (height/width) * cos(lat)` degrees of latitude.
        // Without the cosine the displayed span — and with it the shift — is
        // too big by 1/cos(lat): 20% at Phoenix, which over-shifted a wide
        // park by ~35 pt at the fit stop and ~55 pt at browse (the cosine
        // term alone — MapKit itself centres a region some 15–20 pt below the
        // view's midpoint, which hid part of that on screen), and 2× at 60°N,
        // where a wide park was pushed clear off the top of the visible
        // strip. Floored well short of the poles so a polar area can't zero
        // the span.
        let mercatorLatPerLon = max(0.05, cos(centerLat * .pi / 180))
        let displayedLatDelta = max(
            regionLatDelta,
            regionLonDelta * Double(screenHeight / max(screenWidth, 1)) * mercatorLatPerLon
        )
        let shiftLat = displayedLatDelta * p / 2
        return .region(
            centerLat: centerLat - shiftLat,
            centerLon: centerLon,
            latDelta: regionLatDelta,
            lonDelta: regionLonDelta
        )
    }

    /// Directional selected-route fit. The existing bottom-only overload above
    /// remains the authority for all opening, area, recording, recenter, and
    /// follow framing. A bottom-only directional request delegates to it so its
    /// behavior remains identical.
    nonisolated static func fittedRegion(
        centerLat: Double, centerLon: Double,
        latDelta: Double, lonDelta: Double,
        viewportInsets: MapViewportInsets,
        screenHeight: CGFloat,
        screenWidth: CGFloat
    ) -> MapTarget {
        if viewportInsets.top == 0,
           viewportInsets.leading == 0,
           viewportInsets.trailing == 0 {
            return fittedRegion(
                centerLat: centerLat,
                centerLon: centerLon,
                latDelta: latDelta,
                lonDelta: lonDelta,
                bottomInset: viewportInsets.bottom,
                screenHeight: screenHeight,
                screenWidth: screenWidth
            )
        }

        let height = max(Double(screenHeight), 1)
        let width = max(Double(screenWidth), 1)

        func obstructionFractions(
            first: CGFloat,
            second: CGFloat,
            dimension: Double
        ) -> (first: Double, second: Double) {
            let rawFirst = max(0, Double(first)) / dimension
            let rawSecond = max(0, Double(second)) / dimension
            let rawTotal = rawFirst + rawSecond
            guard rawTotal > 0.7 else { return (rawFirst, rawSecond) }
            let scale = 0.7 / rawTotal
            return (rawFirst * scale, rawSecond * scale)
        }

        let vertical = obstructionFractions(
            first: viewportInsets.top,
            second: viewportInsets.bottom,
            dimension: height
        )
        let horizontal = obstructionFractions(
            first: viewportInsets.leading,
            second: viewportInsets.trailing,
            dimension: width
        )
        let visibleHeight = max(0.3, 1 - vertical.first - vertical.second)
        let visibleWidth = max(0.3, 1 - horizontal.first - horizontal.second)

        let regionLatDelta = min(max(latDelta / visibleHeight, 0.005), 180)
        let regionLonDelta = min(max(lonDelta / visibleWidth, 0.005), 360)
        let mercatorLatPerLon = max(0.05, cos(centerLat * .pi / 180))
        let displayedLatDelta = min(180, max(
            regionLatDelta,
            regionLonDelta * height / width * mercatorLatPerLon
        ))
        let displayedLonDelta = min(360, max(
            regionLonDelta,
            regionLatDelta * width / height / mercatorLatPerLon
        ))

        // A top obstruction moves the unobstructed center down the screen, so
        // move the map center north; bottom does the inverse. Leading/trailing
        // follow the same screen-space rule in longitude.
        let shiftedLat = min(max(
            centerLat + displayedLatDelta * (vertical.first - vertical.second) / 2,
            -90
        ), 90)
        var shiftedLon = centerLon
            + displayedLonDelta * (horizontal.second - horizontal.first) / 2
        while shiftedLon > 180 { shiftedLon -= 360 }
        while shiftedLon < -180 { shiftedLon += 360 }

        return .region(
            centerLat: shiftedLat,
            centerLon: shiftedLon,
            latDelta: regionLatDelta,
            lonDelta: regionLonDelta
        )
    }

    /// Convert raw route geometry plus nearby parking/user coordinates into a
    /// single fail-closed input. A short point rejects the entire route rather
    /// than silently omitting an endpoint from the safety calculation.
    nonisolated static func selectedRoutePoints(
        segments: [[[Double]]],
        additionalPoints: [(lat: Double, lon: Double)] = []
    ) -> [(lat: Double, lon: Double)]? {
        var points: [(lat: Double, lon: Double)] = []
        for segment in segments {
            for point in segment {
                guard point.count >= 2 else { return nil }
                points.append((lat: point[0], lon: point[1]))
            }
        }
        guard !points.isEmpty else { return nil }
        points.append(contentsOf: additionalPoints)
        return points
    }

    /// Build the selected-route camera payload from route, nearby parking, and
    /// optional user points. Any malformed coordinate rejects the whole payload
    /// so an incomplete route can never be presented as safely framed.
    nonisolated static func selectedRouteRegion(
        points: [(lat: Double, lon: Double)],
        viewportInsets: MapViewportInsets,
        screenHeight: CGFloat,
        screenWidth: CGFloat
    ) -> MapTarget? {
        let insetValues = [
            viewportInsets.top,
            viewportInsets.leading,
            viewportInsets.bottom,
            viewportInsets.trailing,
        ]
        guard !points.isEmpty,
              screenHeight.isFinite, screenHeight > 0,
              screenWidth.isFinite, screenWidth > 0,
              insetValues.allSatisfy({ $0.isFinite }),
              points.allSatisfy({ point in
                  point.lat.isFinite && point.lon.isFinite
                      && (-90...90).contains(point.lat)
                      && (-180...180).contains(point.lon)
              }) else { return nil }

        let lats = points.map(\.lat)
        let lons = points.map(\.lon)
        guard let minLat = lats.min(), let maxLat = lats.max(),
              let minLon = lons.min(), let maxLon = lons.max() else { return nil }
        let longitude = lonCenterAndSpan(minLon: minLon, maxLon: maxLon)
        return fittedRegion(
            centerLat: (minLat + maxLat) / 2,
            centerLon: longitude.center,
            latDelta: max((maxLat - minLat) * 1.4, 0.005),
            lonDelta: max(longitude.span * 1.4, 0.005),
            viewportInsets: viewportInsets,
            screenHeight: screenHeight,
            screenWidth: screenWidth
        )
    }

    /// Longitude center + span for an extent that may CROSS the antimeridian
    /// (±180°).
    ///
    /// A naive `(min + max) / 2` / `max - min` breaks for any such area, and it
    /// CRASHES rather than merely mis-framing. Alaska Maritime National Wildlife
    /// Refuge runs the Aleutians from -166.73° to +173.14°, so the naive math
    /// asks for a center of 3.21° (off the coast of Africa) and a longitude span
    /// of 441° — wider than the planet. `MKCoordinateSpan` rejects that and
    /// `setRegion` traps, so tapping the area killed the app every time.
    ///
    /// When the naive span exceeds 180° the extent is really the SHORT way round
    /// through the antimeridian: the true span is `360 - naive`, centered past
    /// `maxLon` and wrapped back into [-180, 180]. For that refuge: a 20.13°
    /// span centered at -176.79°, which is the Aleutians.
    nonisolated static func lonCenterAndSpan(minLon: Double,
                                             maxLon: Double) -> (center: Double, span: Double) {
        let naive = maxLon - minLon
        guard naive > 180 else { return ((minLon + maxLon) / 2, naive) }
        let span = 360 - naive
        var center = maxLon + span / 2
        if center > 180 { center -= 360 }
        return (center, span)
    }

    nonisolated static func regionCoveringArea(area: Area, bottomInset: CGFloat, screenHeight: CGFloat, screenWidth: CGFloat) -> MapTarget {
        let coords = area.trails
            .flatMap { $0.segments.flatMap { $0 } }
            .compactMap { p -> (lat: Double, lon: Double)? in
                guard p.count >= 2 else { return nil }
                return (p[0], p[1])
            }

        if !coords.isEmpty {
            let lats = coords.map { $0.lat }
            let lons = coords.map { $0.lon }
            let minLat = lats.min()!, maxLat = lats.max()!
            let minLon = lons.min()!, maxLon = lons.max()!
            // Antimeridian-safe — see lonCenterAndSpan.
            let lon = lonCenterAndSpan(minLon: minLon, maxLon: maxLon)
            return fittedRegion(
                centerLat: (minLat + maxLat) / 2,
                centerLon: lon.center,
                // 1.3x padding so trails don't kiss the visible edges.
                latDelta: max((maxLat - minLat) * 1.3, 0.01),
                lonDelta: max(lon.span * 1.3, 0.01),
                bottomInset: bottomInset,
                screenHeight: screenHeight,
                screenWidth: screenWidth
            )
        }

        if let bbox = area.bbox, bbox.count == 4 {
            // Antimeridian-safe — see lonCenterAndSpan.
            let lon = lonCenterAndSpan(minLon: bbox[0], maxLon: bbox[2])
            return fittedRegion(
                centerLat: (bbox[1] + bbox[3]) / 2,
                centerLon: lon.center,
                latDelta: max(abs(bbox[3] - bbox[1]) * 1.2, 0.01),
                lonDelta: max(abs(lon.span) * 1.2, 0.01),
                bottomInset: bottomInset,
                screenHeight: screenHeight,
                screenWidth: screenWidth
            )
        }

        // Fallback: a fixed-distance camera around the area's
        // bundled center. Used only when the area carries no trail
        // geometry AND no bbox — vanishingly rare in practice.
        return .camera(
            centerLat: area.centerLat,
            centerLon: area.centerLon,
            distance: 5000,
            heading: 0
        )
    }
}
