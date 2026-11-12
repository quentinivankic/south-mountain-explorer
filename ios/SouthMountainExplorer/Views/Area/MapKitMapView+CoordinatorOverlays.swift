import SwiftUI
import MapKit
import UIKit
import os

/// Trail, halo, live, walked and recording overlays, plus the line renderer factory.
/// Moved out of `MapKitMapView.Coordinator` unchanged; stored state stays in
/// the class in MapKitMapView.swift.
extension MapKitMapView.Coordinator {
    static func usesScreenWidthLines(_ mapView: MKMapView) -> Bool {
        mapView.preferredConfiguration.elevationStyle == .realistic
    }

    /// Flat and realistic maps need different renderer classes (see
    /// `ScreenWidthPolylineRenderer`). When the style crosses between
    /// them, re-add every overlay in its current level and order so
    /// MapKit asks for fresh renderers.
    func reloadLineRenderersIfElevationChanged(on mapView: MKMapView) {
        let realistic = Self.usesScreenWidthLines(mapView)
        defer { lineRenderersRealistic = realistic }
        guard let built = lineRenderersRealistic, built != realistic else { return }
        let roads = mapView.overlays(in: .aboveRoads)
        let labels = mapView.overlays(in: .aboveLabels)
        mapView.removeOverlays(roads + labels)
        trailRenderers.removeAll(keepingCapacity: true)
        mapView.addOverlays(roads, level: .aboveRoads)
        mapView.addOverlays(labels, level: .aboveLabels)
    }

    /// Track the map's on-screen scale for the line renderers. Every frame
    /// of a gesture only stores the value (tiles MapKit draws mid-gesture
    /// use it); `redraw` re-rasterizes the existing lines once, when the
    /// scale actually changed.
    func refreshLineDisplayZoomScale(on mapView: MKMapView, redraw: Bool) {
        var scale: MKZoomScale?
        if mapView.preferredConfiguration.elevationStyle == .realistic {
            // Not `convert(_:toCoordinateFrom:)`: on a terrain map it
            // returns a scale that lags or snaps away from the screen's.
            scale = LineDisplayZoomScale.zoomScale(
                viewSize: mapView.bounds.size,
                visibleMapWidth: mapView.visibleMapRect.size.width,
                headingDegrees: mapView.camera.heading
            )
        }
        lineDisplayZoomScale.value = scale
        guard redraw, LineDisplayZoomScale.needsRedraw(from: lineRedrawZoomScale, to: scale) else { return }
        lineRedrawZoomScale = scale
        for overlay in mapView.overlays {
            mapView.renderer(for: overlay)?.setNeedsDisplay()
        }
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

        let isRecordingThis = trailId == recordingTrailId
        let isSelected = trailId == selectedTrailId
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
            // Completed trails are cyan — the color marks "done"; the line
            // width stays the same as every other trail (below).
            baseColor = Self.completedTrail
        } else {
            baseColor = Self.difficultyColor(difficulty)
        }
        // NOTHING is dimmed, ever. Selecting a trail used to drop every
        // other trail to 0.35 alpha so the blue selection would pop, and
        // that was the wrong trade twice over: it made the surrounding
        // network hard to read in bright sun mid-hike (which is why the
        // recording case was already exempted), and it hides the very
        // context you selected a trail in order to understand — what
        // connects to it, what crosses it, what you could add on.
        //
        // The selection does not need the rest of the map turned down to be
        // findable. It is iOS blue against difficulty colours and 9pt
        // against 3pt, which is three times the width of anything near it.
        renderer.strokeColor = baseColor
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

    // MARK: Live trail-snapped overlays

    /// Render the user's GPS path snapped to the trail polyline as
    /// an overlay layer. The "halo" name dates to PR #125 when this
    /// was rendered as a wide GPS-space halo around the raw path;
    /// PR #126 switched to trail-polyline-snapping, so it's no
    /// longer a "halo" in any visual sense — see the rename for
    /// the rest of the symbols.
    func rebuildLiveTrailSnappedOverlays(on mapView: MKMapView, segments: [[CLLocationCoordinate2D]]) {
        if !liveTrailSnappedOverlays.isEmpty {
            mapView.removeOverlays(liveTrailSnappedOverlays as [MKOverlay])
            liveTrailSnappedOverlays.removeAll(keepingCapacity: true)
            liveTrailSnappedIds.removeAll(keepingCapacity: true)
        }
        for seg in segments where seg.count >= 2 {
            let pl = MKPolyline(coordinates: seg, count: seg.count)
            liveTrailSnappedOverlays.append(pl)
            liveTrailSnappedIds.insert(ObjectIdentifier(pl))
            // .aboveLabels so the live halo sits on top of trail
            // polylines (which live in .aboveRoads). The recording
            // polyline is also at .aboveLabels and re-added every
            // tick after this rebuild, so it stays on top of the
            // halo it's tracing.
            mapView.addOverlay(pl, level: .aboveLabels)
        }
    }

    /// Rebuild the orange walked-since-completion overlay for
    /// the selected trail. Same shape as `rebuildLiveTrailSnappedOverlays`
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

    // MARK: Recording overlays

    func updateRecordingOverlays(on mapView: MKMapView, path: [GpsPoint]) {
        Self.reconcileRecordingOverlays(
            on: mapView,
            path: path,
            overlays: &recordingOverlays,
            overlayIds: &recordingOverlayIds
        )
    }

    /// Incrementally reconcile drawable continuous runs. Existing overlays
    /// before the active run are finalized and retained by identity; only
    /// the prior active overlay is removed and replaced.
    static func reconcileRecordingOverlays(
        on mapView: MKMapView,
        path: [GpsPoint],
        overlays: inout [MKPolyline],
        overlayIds: inout Set<ObjectIdentifier>
    ) {
        let coordinateRuns = GpsIngest.continuousRuns(path).map { run in
            run.compactMap { point -> CLLocationCoordinate2D? in
                guard point.count >= 2,
                      point[0].isFinite, point[1].isFinite else { return nil }
                return CLLocationCoordinate2D(latitude: point[0], longitude: point[1])
            }
        }
        let finalizedRuns = coordinateRuns.dropLast().filter { $0.count >= 2 }

        // Anything after the finalized prefix was the old growing run (or
        // belongs to a path that was replaced/shrunk). No unrelated overlay
        // is ever considered for removal.
        if overlays.count > finalizedRuns.count {
            let removed = overlays[finalizedRuns.count...]
            mapView.removeOverlays(Array(removed) as [MKOverlay])
            for overlay in removed {
                overlayIds.remove(ObjectIdentifier(overlay))
            }
            overlays.removeLast(overlays.count - finalizedRuns.count)
        }

        // Initial rendering can arrive with several completed runs. Build
        // only the missing finalized suffix; an incrementally finalized run
        // already occupies the matching slot and keeps its identity.
        if overlays.count < finalizedRuns.count {
            for coordinates in finalizedRuns.dropFirst(overlays.count) {
                addRecordingOverlay(
                    coordinates: coordinates,
                    to: mapView,
                    overlays: &overlays,
                    overlayIds: &overlayIds
                )
            }
        }

        if let activeRun = coordinateRuns.last, activeRun.count >= 2 {
            addRecordingOverlay(
                coordinates: activeRun,
                to: mapView,
                overlays: &overlays,
                overlayIds: &overlayIds
            )
        }
    }

    static func removeRecordingOverlays(
        from mapView: MKMapView,
        overlays: inout [MKPolyline],
        overlayIds: inout Set<ObjectIdentifier>
    ) {
        mapView.removeOverlays(overlays as [MKOverlay])
        overlays.removeAll(keepingCapacity: true)
        overlayIds.removeAll(keepingCapacity: true)
    }

    func removeRecordingOverlays(from mapView: MKMapView) {
        Self.removeRecordingOverlays(
            from: mapView,
            overlays: &recordingOverlays,
            overlayIds: &recordingOverlayIds
        )
    }

    private static func addRecordingOverlay(
        coordinates: [CLLocationCoordinate2D],
        to mapView: MKMapView,
        overlays: inout [MKPolyline],
        overlayIds: inout Set<ObjectIdentifier>
    ) {
        let overlay = RecordingPolyline(coordinates: coordinates, count: coordinates.count)
        overlays.append(overlay)
        overlayIds.insert(ObjectIdentifier(overlay))
        // .aboveRoads keeps raw GPS below the snapped live coverage layer.
        mapView.addOverlay(overlay, level: .aboveRoads)
    }

    func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
        if let multi = overlay as? MKMultiPolyline {
            let r = Self.usesScreenWidthLines(mapView)
                ? ScreenWidthMultiPolylineRenderer(multiPolyline: multi, displayZoomScale: lineDisplayZoomScale)
                : MKMultiPolylineRenderer(multiPolyline: multi)
            if let trailId = trailIdByOverlay[ObjectIdentifier(multi)] {
                trailRenderers[trailId] = r
                applyTrailStyle(renderer: r, trailId: trailId)
            }
            return r
        }
        if let arrows = overlay as? PlannedRouteArrowsPolyline {
            return PlannedRouteArrowsRenderer(polyline: arrows)
        }
        if let route = overlay as? PlannedRoutePolyline {
            let r = Self.usesScreenWidthLines(mapView)
                ? ScreenWidthPolylineRenderer(polyline: route, displayZoomScale: lineDisplayZoomScale)
                : MKPolylineRenderer(polyline: route)
            let dashed = route.kind != .trail
            r.lineJoin = .round
            r.lineCap = dashed ? .butt : .round
            if dashed { r.lineDashPattern = [9, 7] }
            if route.isHighlight {
                // Selected step: a wide warm glow around the leg.
                r.lineDashPattern = nil
                r.lineCap = .round
                r.strokeColor = UIColor.systemYellow.withAlphaComponent(0.9)
                r.lineWidth = 16
            } else if route.isCasing {
                r.strokeColor = UIColor.white.withAlphaComponent(0.95 * (route.isFaded ? Self.plannedFade : 1))
                r.lineWidth = 8
            } else {
                // Done ground: same hue and width, faded over the white casing.
                let color = route.isDone ? Self.plannedRouteDoneColor : Self.plannedRouteColor
                r.strokeColor = route.isFaded ? color.withAlphaComponent(color.cgColor.alpha * Self.plannedFade) : color
                r.lineWidth = 4
            }
            return r
        }
        if let pl = overlay as? MKPolyline {
            let r = Self.usesScreenWidthLines(mapView)
                ? ScreenWidthPolylineRenderer(polyline: pl, displayZoomScale: lineDisplayZoomScale)
                : MKPolylineRenderer(polyline: pl)
            r.lineCap = .round
            r.lineJoin = .round
            if pl is RecordingPolyline {
                // Raw GPS path during recording. Purple at
                // reduced alpha + slimmer than the on-trail
                // snapped overlay below so it shows through
                // for off-trail portions (parking, road walk,
                // scrambles) but doesn't compete with the
                // snapped purple stroke that sits on the
                // trail polyline itself for counted segments.
                r.strokeColor = UIColor.systemPurple.withAlphaComponent(0.55)
                r.lineWidth = 2
            } else if liveTrailSnappedIds.contains(ObjectIdentifier(pl)) {
                // Live coverage: the TRAIL-POLYLINE-SNAPPED runs you've
                // actually walked this recording (10m snap, ≥2 consecutive
                // covered nodes). Drawn in the completed cyan at the normal
                // 3pt trail width (applyTrailStyle's un-selected base) so the
                // covered stretch reads as the trail line itself turning
                // cyan rather than a separate follower stripe. On the thick
                // recording trail the purple shows at the edges under it —
                // accepted tradeoff (chosen over widening or thinning the
                // active trail). Was a wider systemPink stripe.
                r.strokeColor = Self.completedTrail
                r.lineWidth = 3
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
}
