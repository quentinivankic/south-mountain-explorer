import SwiftUI
import MapKit
import UIKit
import os

/// Camera, region-change and gesture callbacks, plus the selected-marker audit.
/// Moved out of `MapKitMapView.Coordinator` unchanged; stored state stays in
/// the class in MapKitMapView.swift.
extension MapKitMapView.Coordinator {
    /// Point the dot at the real bearing.
    ///
    /// Rotate by heading MINUS the map's own camera heading: in
    /// follow-with-heading the map itself turns so that your bearing is up,
    /// and rotating the dot by the raw heading there would double-count the
    /// rotation and leave it pointing sideways.
    func applyHeadingRotation(on mapView: MKMapView) {
        guard let view = userDotView else { return }
        guard let heading = pendingHeading else {
            view.transform = .identity
            return
        }
        let relative = heading - mapView.camera.heading
        guard !relative.isNaN else { return }
        if !lastAppliedHeading.isNaN,
           abs(relative - lastAppliedHeading) < 0.5 { return }
        lastAppliedHeading = relative
        view.transform = CGAffineTransform(rotationAngle: CGFloat(relative * .pi / 180))
    }

    // MARK: MKMapViewDelegate

    /// Fires at the END of every region change — both ours and the
    /// user's. We decrement the pending-programmatic counter for
    /// our own calls; anything left is user-driven (pinch / pan /
    /// double-tap), in which case we notify the parent so it can
    /// re-snap to the user dot (preserving the user's pinched zoom
    /// — see TrailMapView.updateTrackedPosition).
    func mapView(_ mapView: MKMapView, regionDidChangeAnimated animated: Bool) {
        // The cone is drawn relative to the map's own rotation, so it has to
        // be re-applied whenever the map turns — otherwise rotating the map
        // by hand leaves the dot pointing at the old bearing.
        applyHeadingRotation(on: mapView)
        refreshLineDisplayZoomScale(on: mapView, redraw: true)
        userCameraGestureLatch.regionDidChange()
        if pendingProgrammaticRegionChanges > 0 {
            pendingProgrammaticRegionChanges -= 1
            reportSelectedNearMarkerOcclusionIfNeeded(on: mapView)
            return
        }
        parent.onUserGestureRegionChange?()
    }

    func mapViewDidChangeVisibleRegion(_ mapView: MKMapView) {
        refreshLineDisplayZoomScale(on: mapView, redraw: false)
    }

    func mapView(_ mapView: MKMapView, didAdd views: [MKAnnotationView]) {
        guard let generation = pendingSelectedMarkerAuditGeneration,
              generation == parent.selectedMarkerAuditGeneration,
              generation != lastSelectedMarkerAuditGeneration,
              views.contains(where: {
                  ($0.annotation as? ParkingAnnotation)?.isNearSelectedTrail == true
              }) else { return }

        mapView.layoutIfNeeded()
        let nearAnnotations = mapView.annotations.compactMap { annotation -> ParkingAnnotation? in
            guard let parking = annotation as? ParkingAnnotation,
                  parking.isNearSelectedTrail else { return nil }
            return parking
        }
        let frames = renderedNearMarkerFrames(
            on: mapView,
            annotations: nearAnnotations
        )
        guard MapKitMapView.markerFramesAreComplete(
            frames,
            expectedCount: nearAnnotations.count
        ) else { return }

        pendingSelectedMarkerAuditGeneration = nil
        reportSelectedNearMarkerOcclusionIfNeeded(
            on: mapView,
            allowRescheduling: false
        )
    }

    private func reportSelectedNearMarkerOcclusionIfNeeded(
        on mapView: MKMapView,
        allowRescheduling: Bool = true
    ) {
        let generation = parent.selectedMarkerAuditGeneration
        guard generation > 0,
              generation != lastSelectedMarkerAuditGeneration else { return }

        // A programmatic region has finished and the native sheet was
        // already allowed to settle before this generation was requested.
        // Force the annotation container's pending layout, then measure
        // every expected near marker in full-screen map coordinates.
        mapView.layoutIfNeeded()
        let nearAnnotations = mapView.annotations.compactMap { annotation -> ParkingAnnotation? in
            guard let parking = annotation as? ParkingAnnotation,
                  parking.isNearSelectedTrail else { return nil }
            return parking
        }
        let frames = renderedNearMarkerFrames(
            on: mapView,
            annotations: nearAnnotations
        )
        guard MapKitMapView.markerFramesAreComplete(
            frames,
            expectedCount: nearAnnotations.count
        ) else {
            if allowRescheduling,
               pendingSelectedMarkerAuditGeneration != generation {
                pendingSelectedMarkerAuditGeneration = generation
            }
            return
        }

        pendingSelectedMarkerAuditGeneration = nil
        guard !frames.isEmpty else { return }
        let correction = MapKitMapView.markerOcclusionCorrection(
            markerFrames: frames,
            mapBounds: mapView.bounds,
            visibleInsets: parent.selectedMarkerVisibleInsets
        )
        if MapKitMapView.consumeMarkerAuditGenerationIfNeeded(
            generation: generation,
            correction: correction,
            lastConsumedGeneration: &lastSelectedMarkerAuditGeneration
        ) {
            parent.onSelectedNearMarkerOcclusion?(correction)
            return
        }

        // Annotation frames can settle one run-loop after the camera's
        // completion callback. A complete zero result gets exactly one
        // generation-current recheck; that final pass cannot reschedule.
        guard allowRescheduling,
              correction == .zero,
              lastScheduledSelectedMarkerAuditGeneration != generation else { return }
        lastScheduledSelectedMarkerAuditGeneration = generation
        DispatchQueue.main.async { [weak self, weak mapView] in
            guard let self,
                  self.lastScheduledSelectedMarkerAuditGeneration == generation,
                  let mapView,
                  self.parent.selectedMarkerAuditGeneration == generation,
                  self.lastSelectedMarkerAuditGeneration != generation else { return }
            self.reportSelectedNearMarkerOcclusionIfNeeded(
                on: mapView,
                allowRescheduling: false
            )
        }
    }

    private func renderedNearMarkerFrames(
        on mapView: MKMapView,
        annotations: [ParkingAnnotation]
    ) -> [CGRect] {
        annotations.compactMap { parking in
            guard let view = mapView.view(for: parking),
                  !view.isHidden,
                  view.alpha > 0 else { return nil }
            view.layoutIfNeeded()
            return view.convert(view.bounds, to: mapView)
        }
    }

    // MARK: Tap-to-select

    @objc func handleTap(_ gesture: UITapGestureRecognizer) {
        guard let mapView = self.mapView else { return }
        let pt = gesture.location(in: mapView)
        let tapCoord = mapView.convert(pt, toCoordinateFrom: mapView)

        if let pick = parent.onPickCoordinate {
            pick(tapCoord)
            return
        }

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

    /// MapKit's own recognizers: on the map view and its direct
    /// subviews (where MKMapView keeps its pan / pinch / rotate / zoom
    /// taps), minus our selection tap.
    func mapKitGestureRecognizers(of mapView: MKMapView) -> [UIGestureRecognizer] {
        let views: [UIView] = [mapView] + mapView.subviews
        return views
            .flatMap { $0.gestureRecognizers ?? [] }
            .filter { $0 !== selectionTap }
    }

    /// Fires at the START of every region change. If one of MapKit's own
    /// recognizers is driving it, the user just took the camera: report
    /// it once per gesture.
    func mapView(_ mapView: MKMapView, regionWillChangeAnimated animated: Bool) {
        let states = mapKitGestureRecognizers(of: mapView).map(\.state)
        if userCameraGestureLatch.regionWillChange(recognizerStates: states) {
            parent.onUserCameraGestureBegan?()
        }
    }

    // MARK: UIGestureRecognizerDelegate

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
        // Tap shouldn't compete with MKMapView's pan/pinch.
        // Returning false here makes the tap a standalone gesture
        // that only fires when the user genuinely tapped without
        // initiating a pan.
        return false
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
