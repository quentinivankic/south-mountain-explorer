import SwiftUI
import MapKit
import UIKit

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
enum MapTarget: Equatable {
    case region(centerLat: Double, centerLon: Double, latDelta: Double, lonDelta: Double)
    case camera(centerLat: Double, centerLon: Double, distance: Double, heading: Double)
    case followCenter(centerLat: Double, centerLon: Double, heading: Double?)
}

struct MapKitMapView: UIViewRepresentable {
    let area: Area
    let activeRecording: ActiveRecording?
    /// Per-past-hike on-trail-filtered GPS segments. Each inner array
    /// is one segment of one hike's halo path. TrailMapView still
    /// owns the spatial-grid filter that produces these (so the
    /// coordinate-grid logic stays alongside the other geometry
    /// caches it manages).
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
        mv.mapType = mapStyle.mkMapType
        mv.isPitchEnabled = false
        mv.isRotateEnabled = true
        mv.showsCompass = true
        mv.showsScale = true
        mv.showsUserLocation = showsUserLocation
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
        context.coordinator.rebuildLiveHaloOverlays(on: mv, segments: liveHaloSegments)
        if let rec = activeRecording, rec.path.count > 1 {
            context.coordinator.updateRecordingOverlay(on: mv, path: rec.path)
        }
        context.coordinator.lastAreaId = area.id
        context.coordinator.lastCameraTick = cameraTick
        context.coordinator.lastSelectedTrailId = selectedTrailId
        context.coordinator.lastRecordingTrailId = activeRecording?.trailId
        context.coordinator.lastVisibleTrailIds = visibleTrailIds
        context.coordinator.lastCompletedTrailIds = completedTrailIds
        context.coordinator.lastHaloHashes = Self.haloHashes(haloSegments)
        context.coordinator.lastLiveHaloHash = Self.liveHaloHash(liveHaloSegments)
        context.coordinator.lastSelectedTrailWalkedHash = Self.liveHaloHash(selectedTrailWalkedSegments)

        // Tap-to-select. Uses a UIGestureRecognizerDelegate that
        // returns false from shouldRecognizeSimultaneouslyWithGestureRecognizer
        // for MKMapView's internal pinch / pan so taps don't
        // accidentally fire mid-pan.
        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.handleTap(_:)))
        tap.delegate = context.coordinator
        mv.addGestureRecognizer(tap)
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

        // 1) Trail overlays — only rebuild when the area itself changes.
        // (Trail set within an area is stable for the area's lifetime.)
        if coord.lastAreaId != area.id {
            coord.rebuildTrailOverlays(on: mapView, from: area)
            coord.lastAreaId = area.id
            // Force re-style on next pass since renderer dict was reset.
            coord.lastSelectedTrailId = nil
            coord.lastRecordingTrailId = nil
            coord.lastCompletedTrailIds = []
        }

        // 2) Halo overlays — rebuild when the per-hike segment hashes
        // change (count change, or a new hike replaced one).
        let newHaloHashes = Self.haloHashes(haloSegments)
        if newHaloHashes != coord.lastHaloHashes {
            coord.rebuildHaloOverlays(on: mapView, segments: haloSegments)
            coord.lastHaloHashes = newHaloHashes
        }

        // 3) Live halo — rebuild when the per-segment hash changes.
        // TrailMapView throttles recompute to ~1 Hz so this rebuild
        // is at most 1×/second during recording, free otherwise.
        let newLiveHaloHash = Self.liveHaloHash(liveHaloSegments)
        if newLiveHaloHash != coord.lastLiveHaloHash {
            coord.rebuildLiveHaloOverlays(on: mapView, segments: liveHaloSegments)
            coord.lastLiveHaloHash = newLiveHaloHash
        }

        // 3b) Walked-since-completion overlay for the selected
        // trail — rebuild when the per-segment hash changes.
        // Updates on selection change or when pastHikes grows.
        let newSelectedWalkedHash = Self.liveHaloHash(selectedTrailWalkedSegments)
        if newSelectedWalkedHash != coord.lastSelectedTrailWalkedHash {
            coord.rebuildSelectedTrailWalkedOverlays(on: mapView, segments: selectedTrailWalkedSegments)
            coord.lastSelectedTrailWalkedHash = newSelectedWalkedHash
        }

        // 4) Recording overlay — live updates whenever the path grows.
        // Re-added every tick so it sits on top of the live halo
        // (both at .aboveLabels; insertion order = z-order).
        if let rec = activeRecording, rec.path.count > 1 {
            coord.updateRecordingOverlay(on: mapView, path: rec.path)
        } else if coord.recordingOverlay != nil {
            coord.removeRecordingOverlay(from: mapView)
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
            coord.applyVisibleTrailFilter(
                on: mapView,
                allowed: visibleTrailIds,
                exempt: [newSel, newRec].compactMap { $0 }
            )
            coord.lastVisibleTrailIds = visibleTrailIds
        }

        // 6) Camera moves. Only animate when the caller bumps the
        // tick — we don't want stale view-state updates to re-frame
        // the map.
        if coord.lastCameraTick != cameraTick {
            Self.applyCameraTarget(cameraTarget, to: mapView, animated: true)
            coord.lastCameraTick = cameraTick
        }

        // 7) User-location + tracking mode.
        let targetMapType = mapStyle.mkMapType
        if mapView.mapType != targetMapType {
            mapView.mapType = targetMapType
        }
        if mapView.showsUserLocation != showsUserLocation {
            mapView.showsUserLocation = showsUserLocation
        }
        if mapView.userTrackingMode != userTrackingMode {
            mapView.setUserTrackingMode(userTrackingMode, animated: true)
        }
    }

    // MARK: - Camera helpers

    private static func applyCameraTarget(_ target: MapTarget,
                                          to mapView: MKMapView,
                                          animated: Bool) {
        switch target {
        case .region(let lat, let lon, let latDelta, let lonDelta):
            let region = MKCoordinateRegion(
                center: CLLocationCoordinate2D(latitude: lat, longitude: lon),
                span: MKCoordinateSpan(latitudeDelta: latDelta, longitudeDelta: lonDelta)
            )
            mapView.setRegion(region, animated: animated)
        case .camera(let lat, let lon, let distance, let heading):
            let cam = MKMapCamera(
                lookingAtCenter: CLLocationCoordinate2D(latitude: lat, longitude: lon),
                fromDistance: distance,
                pitch: 0,
                heading: heading
            )
            mapView.setCamera(cam, animated: animated)
        case .followCenter(let lat, let lon, let heading):
            // Preserve user's current zoom. For follow (heading nil),
            // just pan; for followHeading, copy the current camera so
            // we keep its distance and only update center + heading.
            let center = CLLocationCoordinate2D(latitude: lat, longitude: lon)
            if let heading {
                let cam = mapView.camera.copy() as! MKMapCamera
                cam.centerCoordinate = center
                cam.heading = heading
                mapView.setCamera(cam, animated: animated)
            } else {
                mapView.setCenter(center, animated: animated)
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
        /// On-trail segments from the live recording. Tracked
        /// separately from `haloOverlays` because the renderer
        /// styles them brighter and at a different width to
        /// communicate "this is happening NOW."
        var liveHaloOverlays: [MKPolyline] = []
        /// `ObjectIdentifier`s of overlays in `liveHaloOverlays`,
        /// for O(1) lookup in `rendererFor` (the renderer callback
        /// fires per overlay and doesn't know which collection an
        /// MKPolyline came from).
        var liveHaloIds: Set<ObjectIdentifier> = []
        /// Walked-since-last-completion segments for the currently
        /// selected trail. Rendered in systemOrange on top of the
        /// trail's blue highlight. Tracked separately from the
        /// halos so the renderer can identify and style them
        /// (per-overlay closure doesn't carry context).
        var selectedTrailWalkedOverlays: [MKPolyline] = []
        var selectedTrailWalkedIds: Set<ObjectIdentifier> = []
        var recordingOverlay: MKPolyline?

        // Diff snapshots — set in makeUIView and updated in
        // updateUIView so we know what changed since last pass.
        var lastAreaId: String = ""
        var lastSelectedTrailId: String? = nil
        var lastRecordingTrailId: String? = nil
        var lastVisibleTrailIds: Set<String>? = nil
        var lastCompletedTrailIds: Set<String> = []
        var lastCameraTick: Int = -1
        var lastHaloHashes: [Int] = []
        var lastLiveHaloHash: Int = 0
        var lastSelectedTrailWalkedHash: Int = 0

        init(parent: MapKitMapView) {
            self.parent = parent
            super.init()
        }

        // MARK: Trail overlays

        func rebuildTrailOverlays(on mapView: MKMapView, from area: Area) {
            let removed = Array(trailOverlays.values) as [MKOverlay]
            mapView.removeOverlays(removed)
            trailOverlays.removeAll(keepingCapacity: true)
            trailRenderers.removeAll(keepingCapacity: true)
            trailIdByOverlay.removeAll(keepingCapacity: true)
            hiddenTrailIds.removeAll(keepingCapacity: true)

            for trail in area.trails {
                let polylines = trail.segments.compactMap { seg -> MKPolyline? in
                    guard seg.count >= 2 else { return nil }
                    let coords: [CLLocationCoordinate2D] = seg.compactMap { p in
                        guard p.count >= 2 else { return nil }
                        return CLLocationCoordinate2D(latitude: p[0], longitude: p[1])
                    }
                    guard coords.count >= 2 else { return nil }
                    return MKPolyline(coordinates: coords, count: coords.count)
                }
                guard !polylines.isEmpty else { continue }
                let multi = MKMultiPolyline(polylines)
                trailOverlays[trail.id] = multi
                trailIdByOverlay[ObjectIdentifier(multi)] = trail.id
                mapView.addOverlay(multi, level: .aboveRoads)
            }
        }

        func restyleAllTrailRenderers() {
            for (trailId, renderer) in trailRenderers {
                applyTrailStyle(renderer: renderer, trailId: trailId)
                renderer.setNeedsDisplay()
            }
        }

        func restyleTrailRenderer(trailId: String) {
            guard let renderer = trailRenderers[trailId] else { return }
            applyTrailStyle(renderer: renderer, trailId: trailId)
            renderer.setNeedsDisplay()
        }

        /// Resolve a trail's stroke color + line width + alpha from
        /// the parent state and write them onto the renderer. Mirrors
        /// the old `trailPolylineLayer` logic from TrailMapView.
        private func applyTrailStyle(renderer: MKMultiPolylineRenderer, trailId: String) {
            let p = parent
            let recordingTrailId = p.activeRecording?.trailId
            let selectedTrailId = p.selectedTrailId
            let highlightedTrailId = recordingTrailId ?? selectedTrailId

            let isRecordingThis = trailId == recordingTrailId
            let isSelected = trailId == selectedTrailId
            let isHighlighted = isRecordingThis || isSelected
            let isComplete = p.completedTrailIds.contains(trailId)

            // Resolve difficulty by trail lookup. O(n) but only runs
            // on style invalidation, not per frame.
            let difficulty = p.area.trails.first(where: { $0.id == trailId })?.difficulty

            // Selection is iOS-blue regardless of difficulty / completion
            // state. The previous selected-stays-mint behavior made a
            // selected completed trail nearly indistinguishable from
            // surrounding completed trails — same hue, only 6 vs 3 px
            // wider, easy to lose on a busy network. Completion state
            // is still communicated by the green checkmark in the trail
            // list row; the map's job during selection is to scream
            // "this is the trail you tapped." Recording purple still
            // wins over selection because the active recording is the
            // higher-priority state.
            let baseColor: UIColor
            if isRecordingThis {
                baseColor = .systemPurple
            } else if isSelected {
                baseColor = .systemBlue
            } else if isComplete {
                // .mint via UIKit — matches the SwiftUI .mint from build 8.
                baseColor = UIColor.systemMint
            } else {
                baseColor = Self.difficultyColor(difficulty)
            }
            let dimmed = (highlightedTrailId != nil && !isHighlighted)
            // Tightened dim alpha 0.5 → 0.35 so the selected trail's
            // bright blue pops harder against the surrounding tangle.
            let alpha: CGFloat = dimmed ? 0.35 : 1.0

            renderer.strokeColor = baseColor.withAlphaComponent(alpha)
            // Selected width bumped 6 → 9 so the selection still reads
            // as obviously distinct even when paralleling a completed
            // trail at full opacity (they overlap, both visible — width
            // is the differentiator).
            renderer.lineWidth = isRecordingThis ? 10 : (isSelected ? 9 : 3)
            renderer.lineCap = .round
            renderer.lineJoin = .round
        }

        private static func difficultyColor(_ difficulty: Difficulty?) -> UIColor {
            switch difficulty {
            case .easy: return .systemGreen
            case .moderate: return .systemOrange
            case .hard: return .systemRed
            case .none: return .systemGray
            }
        }

        func applyVisibleTrailFilter(on mapView: MKMapView,
                                     allowed: Set<String>?,
                                     exempt: [String]) {
            let exemptSet = Set(exempt)
            for (trailId, overlay) in trailOverlays {
                let shouldShow: Bool
                if let allowed {
                    shouldShow = allowed.contains(trailId) || exemptSet.contains(trailId)
                } else {
                    shouldShow = true
                }
                let isHidden = hiddenTrailIds.contains(trailId)
                if shouldShow && isHidden {
                    mapView.addOverlay(overlay, level: .aboveRoads)
                    hiddenTrailIds.remove(trailId)
                } else if !shouldShow && !isHidden {
                    mapView.removeOverlay(overlay)
                    trailRenderers.removeValue(forKey: trailId)
                    hiddenTrailIds.insert(trailId)
                }
            }
        }

        // MARK: Halo overlays

        func rebuildHaloOverlays(on mapView: MKMapView, segments: [[[CLLocationCoordinate2D]]]) {
            if !haloOverlays.isEmpty {
                mapView.removeOverlays(haloOverlays as [MKOverlay])
                haloOverlays.removeAll(keepingCapacity: true)
            }
            for hike in segments {
                for seg in hike where seg.count >= 2 {
                    let pl = MKPolyline(coordinates: seg, count: seg.count)
                    haloOverlays.append(pl)
                    // Insert at the bottom of `.aboveRoads` so halos
                    // render UNDER the trail outlines already in the
                    // level. Otherwise, when a new hike finishes
                    // mid-session, the freshly-added halo would land
                    // on TOP of every trail because the trail
                    // overlays are already in the map's overlay list.
                    mapView.insertOverlay(pl, at: 0, level: .aboveRoads)
                }
            }
        }

        // MARK: Live halo overlays

        func rebuildLiveHaloOverlays(on mapView: MKMapView, segments: [[CLLocationCoordinate2D]]) {
            if !liveHaloOverlays.isEmpty {
                mapView.removeOverlays(liveHaloOverlays as [MKOverlay])
                liveHaloOverlays.removeAll(keepingCapacity: true)
                liveHaloIds.removeAll(keepingCapacity: true)
            }
            for seg in segments where seg.count >= 2 {
                let pl = MKPolyline(coordinates: seg, count: seg.count)
                liveHaloOverlays.append(pl)
                liveHaloIds.insert(ObjectIdentifier(pl))
                // .aboveLabels so the live halo sits on top of trail
                // polylines (which live in .aboveRoads). The recording
                // polyline is also at .aboveLabels and re-added every
                // tick after this rebuild, so it stays on top of the
                // halo it's tracing.
                mapView.addOverlay(pl, level: .aboveLabels)
            }
        }

        /// Rebuild the orange walked-since-completion overlay for
        /// the selected trail. Same shape as `rebuildLiveHaloOverlays`
        /// but with its own bucket — the renderer styles these in
        /// systemOrange (vs the live halo's mint) so a recording-in-
        /// progress overlay and a selection overlay can coexist.
        func rebuildSelectedTrailWalkedOverlays(on mapView: MKMapView, segments: [[CLLocationCoordinate2D]]) {
            if !selectedTrailWalkedOverlays.isEmpty {
                mapView.removeOverlays(selectedTrailWalkedOverlays as [MKOverlay])
                selectedTrailWalkedOverlays.removeAll(keepingCapacity: true)
                selectedTrailWalkedIds.removeAll(keepingCapacity: true)
            }
            for seg in segments where seg.count >= 2 {
                let pl = MKPolyline(coordinates: seg, count: seg.count)
                selectedTrailWalkedOverlays.append(pl)
                selectedTrailWalkedIds.insert(ObjectIdentifier(pl))
                // .aboveLabels so the orange sits above the trail
                // polyline (which sits at .aboveRoads).
                mapView.addOverlay(pl, level: .aboveLabels)
            }
        }

        // MARK: Recording overlay

        func updateRecordingOverlay(on mapView: MKMapView, path: [GpsPoint]) {
            let coords: [CLLocationCoordinate2D] = path.compactMap { p in
                guard p.count >= 2 else { return nil }
                return CLLocationCoordinate2D(latitude: p[0], longitude: p[1])
            }
            guard coords.count >= 2 else { return }
            if let existing = recordingOverlay {
                mapView.removeOverlay(existing)
            }
            let pl = MKPolyline(coordinates: coords, count: coords.count)
            recordingOverlay = pl
            // .aboveRoads so the (slim, semi-transparent) raw-GPS
            // stroke sits BELOW the snapped purple live-halo at
            // .aboveLabels — on-trail portions read as a single
            // bold snapped stroke; off-trail portions show only
            // the slimmer raw GPS line.
            mapView.addOverlay(pl, level: .aboveRoads)
        }

        func removeRecordingOverlay(from mapView: MKMapView) {
            if let existing = recordingOverlay {
                mapView.removeOverlay(existing)
                recordingOverlay = nil
            }
        }

        // MARK: MKMapViewDelegate

        func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
            if let multi = overlay as? MKMultiPolyline {
                let r = MKMultiPolylineRenderer(multiPolyline: multi)
                if let trailId = trailIdByOverlay[ObjectIdentifier(multi)] {
                    trailRenderers[trailId] = r
                    applyTrailStyle(renderer: r, trailId: trailId)
                }
                return r
            }
            if let pl = overlay as? MKPolyline {
                let r = MKPolylineRenderer(polyline: pl)
                r.lineCap = .round
                r.lineJoin = .round
                if pl === recordingOverlay {
                    // Raw GPS path during recording. Purple at
                    // reduced alpha + slimmer than the on-trail
                    // snapped overlay below so it shows through
                    // for off-trail portions (parking, road walk,
                    // scrambles) but doesn't compete with the
                    // snapped purple stroke that sits on the
                    // trail polyline itself for counted segments.
                    r.strokeColor = UIColor.systemPurple.withAlphaComponent(0.55)
                    r.lineWidth = 2
                } else if liveHaloIds.contains(ObjectIdentifier(pl)) {
                    // Live counted-segments — TRAIL-POLYLINE-
                    // SNAPPED runs (same 10m / ≥2-consecutive-
                    // covered-nodes rule as the post-completion
                    // orange overlay). Purple at high alpha so it
                    // dominates the raw GPS stroke on portions
                    // the user is actually walking on a trail.
                    // Sits above trail polylines.
                    r.strokeColor = UIColor.systemPurple.withAlphaComponent(0.95)
                    r.lineWidth = 6
                } else if selectedTrailWalkedIds.contains(ObjectIdentifier(pl)) {
                    // Walked-since-completion on the selected trail.
                    // System orange, opaque, narrower than the
                    // trail's blue highlight so the blue stays
                    // visible underneath — "blue = still to walk,
                    // orange = already covered this cycle."
                    r.strokeColor = UIColor.systemOrange.withAlphaComponent(0.95)
                    r.lineWidth = 5
                } else {
                    // Past-hike halo. Cyan with 0.55 alpha, 7pt —
                    // matches the SwiftUI MapPolyline halo style
                    // from build 8.
                    r.strokeColor = UIColor.cyan.withAlphaComponent(0.55)
                    r.lineWidth = 7
                }
                return r
            }
            return MKOverlayRenderer(overlay: overlay)
        }

        // MARK: Tap-to-select

        @objc func handleTap(_ gesture: UITapGestureRecognizer) {
            guard let mapView = self.mapView else { return }
            let pt = gesture.location(in: mapView)
            let tapCoord = mapView.convert(pt, toCoordinateFrom: mapView)

            // Convert a 22pt screen radius into a meters tolerance at
            // the tap location's latitude — same scale the user sees,
            // independent of zoom level.
            let edgeCoord = mapView.convert(
                CGPoint(x: pt.x + 22, y: pt.y),
                toCoordinateFrom: mapView
            )
            let toleranceMeters = MapMath.haversineMeters(
                lat1: tapCoord.latitude, lon1: tapCoord.longitude,
                lat2: edgeCoord.latitude, lon2: edgeCoord.longitude
            )

            var bestTrailId: String?
            var bestMeters = Double.infinity
            for (trailId, multi) in trailOverlays where !hiddenTrailIds.contains(trailId) {
                for polyline in multi.polylines {
                    let d = distance(from: tapCoord, toPolyline: polyline)
                    if d < bestMeters {
                        bestMeters = d
                        bestTrailId = trailId
                    }
                }
            }

            if let id = bestTrailId, bestMeters <= toleranceMeters {
                parent.selectedTrailId = id
            } else {
                // Tap on empty map — clear selection so the highlight
                // dim across other trails goes back to normal.
                parent.selectedTrailId = nil
            }
        }

        // MARK: UIGestureRecognizerDelegate

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            // Tap shouldn't compete with MKMapView's pan/pinch.
            // Returning false here makes the tap a standalone gesture
            // that only fires when the user genuinely tapped without
            // initiating a pan.
            false
        }

        // MARK: Distance helpers

        /// Minimum great-circle distance (meters) from `tap` to any
        /// segment of `polyline`. Iterates the polyline's points in
        /// pairs and runs point-to-segment math in flat-earth
        /// approximation — adequate for trail-scale (single-area)
        /// distances where 1° lat ≈ 111 km. The actual math lives in
        /// `MapMath` so it's reachable from the unit-test target;
        /// this method just unpacks the MKPolyline's coords buffer.
        private func distance(from tap: CLLocationCoordinate2D,
                              toPolyline polyline: MKPolyline) -> Double {
            let n = polyline.pointCount
            guard n >= 2 else { return .infinity }
            let coordsPtr = UnsafeMutablePointer<CLLocationCoordinate2D>.allocate(capacity: n)
            defer { coordsPtr.deallocate() }
            polyline.getCoordinates(coordsPtr, range: NSRange(location: 0, length: n))
            let coords = Array(UnsafeBufferPointer(start: coordsPtr, count: n))
            return MapMath.distanceFromPoint(tap, toPolylineCoords: coords)
        }
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
    static func haversineMeters(lat1: Double, lon1: Double,
                                lat2: Double, lon2: Double) -> Double {
        let R = 6_371_000.0
        let dLat = (lat2 - lat1) * .pi / 180
        let dLon = (lon2 - lon1) * .pi / 180
        let a = sin(dLat / 2) * sin(dLat / 2) +
            cos(lat1 * .pi / 180) * cos(lat2 * .pi / 180) *
            sin(dLon / 2) * sin(dLon / 2)
        return 2 * R * atan2(sqrt(a), sqrt(1 - a))
    }
}
