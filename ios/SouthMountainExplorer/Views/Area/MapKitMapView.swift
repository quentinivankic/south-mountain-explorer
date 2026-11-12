import SwiftUI
import MapKit
import UIKit
import os

let mapLog = Logger(subsystem: "app.trekdex", category: "map")

extension Color {
    /// Cyan for completed trails — the single source of truth shared by the
    /// map's completed-trail stroke (bridged to UIColor) and the trail-list
    /// completed thumbnail, so the two never drift. This is the app's "done /
    /// progress" language (the onboarding promises "trails you finish turn
    /// cyan"); it also reads as cyan under the translucent walked-here halo.
    /// Distinct from difficulty (green/orange/red), selection (blue), and
    /// recording (purple); the color alone marks "done" — same line width as
    /// the other trails.
    static let completedTrail = Color.cyan
}

/// UIKit-backed map. Replaces SwiftUI's `Map { ... }` content tree
/// for dense overlay scenarios (200+ trail polylines).
///
/// Why: SwiftUI `Map` re-evaluates its `MapContentBuilder` closure
/// every time any state in the surrounding view changes — including
/// every `.onMapCameraChange` tick — and diffs the resulting overlay
/// tree against the previous one. At hundreds of overlays the diff
/// dominates frame time, and under sustained pressure the Map view
/// silently drops its content (the "map disappears" symptom from
/// build-8 device testing). `MKMapView` treats overlays as
/// long-lived UIKit objects: add/remove is incremental, the renderer
/// pipeline is fully Core Animation, and camera-change costs nothing
/// beyond MapKit's internal viewport culling.
///
/// The caller (TrailMapView) keeps its existing state model
/// — selection, tracking mode, halo cache — and passes the relevant
/// slices in via the struct's stored properties. Camera moves are
/// delivered as a `(target, tick)` pair: bump the tick when you want
/// the map to animate to the target, leave it stable when you don't.
/// This avoids the trap of accidentally re-applying the same camera
/// region on every unrelated state change.

/// Discriminated camera move. `region` for normal framings (centered
/// on an area / trail / user), `camera` for the followHeading mode
/// (which needs a rotated heading and is set via MKMapCamera since
/// MKCoordinateRegion has no heading concept), `followCenter` for
/// continuous live-tracking pans that must PRESERVE the user's
/// current pinch-zoom — without this case, every GPS update during
/// follow lock would re-apply a fixed zoom and undo any zoom the
/// user just performed.
///
/// `followCenter` takes the RAW user coord plus the bottom panel
/// inset. The shift to keep the dot above the panel is computed by
/// `applyCameraTarget` using `MKMapView.convert(_:toCoordinateFrom:)`,
/// which respects the current zoom AND the current heading (for
/// followHeading mode). Without this, a pre-computed shift assuming
/// the default 1500m zoom drifts the dot off-screen at any other
/// zoom level — observed after the pinch-zoom-while-locked feature
/// landed.
enum MapTarget: Equatable {
    case region(centerLat: Double, centerLon: Double, latDelta: Double, lonDelta: Double)
    case camera(centerLat: Double, centerLon: Double, distance: Double, heading: Double)
    case followCenter(rawLat: Double, rawLon: Double, heading: Double?, bottomInset: CGFloat)
}

/// Marker type for the screenshot-only synthetic user dot — see
/// `MapKitMapView.demoUserDot`. A distinct class (rather than a bare
/// MKPointAnnotation) lets the Coordinator's `viewFor` recognize it
/// without affecting any other annotation MapKit manages (including
/// the real MKUserLocation).
final class DemoUserDotAnnotation: MKPointAnnotation {}

/// Marker subtype for raw recording runs. MKMapView asks its delegate for a
/// renderer synchronously from `addOverlay`; identifying the run by type avoids
/// re-entering the coordinator's `recordingOverlayIds` while reconciliation
/// holds that set as an `inout` value.
final class RecordingPolyline: MKPolyline {}
/// "Plan a Hike" route preview line. `isCasing` marks the wider white stroke
/// drawn under the colored line. Type-identified, like `RecordingPolyline`,
/// because MapKit asks for the renderer inside `addOverlay`.
final class PlannedRoutePolyline: MKPolyline {
    var kind: PlannedRoute.Kind = .trail
    var isCasing = false
    /// Trail ground already walked (or walked earlier this hike): drawn faded.
    var isDone = false
    /// The selected step's leg: a wide glow drawn under the route line.
    var isHighlight = false
    /// Walk through: the rest of the route, drawn strongly faded.
    var isFaded = false
}

/// The map's current screen points per map point, shared by the line
/// renderers. Satellite and hybrid use realistic elevation (the iOS 27.3 pan
/// fix), and on a terrain map MapKit draws overlay tiles at a power-of-two
/// tile zoom scale and stretches them to the screen's, so a fixed `lineWidth`
/// drew about 1.4-1.8x too thick and changed as you zoomed. Stroking against
/// the screen's scale keeps every line at its `lineWidth` in screen points at
/// every zoom. `nil` (flat maps) = MapKit's own stroke, unchanged.
final class LineDisplayZoomScale: Sendable {
    private let lock = OSAllocatedUnfairLock<MKZoomScale?>(initialState: nil)
    var value: MKZoomScale? {
        get { lock.withLock { $0 } }
        set { lock.withLock { $0 = newValue } }
    }

    /// Screen points per map point. `visibleMapRect` is the bounding box of
    /// the (possibly rotated) view, whose width is
    /// `(w·|cos h| + h·|sin h|) / scale`, so solving for scale keeps a rotated
    /// map the same as an unrotated one. `nil` when the inputs are degenerate.
    static func zoomScale(viewSize: CGSize, visibleMapWidth: Double, headingDegrees: Double) -> MKZoomScale? {
        let radians = headingDegrees * .pi / 180
        let boxPoints = Double(viewSize.width) * abs(cos(radians))
            + Double(viewSize.height) * abs(sin(radians))
        guard boxPoints > 0, visibleMapWidth > 0, visibleMapWidth.isFinite,
              boxPoints.isFinite else { return nil }
        return MKZoomScale(boxPoints / visibleMapWidth)
    }

    /// Redraw only when the scale moved by more than 2%, so a pan (same scale)
    /// never re-rasterizes the lines.
    static func needsRedraw(from old: MKZoomScale?, to new: MKZoomScale?) -> Bool {
        switch (old, new) {
        case (nil, nil): return false
        case let (o?, n?): return abs(n / o - 1) > 0.02
        default: return true
        }
    }
}

/// Used only on realistic-elevation maps: any MapKit renderer subclass is
/// drawn as raster tiles, while the stock renderers on a flat map are drawn as
/// crisp vectors at the right width already.
final class ScreenWidthPolylineRenderer: MKPolylineRenderer {
    var displayZoomScale: LineDisplayZoomScale?
    override func applyStrokeProperties(to context: CGContext, atZoomScale zoomScale: MKZoomScale) {
        super.applyStrokeProperties(to: context, atZoomScale: zoomScale)
        if let scale = displayZoomScale?.value { context.setLineWidth(lineWidth / scale) }
    }
}

extension ScreenWidthPolylineRenderer {
    convenience init(polyline: MKPolyline, displayZoomScale: LineDisplayZoomScale) {
        self.init(polyline: polyline)
        self.displayZoomScale = displayZoomScale
    }
}

final class ScreenWidthMultiPolylineRenderer: MKMultiPolylineRenderer {
    var displayZoomScale: LineDisplayZoomScale?
    override func applyStrokeProperties(to context: CGContext, atZoomScale zoomScale: MKZoomScale) {
        super.applyStrokeProperties(to: context, atZoomScale: zoomScale)
        if let scale = displayZoomScale?.value { context.setLineWidth(lineWidth / scale) }
    }
}
extension ScreenWidthMultiPolylineRenderer {
    convenience init(multiPolyline: MKMultiPolyline, displayZoomScale: LineDisplayZoomScale) {
        self.init(multiPolyline: multiPolyline)
        self.displayZoomScale = displayZoomScale
    }
}
/// Start / end marker or "Off-trail" / "Unverified connection" tag of the
/// planned route preview.
final class PlannedRouteAnnotation: MKPointAnnotation {
    enum Role { case start, end, tag(PlannedRoute.Kind), step }
    var role: Role = .start
    /// Step badges: the step numbers that begin here ("3" or "3, 9").
    var stepNumbers: [Int] = []
    var isSelectedStep = false
}

/// A trailhead parking / access pin (see `Area.parking`). A distinct class lets
/// the Coordinator's `viewFor` render it without touching other annotations
/// MapKit manages. `isTrailhead` (a federal BLM/USFS trailhead POINT, not a
/// parking lot) switches it from the blue "P" parking marker to a green
/// trailhead marker so we never mislabel a trail start as a parking lot.
final class ParkingAnnotation: MKPointAnnotation {
    var isTrailhead = false
    /// True only for a lot inside the selected trail's near threshold. Area-wide
    /// "show all" markers have neither selected-trail role.
    var isNearSelectedTrail = false
    /// True only for an intentional closest-known fallback outside the near
    /// trailhead threshold. Camera containment applies to near access markers;
    /// fallback markers instead carry an explicit distance in their semantics.
    var isFarFallback = false
}

struct MapKitMapView: UIViewRepresentable {
    let area: Area
    let activeRecording: ActiveRecording?
    /// Trail-snapped "walked here" runs for the cyan lifetime halo.
    /// TrailMapView snaps every past hike's GPS onto the trail
    /// polylines (`trailSnappedHaloRuns`) and passes the result as a
    /// single group; each inner array is a run of trail nodes, so the
    /// overlay follows the trail exactly rather than the raw GPS
    /// scatter. The outer array is just the grouping the renderer
    /// flattens — kept `[[[…]]]` to match the overlay-rebuild path.
    let haloSegments: [[[CLLocationCoordinate2D]]]
    /// On-trail-filtered segments of the **live** recording's GPS
    /// path. While a recording is active, TrailMapView rebuilds
    /// this at ~1 Hz against the same spatial grid the past-hike
    /// halo uses; we render the segments in a brighter style so
    /// the user can see which parts of their live walk are
    /// counting toward coverage. Empty when no recording is
    /// active.
    let liveHaloSegments: [[CLLocationCoordinate2D]]
    /// Segments of the *selected* trail that the user has walked
    /// since the last completion of that trail. Rendered in
    /// orange on top of the blue trail-highlight so the user sees
    /// "blue = still to walk for the next completion, orange =
    /// already covered this cycle." Empty when nothing's selected
    /// (or when the user has walked nothing of the selected trail
    /// since its last completion).
    let selectedTrailWalkedSegments: [[CLLocationCoordinate2D]]
    @Binding var selectedTrailId: String?
    /// "Plan a Hike" pick mode. When set, a tap reports the tapped coordinate
    /// here and does not select a trail. nil = normal tap-to-select.
    var onPickCoordinate: ((CLLocationCoordinate2D) -> Void)? = nil
    /// "Plan a Hike" route preview, drawn above the trail lines and below the
    /// recording line. nil draws nothing.
    var plannedRoute: PlannedRoute? = nil
    /// Index into `plannedRoute.steps` whose leg is highlighted. nil = none.
    var plannedRouteSelectedStep: Int? = nil
    /// Show every lot in the area rather than only the selected trail's few.
    /// Off by default — see StorageKeys.showAllParking for why the per-trail
    /// gate stays strict and this exists alongside it.
    var showAllParking: Bool = false
    /// How many lots the global parking pool has loaded — an INPUT, not read
    /// from the service here, so SwiftUI re-runs this representable when the pool
    /// lands. The pool arrives asynchronously after the first render and never
    /// touches `area.parking`, so without a stored input the pin signature would
    /// be unchanged and the pins would never redraw to include pooled lots.
    /// Defaults to 0 for the recording map, which updates every GPS tick anyway.
    var parkingPoolCount: Int = 0
    /// nil = render every trail. Non-nil = only render trails whose
    /// id is in this set, plus the recording trail and the selected
    /// trail (which are exempt so the user can always see what
    /// they tapped / are recording).
    let visibleTrailIds: Set<String>?
    /// Set of trail ids that are marked complete in ProgressService.
    /// Passed in as a flat set rather than the service itself so
    /// SwiftUI's diffing can detect changes via Equatable rather
    /// than via Observation. Keeps `updateUIView` cheap.
    let completedTrailIds: Set<String>
    /// Where to point the camera. Re-applied when `cameraTick`
    /// changes (and only then).
    let cameraTarget: MapTarget
    let cameraTick: Int
    let showsUserLocation: Bool
    /// MapKit's own user-tracking. `.none` for free pan,
    /// `.follow` / `.followWithHeading` to make the camera glue to
    /// the user. TrailMapView translates its tri-state
    /// MapTrackingMode into this and the headed-camera math, which
    /// is applied via cameraTarget when the mode flips.
    let userTrackingMode: MKUserTrackingMode
    /// Compass heading in degrees from true north, or nil when unavailable.
    ///
    /// Apple's stock blue dot only draws a facing cone while the camera is in
    /// `.followWithHeading`; in free-pan or plain follow it's a bare dot, so on
    /// a trail you can't tell which way you're pointed without moving. We draw
    /// our own dot and rotate it by this, so direction is always visible.
    let userHeading: Double?

    /// Screenshot-only synthetic user dot. When non-nil, a system-look
    /// blue location dot is pinned at this coordinate. Exists because
    /// MKMapView's built-in dot (`showsUserLocation`) only renders once
    /// CoreLocation authorization actually lands, and CI's
    /// `simctl privacy grant` provably doesn't land in time on cold
    /// simulators — the recording screenshot captured dot-less.
    /// TrailMapView only ever passes non-nil under DEBUG +
    /// `--uitest-recording`; every production path leaves it nil.
    var demoUserDot: CLLocationCoordinate2D? = nil

    /// Real rendered viewport boundaries for selected-marker auditing. These
    /// are measured from the map's top edge and physical sheet extent and do
    /// not include optional aesthetic margins used by route framing.
    var selectedMarkerVisibleInsets: MapViewportInsets = .zero
    /// Changes only when TrailMapView requests a post-settle selected-marker
    /// audit. A correction camera move advances it again for one bounded
    /// recheck after animation.
    var selectedMarkerAuditGeneration: Int = 0
    var onSelectedNearMarkerOcclusion: ((MapViewportInsets) -> Void)? = nil

    /// Fires when a user gesture (pinch / pan / double-tap) changes
    /// the visible region. TrailMapView uses this to re-snap the
    /// camera to the user in follow modes — without this hook, a
    /// stationary user who pinches to zoom would see the dot drift
    /// off-center (because pinch holds its midpoint, not the user
    /// dot) and stay there until the next GPS update, which never
    /// comes if they're not moving.
    let onUserGestureRegionChange: (() -> Void)?

    /// Fires the moment the user starts moving the camera by hand — a pan,
    /// pinch or rotation beginning on the map, or a zoom tap (double-tap,
    /// two-finger tap) landing. TrailMapView uses it to stop re-fitting its
    /// opening framing as the sheet moves: from this point the camera is the
    /// user's.
    ///
    /// Deliberately NOT derived from `onUserGestureRegionChange`. That hook
    /// classifies a region change as user-driven by counting our own
    /// programmatic changes in and out through `regionDidChangeAnimated`, and
    /// the count is only approximate: the first layout of the map and the
    /// un-counted initial `setRegion` in `makeUIView` both report a change
    /// nobody asked for, and an animated change cut short by the next one
    /// may never report its end. Either error is fatal for a one-way latch —
    /// a spurious report releases the framing before it has settled, a
    /// missed one re-fits over a map the user has panned. Instead, when a
    /// region change starts, we ask MapKit's OWN gesture recognizers whether
    /// one of them is driving it (`UserCameraGestureLatch`): a finger on the
    /// map and nothing else. We add no pan / pinch / rotate recognizers of
    /// our own — on iOS 27.3 such observers stopped MapKit's gestures from
    /// moving the map at all.
    var onUserCameraGestureBegan: (() -> Void)? = nil

    /// Decides, from the states of MapKit's own gesture recognizers, when a
    /// region change is the user's finger, and reports it once per gesture.
    /// Pure so it can be unit-tested without a map.
    struct UserCameraGestureLatch {
        private(set) var reported = false

        /// A recognizer in one of these states is moving the camera right
        /// now: the continuous ones (pan / pinch / rotate) at `.began` /
        /// `.changed`, the discrete zoom taps at `.ended` (== `.recognized`),
        /// the state they are in while their zoom starts. Programmatic
        /// camera moves leave every recognizer at `.possible`.
        static func isUserGestureActive(_ states: [UIGestureRecognizer.State]) -> Bool {
            states.contains { $0 == .began || $0 == .changed || $0 == .ended }
        }

        /// Called from `regionWillChangeAnimated`. True exactly once per
        /// user gesture: the first region change a finger drives.
        mutating func regionWillChange(recognizerStates: [UIGestureRecognizer.State]) -> Bool {
            guard !reported, Self.isUserGestureActive(recognizerStates) else { return false }
            reported = true
            return true
        }

        /// Called from `regionDidChangeAnimated`: the gesture's camera move
        /// is over, so the next one reports again.
        mutating func regionDidChange() {
            reported = false
        }
    }

    /// User-selected map style from Settings → Display. Re-applied
    /// in `updateUIView` so flipping the picker propagates to the
    /// open map without re-creating the view.
    @AppStorage(StorageKeys.mapStyle) private var mapStyle: MapStylePreference = .standard

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeUIView(context: Context) -> MKMapView {
        let mv = MKMapView()
        mv.delegate = context.coordinator
        mv.pointOfInterestFilter = .excludingAll
        // Configuration API, not legacy `mapType` — see
        // `MapStylePreference.makeMapConfiguration` (iOS 27.3 satellite pan bug).
        mv.preferredConfiguration = mapStyle.makeMapConfiguration()
        context.coordinator.appliedMapStyle = mapStyle
        context.coordinator.lineRenderersRealistic = Coordinator.usesScreenWidthLines(mv)
        mv.isPitchEnabled = false
        mv.isRotateEnabled = true
        mv.showsCompass = true
        mv.showsScale = true
        mv.showsUserLocation = showsUserLocation
        // Synthetic screenshot dot (see `demoUserDot` doc). Added once
        // here — the demo recording is injected in-memory at launch and
        // never moves, so there's nothing to update.
        if let dot = demoUserDot {
            let ann = DemoUserDotAnnotation()
            ann.coordinate = dot
            mv.addAnnotation(ann)
        }
        // Push MKMapView's built-in compass (top-right) down below
        // AreaView's favorite-heart button. MKMapView positions the
        // compass with respect to its `layoutMargins.top` — a 60pt
        // top margin clears the heart row, which sits at ~y=8 with
        // its own 36pt height. Without this, heart + compass stack
        // in the same screen cell and read as a single jumbled glyph.
        mv.layoutMargins = UIEdgeInsets(top: 60, left: 0, bottom: 0, right: 0)

        // Initial camera (no animation — instant frame).
        Self.applyCameraTarget(cameraTarget, to: mv, animated: false)

        context.coordinator.mapView = mv
        // Halos first, then trails — within MKMapView's `.aboveRoads`
        // overlay level, insertion order is z-order. Halos must
        // render UNDER trails so the user's past-hike highlight sits
        // beneath the colored trail outlines.
        context.coordinator.rebuildHaloOverlays(on: mv, segments: haloSegments)
        context.coordinator.rebuildTrailOverlays(on: mv, from: area)
        context.coordinator.rebuildSelectedTrailWalkedOverlays(on: mv, segments: selectedTrailWalkedSegments)
        context.coordinator.rebuildLiveTrailSnappedOverlays(on: mv, segments: liveHaloSegments)
        if let rec = activeRecording {
            context.coordinator.updateRecordingOverlays(on: mv, path: rec.path)
            context.coordinator.lastRecordingPathCount = rec.path.count
            context.coordinator.lastRecordingIdentity = rec.recordingId
                ?? "\(rec.areaId)|\(rec.startedAt.timeIntervalSinceReferenceDate)"
        }
        context.coordinator.lastAreaId = area.id
        context.coordinator.lastCameraTick = cameraTick
        context.coordinator.lastSelectedTrailId = selectedTrailId
        context.coordinator.lastRecordingTrailId = activeRecording?.trailId
        context.coordinator.lastVisibleTrailIds = visibleTrailIds
        context.coordinator.lastCompletedTrailIds = completedTrailIds
        context.coordinator.lastHaloHashes = Self.haloHashes(haloSegments)
        context.coordinator.lastLiveTrailSnappedHash = Self.liveHaloHash(liveHaloSegments)
        context.coordinator.lastSelectedTrailWalkedHash = Self.liveHaloHash(selectedTrailWalkedSegments)

        // Tap-to-select. Uses a UIGestureRecognizerDelegate that
        // returns false from shouldRecognizeSimultaneouslyWithGestureRecognizer
        // for MKMapView's internal pinch / pan so taps don't
        // accidentally fire mid-pan.
        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.handleTap(_:)))
        tap.delegate = context.coordinator
        mv.addGestureRecognizer(tap)
        context.coordinator.selectionTap = tap
        // User camera gestures are detected from MapKit's own recognizers in
        // `regionWillChangeAnimated` (see `onUserCameraGestureBegan`) — no
        // extra recognizers that could compete with MapKit's pan / pinch.
        return mv
    }

    func updateUIView(_ mapView: MKMapView, context: Context) {
        // Stamp the start time so we can publish the per-update
        // duration into MapDiagnostics for the debug HUD. Cheap
        // when the HUD is off (no observer is subscribed) — the
        // measurement itself is a CACurrentMediaTime call.
        let startTime = CACurrentMediaTime()
        defer {
            let elapsedMs = (CACurrentMediaTime() - startTime) * 1000
            MapDiagnostics.shared.lastUpdateDurationMs = elapsedMs
            MapDiagnostics.shared.overlayCount = mapView.overlays.count
        }

        let coord = context.coordinator
        // Refresh the parent reference so the coordinator's callbacks
        // and delegate methods see the latest closures and bindings.
        coord.parent = self

        // Layers that are (re)added below land above the planned route; the
        // route is moved back to the front once, after step 5.
        var layersReAdded = false
        // 1) Trail overlays — only rebuild when the area itself changes.
        // (Trail set within an area is stable for the area's lifetime.)
        if coord.lastAreaId != area.id {
            layersReAdded = true
            coord.rebuildTrailOverlays(on: mapView, from: area)
            coord.lastAreaId = area.id
            // Force re-style on next pass since renderer dict was reset.
            coord.lastSelectedTrailId = nil
            coord.lastRecordingTrailId = nil
            coord.lastCompletedTrailIds = []
        }

        // 1b) Parking pins — gated on the parking SET, not area.id, because
        // parking can arrive in a later in-place area update (same id) after
        // the first render (e.g. a background refetch that adds the layer).
        // Gating on area.id alone would draw zero pins and never revisit.
        // Declutter: parking is HIDDEN while browsing; only the selected
        // trail's ≤3 nearest lots draw. So the signature includes the
        // selection — tapping a trail (or clearing it) rebuilds the pins.
        let pool = ParkingPoolService.shared
        let parkingSig = Self.parkingSig(area.parking, selectedTrailId: selectedTrailId,
                                         showAll: showAllParking,
                                         poolCount: parkingPoolCount)
        if parkingSig != coord.lastParkingSig {
            // Merge HERE, not in the Coordinator: `ParkingPoolService` is
            // @MainActor and the Coordinator is deliberately non-isolated (see
            // its doc comment), and doing it behind the signature check keeps the
            // show-all case — which walks every trail's endpoints — off the
            // per-update path.
            let lots: [ParkingLot] = showAllParking
                ? pool.merged(with: area.parking, forAnyOf: area.trails)
                : (area.trails.first { $0.id == selectedTrailId }
                    .map { pool.merged(with: area.parking, for: $0) } ?? [])
            coord.rebuildParkingAnnotations(on: mapView, from: area, lots: lots,
                                            selectedTrailId: selectedTrailId,
                                            showAll: showAllParking)
            coord.lastParkingSig = parkingSig
        }

        // 2) Halo overlays — rebuild when the per-hike segment hashes
        // change (count change, or a new hike replaced one).
        let newHaloHashes = Self.haloHashes(haloSegments)
        if newHaloHashes != coord.lastHaloHashes {
            layersReAdded = true
            coord.rebuildHaloOverlays(on: mapView, segments: haloSegments)
            coord.lastHaloHashes = newHaloHashes
        }

        // 2b) Planned route preview — only when the route itself changes.
        if coord.lastPlannedRoute != plannedRoute {
            coord.applyPlannedRoute(plannedRoute, on: mapView)
            coord.lastPlannedRoute = plannedRoute
            coord.applyPlannedSteps(plannedRoute, selected: plannedRouteSelectedStep, on: mapView)
        } else if coord.lastPlannedStep != plannedRouteSelectedStep {
            coord.applyPlannedSteps(plannedRoute, selected: plannedRouteSelectedStep, on: mapView)
        }
        // 3) Live halo — rebuild when the per-segment hash changes.
        // TrailMapView throttles recompute to ~1 Hz so this rebuild
        // is at most 1×/second during recording, free otherwise.
        let newLiveHaloHash = Self.liveHaloHash(liveHaloSegments)
        if newLiveHaloHash != coord.lastLiveTrailSnappedHash {
            coord.rebuildLiveTrailSnappedOverlays(on: mapView, segments: liveHaloSegments)
            coord.lastLiveTrailSnappedHash = newLiveHaloHash
        }

        // 3b) Walked-since-completion overlay for the selected
        // trail — rebuild when the per-segment hash changes.
        // Updates on selection change or when pastHikes grows.
        let newSelectedWalkedHash = Self.liveHaloHash(selectedTrailWalkedSegments)
        if newSelectedWalkedHash != coord.lastSelectedTrailWalkedHash {
            coord.rebuildSelectedTrailWalkedOverlays(on: mapView, segments: selectedTrailWalkedSegments)
            coord.lastSelectedTrailWalkedHash = newSelectedWalkedHash
        }

        // 4) Recording overlays — one polyline per continuous GPS run.
        // A normal append replaces only the growing final run. Finalized runs
        // and every unrelated map layer keep their object identity.
        if let rec = activeRecording {
            let identity = rec.recordingId
                ?? "\(rec.areaId)|\(rec.startedAt.timeIntervalSinceReferenceDate)"
            if coord.lastRecordingIdentity != identity {
                coord.removeRecordingOverlays(from: mapView)
                coord.lastRecordingPathCount = 0
                coord.lastRecordingIdentity = identity
            }
            if coord.lastRecordingPathCount != rec.path.count {
                coord.updateRecordingOverlays(on: mapView, path: rec.path)
                coord.lastRecordingPathCount = rec.path.count
            }
        } else if !coord.recordingOverlays.isEmpty {
            coord.removeRecordingOverlays(from: mapView)
            coord.lastRecordingPathCount = 0
            coord.lastRecordingIdentity = nil
        }

        // 4) Re-style trail renderers when selection / recording /
        // completion set changes affect visual state.
        let oldSel = coord.lastSelectedTrailId
        let oldRec = coord.lastRecordingTrailId
        let newSel = selectedTrailId
        let newRec = activeRecording?.trailId
        let completedChanged = coord.lastCompletedTrailIds != completedTrailIds
        if oldSel != newSel || oldRec != newRec || completedChanged {
            let oldHighlight = oldRec ?? oldSel
            let newHighlight = newRec ?? newSel
            let dimmingFlipped = (oldHighlight == nil) != (newHighlight == nil)
            if dimmingFlipped || completedChanged {
                // All other trails change dim state, or all completions
                // changed. Restyle every trail's renderer.
                coord.restyleAllTrailRenderers()
            } else {
                // Only the highlighted-trail identity changed.
                // Restyle the two affected trails; everyone else's
                // state is unchanged.
                if let id = oldHighlight { coord.restyleTrailRenderer(trailId: id) }
                if let id = newHighlight, id != oldHighlight {
                    coord.restyleTrailRenderer(trailId: id)
                }
            }
            coord.lastSelectedTrailId = newSel
            coord.lastRecordingTrailId = newRec
            coord.lastCompletedTrailIds = completedTrailIds
        }

        // 5) Filter changes — show / hide overlays based on
        // `visibleTrailIds`. Exempt selected / recording trails.
        if coord.lastVisibleTrailIds != visibleTrailIds {
            layersReAdded = true
            coord.applyVisibleTrailFilter(
                on: mapView,
                allowed: visibleTrailIds,
                exempt: [newSel, newRec].compactMap { $0 }
            )
            coord.lastVisibleTrailIds = visibleTrailIds
        }
        // Trail, halo and filter overlays added after the route would draw over
        // it, so move the route back to the front.
        if layersReAdded { coord.bringPlannedRouteToFront(on: mapView) }

        // 6) Camera moves. Only animate when the caller bumps the
        // tick — we don't want stale view-state updates to re-frame
        // the map. Bump the pending-programmatic counter so the
        // post-animation `regionDidChangeAnimated` knows this change
        // was ours, not a user gesture.
        if coord.lastCameraTick != cameraTick {
            coord.pendingProgrammaticRegionChanges += 1
            Self.applyCameraTarget(cameraTarget, to: mapView, animated: true)
            coord.lastCameraTick = cameraTick
        }

        // 7) User-location + tracking mode.
        if coord.appliedMapStyle != mapStyle {
            mapView.preferredConfiguration = mapStyle.makeMapConfiguration()
            coord.appliedMapStyle = mapStyle
            coord.refreshLineDisplayZoomScale(on: mapView, redraw: true)
            coord.reloadLineRenderersIfElevationChanged(on: mapView)
        }
        if mapView.showsUserLocation != showsUserLocation {
            mapView.showsUserLocation = showsUserLocation
        }
        if mapView.userTrackingMode != userTrackingMode {
            mapView.setUserTrackingMode(userTrackingMode, animated: true)
        }
        // Keep the facing cone pointed the right way on every heading update.
        coord.pendingHeading = userHeading
        coord.applyHeadingRotation(on: mapView)
    }

    /// A selected-marker audit is complete only when every expected annotation
    /// produced one finite, positive-sized rendered frame.
    nonisolated static func markerFramesAreComplete(
        _ frames: [CGRect],
        expectedCount: Int
    ) -> Bool {
        guard expectedCount >= 0, frames.count == expectedCount else { return false }
        return frames.allSatisfy { frame in
            [frame.minX, frame.minY, frame.maxX, frame.maxY].allSatisfy(\.isFinite)
                && frame.width > 0
                && frame.height > 0
        }
    }

    /// Extra route-fit insets needed to place complete rendered marker frames
    /// inside the real control-to-sheet viewport. Fully visible frames return
    /// zero even when they cross an optional aesthetic edge margin.
    nonisolated static func markerOcclusionCorrection(
        markerFrames: [CGRect],
        mapBounds: CGRect,
        visibleInsets: MapViewportInsets,
        padding: CGFloat = 8
    ) -> MapViewportInsets {
        let values = [
            mapBounds.minX, mapBounds.minY, mapBounds.maxX, mapBounds.maxY,
            visibleInsets.top, visibleInsets.leading,
            visibleInsets.bottom, visibleInsets.trailing, padding,
        ]
        guard values.allSatisfy({ $0.isFinite }),
              mapBounds.width > 0,
              mapBounds.height > 0,
              padding >= 0 else { return .zero }

        let visible = CGRect(
            x: mapBounds.minX + max(0, visibleInsets.leading),
            y: mapBounds.minY + max(0, visibleInsets.top),
            width: max(
                0,
                mapBounds.width - max(0, visibleInsets.leading)
                    - max(0, visibleInsets.trailing)
            ),
            height: max(
                0,
                mapBounds.height - max(0, visibleInsets.top)
                    - max(0, visibleInsets.bottom)
            )
        )
        guard visible.width > 0, visible.height > 0 else { return .zero }

        var correction = MapViewportInsets.zero
        for frame in markerFrames {
            let frameValues = [frame.minX, frame.minY, frame.maxX, frame.maxY]
            guard frameValues.allSatisfy({ $0.isFinite }),
                  frame.width > 0,
                  frame.height > 0 else { continue }
            if frame.minY < visible.minY {
                correction.top = max(correction.top, visible.minY - frame.minY + padding)
            }
            if frame.minX < visible.minX {
                correction.leading = max(
                    correction.leading,
                    visible.minX - frame.minX + padding
                )
            }
            if frame.maxY > visible.maxY {
                correction.bottom = max(
                    correction.bottom,
                    frame.maxY - visible.maxY + padding
                )
            }
            if frame.maxX > visible.maxX {
                correction.trailing = max(
                    correction.trailing,
                    frame.maxX - visible.maxX + padding
                )
            }
        }
        return correction
    }

    /// Consume one selected-marker audit generation only for a correction that
    /// will actually be emitted. A complete zero-correction observation leaves
    /// the generation open so a later callback for the same camera generation
    /// can report newly occluded rendered frames.
    nonisolated static func consumeMarkerAuditGenerationIfNeeded(
        generation: Int,
        correction: MapViewportInsets,
        lastConsumedGeneration: inout Int
    ) -> Bool {
        guard generation > 0,
              generation != lastConsumedGeneration,
              correction != .zero else { return false }
        lastConsumedGeneration = generation
        return true
    }

    // MARK: - Camera helpers

    private static func applyCameraTarget(_ target: MapTarget,
                                          to mapView: MKMapView,
                                          animated: Bool) {
        switch target {
        case .region(let lat, let lon, let latDelta, let lonDelta):
            // Defense in depth: MKCoordinateSpan REJECTS an out-of-range span and
            // setRegion then traps, killing the app rather than mis-framing. The
            // known producer (an antimeridian-crossing area asking for a 441°
            // longitude span) is fixed at source in TrailMapView.lonCenterAndSpan,
            // but a bad span must never be able to crash the map again.
            let safeLat = min(max(latDelta, 0.0001), 180)
            let safeLon = min(max(lonDelta, 0.0001), 360)
            let region = MKCoordinateRegion(
                center: CLLocationCoordinate2D(latitude: lat, longitude: lon),
                span: MKCoordinateSpan(latitudeDelta: safeLat, longitudeDelta: safeLon)
            )
            guard CLLocationCoordinate2DIsValid(region.center) else { return }
            mapView.setRegion(region, animated: animated)
        case .camera(let lat, let lon, let distance, let heading):
            let cam = MKMapCamera(
                lookingAtCenter: CLLocationCoordinate2D(latitude: lat, longitude: lon),
                fromDistance: distance,
                pitch: 0,
                heading: heading
            )
            mapView.setCamera(cam, animated: animated)
        case .followCenter(let rawLat, let rawLon, let heading, let bottomInset):
            // Shift the camera center so the user dot lands at the
            // VISIBLE center (above the trail-list panel) regardless
            // of zoom AND of heading. Two parts to get right:
            //
            //   1. Scale — meters per screen point. Derived from a
            //      pair of MKMapView.convert() calls 100 points apart
            //      on the live map. This is heading-invariant (we
            //      measure the geodesic distance between two screen
            //      points), so the heading we're about to APPLY can't
            //      contaminate the scale.
            //   2. Direction — bearing trig with the NEW heading we're
            //      about to set, not the map's current heading. If we
            //      use convert() directly to compute the shift vector
            //      (as #174 did), the projection uses the live map's
            //      OLD heading and the dot drifts off the visible
            //      center each frame; for followHeading that drift
            //      accumulates into "keep having to snap back."
            //
            // For followHeading mode, MKMapView rotates around the
            // shifted centerCoordinate, so the dot's screen position
            // stays at the visible center frame-to-frame.
            let bounds = mapView.bounds
            let p0 = CGPoint(x: bounds.midX, y: bounds.midY)
            let p1 = CGPoint(x: bounds.midX, y: bounds.midY + 100)
            let c0 = mapView.convert(p0, toCoordinateFrom: mapView)
            let c1 = mapView.convert(p1, toCoordinateFrom: mapView)
            let cosLat = max(0.0001, cos(rawLat * .pi / 180))
            let dLat100 = (c1.latitude - c0.latitude) * 111_000
            let dLon100 = (c1.longitude - c0.longitude) * 111_000 * cosLat
            let metersPer100Points = sqrt(dLat100 * dLat100 + dLon100 * dLon100)
            let metersPerPoint = max(0.0001, metersPer100Points / 100)
            let shiftMeters = (bottomInset / 2) * metersPerPoint
            // Apply shift in NEW heading's screen-down direction.
            // For heading=0 (north up) screen-down = south; for
            // heading θ it's bearing (θ + 180°) measured from north.
            let rad = (heading ?? 0) * .pi / 180
            let dLat = -shiftMeters * cos(rad) / 111_000
            let dLon = -shiftMeters * sin(rad) / (111_000 * cosLat)
            let shifted = CLLocationCoordinate2D(
                latitude: rawLat + dLat,
                longitude: rawLon + dLon
            )
            if let heading {
                // followHeading: also rotate. Preserve altitude + pitch
                // from the live camera so only center + heading change.
                let current = mapView.camera
                let cam = MKMapCamera()
                cam.centerCoordinate = shifted
                cam.altitude = current.altitude
                cam.pitch = current.pitch
                cam.heading = heading
                mapView.setCamera(cam, animated: animated)
            } else {
                mapView.setCenter(shifted, animated: animated)
            }
        }
    }

    /// Cheap content-fingerprint of the halo segments. Coordinator
    /// uses this to detect "a new hike was added" vs "same data being
    /// re-supplied because some unrelated state changed", so we can
    /// avoid recreating overlays we already have. Hashes the segment
    /// count per hike + the first/last coord of each segment — enough
    /// to detect any meaningful change, ~free to compute.
    private static func haloHashes(_ segments: [[[CLLocationCoordinate2D]]]) -> [Int] {
        segments.map { hike in
            var h = Hasher()
            h.combine(hike.count)
            for seg in hike {
                h.combine(seg.count)
                if let first = seg.first {
                    h.combine(first.latitude); h.combine(first.longitude)
                }
                if let last = seg.last {
                    h.combine(last.latitude); h.combine(last.longitude)
                }
            }
            return h.finalize()
        }
    }

    /// Stable signature of the parking set so the pin rebuild fires when
    /// parking changes (including nil -> populated) but not on unrelated
    /// updateUIView passes. -1 marks "no parking layer" (distinct from the
    /// `-2` sentinel that means "never evaluated").
    private static func parkingSig(_ lots: [ParkingLot]?, selectedTrailId: String?,
                                   showAll: Bool, poolCount: Int) -> Int {
        var h = Hasher()
        // The flag is part of the signature: flipping it changes what is drawn
        // while the area and selection are unchanged, so without this the pins
        // would not rebuild.
        h.combine(showAll)
        // Selection is part of the signature: parking is drawn per-selected-trail
        // now, so changing (or clearing) the selection must rebuild the pins.
        h.combine(selectedTrailId)
        // The pool lands ASYNCHRONOUSLY, after the first render, and it does not
        // touch `area.parking` — so without its size here the signature would be
        // unchanged and the pins would never redraw to include pooled lots. Comes
        // in as an INPUT (see `parkingPoolCount`) so the re-render is guaranteed
        // rather than dependent on observation reaching a representable. The pool
        // loads once per launch, so this changes at most once.
        h.combine(poolCount)
        guard let lots else { h.combine(-1); return h.finalize() }
        h.combine(lots.count)
        for lot in lots {
            h.combine(lot.lat); h.combine(lot.lon)
        }
        return h.finalize()
    }

    /// Same shape as `haloHashes` but flat — live halo is the
    /// segments from the single in-progress recording, not a
    /// per-hike outer list.
    static func liveHaloHash(_ segments: [[CLLocationCoordinate2D]]) -> Int {
        var h = Hasher()
        h.combine(segments.count)
        for seg in segments {
            h.combine(seg.count)
            if let first = seg.first {
                h.combine(first.latitude); h.combine(first.longitude)
            }
            if let last = seg.last {
                h.combine(last.latitude); h.combine(last.longitude)
            }
        }
        return h.finalize()
    }

    // MARK: - Coordinator

    /// MKMapView calls all delegate / target-action methods on the
    /// main thread. The Coordinator's mutable state (overlay dicts,
    /// last-snapshot diff fields) is only touched from those
    /// callbacks and from `updateUIView`, both of which run on main,
    /// so no explicit `@MainActor` annotation is needed.
    final class Coordinator: NSObject, MKMapViewDelegate, UIGestureRecognizerDelegate {
        var parent: MapKitMapView
        weak var mapView: MKMapView?

        // Trail state. Each trail is one MKMultiPolyline (one overlay,
        // one renderer), so styling updates are O(1) per affected
        // trail rather than O(segments-per-trail).
        var trailOverlays: [String: MKMultiPolyline] = [:]
        var trailRenderers: [String: MKMultiPolylineRenderer] = [:]
        /// Reverse lookup overlay → trailId for the tap hit-test.
        var trailIdByOverlay: [ObjectIdentifier: String] = [:]
        /// Trails currently hidden by `visibleTrailIds`. Their overlays
        /// are removed from the map; re-added on filter relax.
        var hiddenTrailIds: Set<String> = []

        var haloOverlays: [MKPolyline] = []
        // Planned route preview (Task 10). Casing + line per segment.
        var plannedRouteOverlays: [MKPolyline] = []
        var plannedRouteAnnotations: [PlannedRouteAnnotation] = []
        var lastPlannedRoute: PlannedRoute?
        // Round 6: numbered step badges. Round 7: in Walk through only the
        // current step's badge shows.
        var plannedStepAnnotations: [PlannedRouteAnnotation] = []
        var lastPlannedStep: Int?
        /// Steps starting within this distance share one badge ("3, 9").
        static let stepBadgeMergeMeters = 12.0
        static let plannedRouteColor = UIColor(red: 0.86, green: 0.0, blue: 0.52, alpha: 1)
        /// Planned route ground already walked: the route color, faded.
        static let plannedRouteDoneColor = plannedRouteColor.withAlphaComponent(0.35)
        /// Walk through: the legs other than the current one keep this much
        /// of their opacity.
        static let plannedFade: CGFloat = PlannedRouteArrowsRenderer.fadeAlpha
        /// On-trail segments from the live recording. Tracked
        /// separately from `haloOverlays` because the renderer
        /// styles them brighter and at a different width to
        /// communicate "this is happening NOW."
        var liveTrailSnappedOverlays: [MKPolyline] = []
        /// `ObjectIdentifier`s of overlays in `liveTrailSnappedOverlays`,
        /// for O(1) lookup in `rendererFor` (the renderer callback
        /// fires per overlay and doesn't know which collection an
        /// MKPolyline came from).
        var liveTrailSnappedIds: Set<ObjectIdentifier> = []
        /// Walked-since-last-completion segments for the currently
        /// selected trail. Rendered in systemOrange on top of the
        /// trail's blue highlight. Tracked separately from the
        /// halos so the renderer can identify and style them
        /// (per-overlay closure doesn't carry context).
        var selectedTrailWalkedOverlays: [MKPolyline] = []
        var selectedTrailWalkedIds: Set<ObjectIdentifier> = []
        /// Raw recording runs in path order. Completed runs retain their
        /// overlays while only the growing final run is replaced.
        var recordingOverlays: [MKPolyline] = []
        var recordingOverlayIds: Set<ObjectIdentifier> = []

        // Diff snapshots — set in makeUIView and updated in
        // updateUIView so we know what changed since last pass.
        var lastAreaId: String = ""
        var lastSelectedTrailId: String? = nil
        var lastRecordingTrailId: String? = nil
        var lastVisibleTrailIds: Set<String>? = nil
        var lastCompletedTrailIds: Set<String> = []
        var lastCameraTick: Int = -1
        /// Style last pushed to `preferredConfiguration`; configurations
        /// aren't Equatable, so `updateUIView` diffs on this instead.
        var appliedMapStyle: MapStylePreference? = nil
        /// Screen scale the line renderers stroke against (see
        /// `LineDisplayZoomScale`); set only while elevation is realistic.
        let lineDisplayZoomScale = LineDisplayZoomScale()
        /// Scale the lines were last redrawn at.
        var lineRedrawZoomScale: MKZoomScale?
        /// Elevation the current line renderers were built for.
        var lineRenderersRealistic: Bool?

        var lastHaloHashes: [Int] = []
        var lastLiveTrailSnappedHash: Int = 0
        /// The live user-location view, so heading updates can rotate it
        /// without waiting for MapKit to re-request the annotation view.
        weak var userDotView: MKAnnotationView?
        /// Heading last applied, so we skip no-op transform writes.
        var lastAppliedHeading: Double = .nan

        /// Latest heading handed down from the SwiftUI layer.
        var pendingHeading: Double?
        /// Point count of the recording path last rendered, so recording
        /// overlays are reconciled only when the path actually changes.
        var lastRecordingPathCount: Int = 0
        /// Stable session identity prevents a replacement recording with the
        /// same point count from inheriting the prior session's overlays.
        var lastRecordingIdentity: String?
        /// Signature of the parking set last drawn. Parking arrives with the
        /// area but can also land in a later in-place update (same area.id), so
        /// gating the pin rebuild only on area.id change would miss it. -1 =
        /// never drawn.
        var lastParkingSig: Int = -2
        var lastSelectedTrailWalkedHash: Int = 0
        /// Last post-settle request whose rendered near markers were measured.
        /// One report per generation prevents duplicate delegate callbacks from
        /// accumulating the same correction before the camera moves.
        var lastSelectedMarkerAuditGeneration: Int = 0
        /// One incomplete generation may wait for MapKit's remaining annotation
        /// views. The first complete `didAdd` callback consumes this one-shot.
        var pendingSelectedMarkerAuditGeneration: Int?
        /// The latest generation granted a complete-zero next-run-loop recheck.
        /// It stays generation-sticky so later callbacks cannot schedule a
        /// second pass for the same camera result.
        var lastScheduledSelectedMarkerAuditGeneration: Int = 0

        init(parent: MapKitMapView) {
            self.parent = parent
            super.init()
        }

        /// Cyan stroke for completed trails, bridged from
        /// `Color.completedTrail` so the map and the trail-list thumbnail stay
        /// identical.
        static let completedTrail = UIColor(Color.completedTrail)

        /// Counter for in-flight programmatic region changes initiated
        /// by `applyCameraTarget`. `mapView(_:regionDidChangeAnimated:)`
        /// uses it to distinguish our own setCamera/setRegion/setCenter
        /// calls from user-driven gestures (pinch / pan). Without this
        /// the snap-back-to-user logic would treat our OWN snap as a
        /// new user gesture and re-fire, blocking the camera from ever
        /// settling.
        var pendingProgrammaticRegionChanges = 0

        /// System-look location dot: soft shadow, white ring,
        /// systemBlue center. Drawn once — 26pt matches the visual
        /// weight of MKMapView's real dot at screenshot zoom.
        /// The user dot WITH a facing cone. Drawn pointing straight up (north);
        /// `applyHeadingRotation` spins the whole view to the real bearing.
        /// The cone sits behind the dot so the dot still reads as the position
        /// and the cone as "which way you're looking", the same language Apple
        /// Maps uses in its heading mode.
        static let userHeadingDotImage: UIImage = {
            // Canvas sized for the cone, not the dot. This has been tuned from
            // both ends on device: at 44pt with a 20pt reach the cone barely
            // cleared the 18pt dot and faded to nothing before its tip ("super
            // small and hard to see"); at 84pt with a 38pt reach it read as
            // "gigantic". 60pt with a 26pt reach sits between them — the cone
            // still clears the dot by 17pt, which is what makes it legible at
            // any zoom, without becoming the loudest thing on the map.
            let size = CGSize(width: 60, height: 60)
            return UIGraphicsImageRenderer(size: size).image { ctx in
                let c = ctx.cgContext
                let mid = CGPoint(x: size.width / 2, y: size.height / 2)

                // Facing cone: a wedge fading out away from the dot. Reaches
                // ~3x the dot's radius so there is real cone to see, and stops
                // at 0.2 alpha rather than 0 so the tip doesn't vanish.
                let reach: CGFloat = 26
                // Slightly narrower too. Visibility comes from the opacity and
                // the white edge below, not from the wedge's width, so this is
                // the cheapest place to take size back.
                let half: CGFloat = .pi / 6.5        // ~28 deg each side
                let cone = UIBezierPath()
                cone.move(to: mid)
                cone.addArc(withCenter: mid, radius: reach,
                            startAngle: -.pi / 2 - half, endAngle: -.pi / 2 + half,
                            clockwise: true)
                cone.close()
                c.saveGState()
                c.addPath(cone.cgPath)
                c.clip()
                let colors = [UIColor.systemBlue.withAlphaComponent(0.95).cgColor,
                              UIColor.systemBlue.withAlphaComponent(0.20).cgColor] as CFArray
                if let g = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                                      colors: colors, locations: [0, 1]) {
                    c.drawLinearGradient(g,
                                         start: mid,
                                         end: CGPoint(x: mid.x, y: mid.y - reach),
                                         options: [])
                }
                c.restoreGState()

                // Thin white edge so the cone reads against dark satellite
                // imagery, where a translucent blue wedge alone disappears.
                c.setShadow(offset: .zero, blur: 3,
                            color: UIColor.black.withAlphaComponent(0.4).cgColor)
                UIColor.white.withAlphaComponent(0.9).setStroke()
                cone.lineWidth = 1.5
                cone.stroke()
                c.setShadow(offset: .zero, blur: 0, color: nil)

                // The dot itself: white ring around a blue core, matching the
                // demo dot so the two read as the same object.
                let dot = CGRect(x: mid.x - 9, y: mid.y - 9, width: 18, height: 18)
                c.setShadow(offset: .zero, blur: 4,
                            color: UIColor.black.withAlphaComponent(0.35).cgColor)
                UIColor.white.setFill()
                c.fillEllipse(in: dot)
                c.setShadow(offset: .zero, blur: 0, color: nil)
                UIColor.systemBlue.setFill()
                c.fillEllipse(in: dot.insetBy(dx: 3, dy: 3))
            }
        }()

        static let demoUserDotImage: UIImage = {
            let size = CGSize(width: 26, height: 26)
            return UIGraphicsImageRenderer(size: size).image { ctx in
                let c = ctx.cgContext
                c.setShadow(offset: .zero, blur: 4,
                            color: UIColor.black.withAlphaComponent(0.35).cgColor)
                UIColor.white.setFill()
                c.fillEllipse(in: CGRect(origin: .zero, size: size).insetBy(dx: 2, dy: 2))
                c.setShadow(offset: .zero, blur: 0, color: nil)
                UIColor.systemBlue.setFill()
                c.fillEllipse(in: CGRect(origin: .zero, size: size).insetBy(dx: 5, dy: 5))
            }
        }()

        // MARK: User camera gestures

        /// Our selection tap — the one recognizer on the map that is not
        /// MapKit's, so it is left out of the user-gesture check.
        weak var selectionTap: UITapGestureRecognizer?

        var userCameraGestureLatch = UserCameraGestureLatch()

    }
}

/// Map-coordinate math broken out from `MapKitMapView.Coordinator`
/// into a namespace so unit tests can reach it via
/// `@testable import SouthMountainExplorer`. Everything here is
/// pure — no MKMapView / UIKit state — so the tests run on any
/// platform the test bundle supports.
enum MapMath {
    /// Minimum flat-earth distance in meters from `tap` to the
    /// polyline described by `coords`. Returns `.infinity` for a
    /// polyline of fewer than two points.
    static func distanceFromPoint(_ tap: CLLocationCoordinate2D,
                                  toPolylineCoords coords: [CLLocationCoordinate2D]) -> Double {
        guard coords.count >= 2 else { return .infinity }
        let tapLat = tap.latitude * .pi / 180
        let metersPerLat = 111_000.0
        let metersPerLon = 111_000.0 * cos(tapLat)

        func toMeters(_ c: CLLocationCoordinate2D) -> (x: Double, y: Double) {
            let dx = (c.longitude - tap.longitude) * metersPerLon
            let dy = (c.latitude - tap.latitude) * metersPerLat
            return (dx, dy)
        }

        var best = Double.infinity
        for i in 0..<(coords.count - 1) {
            let a = toMeters(coords[i])
            let b = toMeters(coords[i + 1])
            let d = pointToSegmentDistance(px: 0, py: 0,
                                           ax: a.x, ay: a.y,
                                           bx: b.x, by: b.y)
            if d < best { best = d }
        }
        return best
    }

    /// Perpendicular distance from `(px, py)` to the line segment
    /// `(ax, ay)`–`(bx, by)`. Clamps to endpoints when the
    /// perpendicular foot lands outside the segment. Returns 0 for
    /// degenerate (zero-length) segments where the start point is
    /// the closest point.
    static func pointToSegmentDistance(px: Double, py: Double,
                                       ax: Double, ay: Double,
                                       bx: Double, by: Double) -> Double {
        let dx = bx - ax, dy = by - ay
        let lengthSq = dx * dx + dy * dy
        if lengthSq == 0 {
            let ex = px - ax, ey = py - ay
            return (ex * ex + ey * ey).squareRoot()
        }
        var t = ((px - ax) * dx + (py - ay) * dy) / lengthSq
        t = max(0, min(1, t))
        let qx = ax + t * dx, qy = ay + t * dy
        let ex = px - qx, ey = py - qy
        return (ex * ex + ey * ey).squareRoot()
    }

    /// Great-circle distance in meters between two lat/lon pairs via
    /// the haversine formula. Earth radius taken as 6 371 000 m
    /// (mean radius). Accurate to within ~0.5% at trail scale.
    /// See Utilities/Haversine.swift (atan2 form, `earthRadiusMeters`).
    static func haversineMeters(lat1: Double, lon1: Double,
                                lat2: Double, lon2: Double) -> Double {
        haversineDistance(lat1: lat1, lon1: lon1, lat2: lat2, lon2: lon2, radius: earthRadiusMeters)
    }
}
