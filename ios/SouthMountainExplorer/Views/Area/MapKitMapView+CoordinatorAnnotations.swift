import SwiftUI
import MapKit
import UIKit
import os

/// Planned route lines and markers, parking pins and annotation views.
/// Moved out of `MapKitMapView.Coordinator` unchanged; stored state stays in
/// the class in MapKitMapView.swift.
extension MapKitMapView.Coordinator {
    /// Start / end markers and Off-trail tags. The lines are built by
    /// `rebuildPlannedOverlays` (from `applyPlannedSteps`).
    func applyPlannedRoute(_ route: PlannedRoute?, on mapView: MKMapView) {
        mapView.removeAnnotations(plannedRouteAnnotations)
        plannedRouteAnnotations.removeAll(keepingCapacity: true)
        guard let route, !route.isEmpty else { return }
        func note(_ c: PlannedRoute.Coord, _ role: PlannedRouteAnnotation.Role, _ title: String) {
            let a = PlannedRouteAnnotation()
            a.coordinate = CLLocationCoordinate2D(latitude: c.lat, longitude: c.lon)
            a.role = role
            a.title = title
            plannedRouteAnnotations.append(a)
        }
        for tag in route.tags { note(tag.coord, .tag(tag.kind), tag.text) }
        if let s = route.start { note(s, .start, route.end == nil ? "Planned route start and finish" : "Planned route start") }
        if let e = route.end { note(e, .end, "Planned route end") }
        mapView.addAnnotations(plannedRouteAnnotations)
    }

    /// The route lines and arrows. With a step selected (Walk through), the
    /// whole route is drawn strongly faded and that leg is drawn again on
    /// top, bright, with its own arrows (no glow; Quentin asked to drop
    /// the yellow outline).
    func rebuildPlannedOverlays(_ route: PlannedRoute?, selected: Int?, on mapView: MKMapView) {
        mapView.removeOverlays(plannedRouteOverlays as [MKOverlay])
        plannedRouteOverlays.removeAll(keepingCapacity: true)
        guard let route, !route.isEmpty else { return }
        let step = selected.flatMap { $0 >= 0 && $0 < route.steps.count ? route.steps[$0] : nil }
        appendRouteLayers(route.segments, faded: step != nil)
        if let step {
            var leg = route.segments(of: step)
            if leg.isEmpty, step.coords.count >= 2 {
                leg = [.init(kind: step.kind, coords: step.coords, lengthMeters: step.meters)]
            }
            appendRouteLayers(leg, faded: false)
        }
        mapView.addOverlays(plannedRouteOverlays, level: .aboveRoads)
    }

    /// Casings first, then done (faded) lines, then new lines, then the
    /// arrows, so where an out-and-back walks new ground and then repeats
    /// it, the bright first pass stays on top.
    private func appendRouteLayers(_ segments: [PlannedRoute.Segment], faded: Bool, glow: Bool = false) {
        guard !segments.isEmpty else { return }
        let laid = segments.map { seg in
            seg.coords.map { MKMapPoint(CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon)) }
        }
        let isDone: (PlannedRoute.Segment) -> Bool = { $0.kind == .trail && !$0.isNew }
        if glow {
            for (i, seg) in segments.enumerated() where laid[i].count >= 2 {
                let g = PlannedRoutePolyline(points: laid[i], count: laid[i].count)
                g.isHighlight = true
                g.kind = seg.kind
                plannedRouteOverlays.append(g)
            }
        }
        for pass in 0..<3 {
            for (i, seg) in segments.enumerated() where laid[i].count >= 2 {
                if pass == 1, !isDone(seg) { continue }
                if pass == 2, isDone(seg) { continue }
                let pl = PlannedRoutePolyline(points: laid[i], count: laid[i].count)
                pl.kind = seg.kind
                pl.isCasing = pass == 0
                pl.isDone = isDone(seg)
                pl.isFaded = faded
                plannedRouteOverlays.append(pl)
            }
        }
        // Walking-direction chevrons over the whole route, on top of the line.
        var walk: [MKMapPoint] = []
        for p in laid.joined() {
            if let l = walk.last, l.x == p.x, l.y == p.y { continue }
            walk.append(p)
        }
        if walk.count >= 2 {
            let arrows = PlannedRouteArrowsPolyline(points: walk, count: walk.count)
            arrows.isFaded = faded
            plannedRouteOverlays.append(arrows)
        }
    }

    /// Trail overlays re-added by a filter change land above the route;
    /// put the route back on top of them.
    func bringPlannedRouteToFront(on mapView: MKMapView) {
        guard !plannedRouteOverlays.isEmpty else { return }
        mapView.removeOverlays(plannedRouteOverlays as [MKOverlay])
        mapView.addOverlays(plannedRouteOverlays, level: .aboveRoads)
    }

    func applyPlannedSteps(_ route: PlannedRoute?, selected: Int?, on mapView: MKMapView) {
        lastPlannedStep = selected
        rebuildPlannedOverlays(route, selected: selected, on: mapView)
        mapView.removeAnnotations(plannedStepAnnotations)
        plannedStepAnnotations.removeAll()
        guard let route, !route.steps.isEmpty else { return }
        let walking = selected.map { $0 >= 0 && $0 < route.steps.count } ?? false
        for (i, step) in route.steps.enumerated() {
            let selectedHere = i == selected
            if walking, !selectedHere { continue }
            if let a = plannedStepAnnotations.first(where: {
                Area.meters($0.coordinate.latitude, $0.coordinate.longitude, step.at.lat, step.at.lon) < Self.stepBadgeMergeMeters
            }) {
                a.stepNumbers.append(step.number)
                a.isSelectedStep = a.isSelectedStep || selectedHere
                a.title = (a.title ?? "") + ". Step \(step.number): \(step.instruction)"
                continue
            }
            let a = PlannedRouteAnnotation()
            a.coordinate = CLLocationCoordinate2D(latitude: step.at.lat, longitude: step.at.lon)
            a.role = .step
            a.stepNumbers = [step.number]
            a.isSelectedStep = selectedHere
            a.title = "Step \(step.number): \(step.instruction)"
            plannedStepAnnotations.append(a)
        }
        mapView.addAnnotations(plannedStepAnnotations)
    }

    /// A small numbered badge: white with a route-colored ring, or filled
    /// when it is the selected step.
    static func stepBadgeImage(_ numbers: [Int], selected: Bool) -> UIImage {
        let text = numbers.map(String.init).joined(separator: ", ")
        let font = UIFont.systemFont(ofSize: selected ? 13 : 11, weight: .bold)
        let fg = selected ? UIColor.white : plannedRouteColor
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: fg]
        let size = (text as NSString).size(withAttributes: attrs)
        let h = ceil(size.height) + (selected ? 8 : 5)
        let w = max(h, ceil(size.width) + (selected ? 12 : 9))
        let rect = CGRect(x: 0, y: 0, width: w, height: h)
        return UIGraphicsImageRenderer(size: rect.size).image { _ in
            let shape = UIBezierPath(roundedRect: rect.insetBy(dx: 1, dy: 1), cornerRadius: (h - 2) / 2)
            (selected ? plannedRouteColor : UIColor.white).setFill()
            shape.fill()
            (selected ? UIColor.white : plannedRouteColor).setStroke()
            shape.lineWidth = selected ? 2 : 1.5
            shape.stroke()
            (text as NSString).draw(at: CGPoint(x: (w - size.width) / 2, y: (h - size.height) / 2), withAttributes: attrs)
        }
    }

    static func plannedTagImage(_ text: String) -> UIImage {
        let font = UIFont.systemFont(ofSize: 11, weight: .semibold)
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: UIColor.white]
        let size = (text as NSString).size(withAttributes: attrs)
        let padX: CGFloat = 7, padY: CGFloat = 3
        let rect = CGRect(x: 0, y: 0, width: ceil(size.width) + padX * 2, height: ceil(size.height) + padY * 2)
        return UIGraphicsImageRenderer(size: rect.size).image { _ in
            plannedRouteColor.setFill()
            UIBezierPath(roundedRect: rect, cornerRadius: rect.height / 2).fill()
            UIColor.white.setStroke()
            let border = UIBezierPath(roundedRect: rect.insetBy(dx: 0.75, dy: 0.75), cornerRadius: rect.height / 2)
            border.lineWidth = 1.5
            border.stroke()
            (text as NSString).draw(at: CGPoint(x: padX, y: padY), withAttributes: attrs)
        }
    }

    /// Renders the screenshot-only synthetic user dot (see
    /// `demoUserDot`). Returns nil for every other annotation so
    /// MapKit keeps its default views — including the real
    /// MKUserLocation dot when authorization does land.
    /// Replace the area's parking pins when the area changes. Called from
    /// the `updateUIView` area-changed block alongside the trail rebuild.
    ///
    /// `lots` is the CANDIDATE set, already merged with the global pool by the
    /// caller — this method only ranks and draws. The merge happens up there
    /// because `ParkingPoolService` is @MainActor and this Coordinator is
    /// deliberately not (see the type's doc comment).
    func rebuildParkingAnnotations(on mapView: MKMapView, from area: Area,
                                   lots candidates: [ParkingLot],
                                   selectedTrailId: String?,
                                   showAll: Bool = false) {
        let existing = mapView.annotations.compactMap { $0 as? ParkingAnnotation }
        if !existing.isEmpty { mapView.removeAnnotations(existing) }
        // Show-all wins over the selection gate: the point is to answer
        // "does this area have parking at all", which the per-trail view
        // cannot. Measured on Helena-Lewis and Clark NF: 31 lots exist but
        // only 16% of trails have one within the 805 m endpoint radius, so
        // browsing trail by trail reads as "no parking here".
        let lots: [ParkingLot]
        var nearLots: Set<String> = []
        var farLots: Set<String> = []
        var farMeters: [String: Double] = [:]
        if showAll {
            lots = candidates
            // Keep selected-trail roles even while every area marker is
            // visible. Unrelated show-all pins remain area markers and do
            // not participate in selected-camera correction.
            if let selectedTrailId,
               let trail = area.trails.first(where: { $0.id == selectedTrailId }) {
                let ranked = Area.nearestParkingWithFallback(
                    lots: candidates,
                    for: trail
                )
                nearLots = Set(ranked.filter { $0.isNear }.map {
                    "\($0.lot.lat),\($0.lot.lon)"
                })
                farLots = Set(ranked.filter { !$0.isNear }.map {
                    "\($0.lot.lat),\($0.lot.lon)"
                })
                farMeters = Dictionary(
                    ranked.map { ("\($0.lot.lat),\($0.lot.lon)", $0.meters) },
                    uniquingKeysWith: min
                )
            }
            mapLog.notice("parkingPins area=\(area.id, privacy: .public) ALL shown=\(lots.count)")
        } else {
            // No selection → no parking (browsing stays uncluttered). With a
            // trail selected, show only its ≤3 nearest lots.
            guard let selectedTrailId,
                  let trail = area.trails.first(where: { $0.id == selectedTrailId })
            else {
                mapLog.notice("parkingPins area=\(area.id, privacy: .public) none (no selection)")
                return
            }
            // Fallback-aware: when nothing sits within 805 m of a trail
            // end we still answer with the closest lots in the area, marked
            // as far. 45% of trails hit this and previously drew an empty
            // map indistinguishable from "no parking exists here".
            // Ranked from the POOL-MERGED candidates, so a lot no area "owns"
            // still draws. Must be the same merge the row banner uses, or the
            // banner names a lot with no pin under it.
            let ranked = Area.nearestParkingWithFallback(lots: candidates, for: trail)
            lots = ranked.map(\.lot)
            nearLots = Set(ranked.filter { $0.isNear }.map { "\($0.lot.lat),\($0.lot.lon)" })
            farLots = Set(ranked.filter { !$0.isNear }.map { "\($0.lot.lat),\($0.lot.lon)" })
            // `uniquingKeysWith`, not `uniqueKeysWithValues`: that initialiser
            // TRAPS on a duplicate key, and two lots at identical coordinates
            // are possible in older geom (the case #41 collapsed). Keep the
            // nearer distance.
            farMeters = Dictionary(ranked.map { ("\($0.lot.lat),\($0.lot.lon)", $0.meters) },
                                   uniquingKeysWith: min)
            mapLog.notice("parkingPins area=\(area.id, privacy: .public) trail=\(selectedTrailId, privacy: .public) shown=\(lots.count) far=\(farLots.count)")
        }
        for lot in lots {
            let ann = ParkingAnnotation()
            ann.coordinate = CLLocationCoordinate2D(latitude: lot.lat, longitude: lot.lon)
            // A federal BLM/USFS point is a trailhead, not a parking lot.
            ann.isTrailhead = Self.isTrailheadSource(lot.source)
            let key = "\(lot.lat),\(lot.lon)"
            ann.isNearSelectedTrail = nearLots.contains(key)
            ann.isFarFallback = farLots.contains(key)
            ann.title = lot.name ?? (ann.isTrailhead ? "Trailhead" : "Parking")
            var sub = Self.parkingSubtitle(for: lot, isTrailhead: ann.isTrailhead)
            // State the distance when this is NOT at the trail. Saying it is
            // what makes offering a far lot honest rather than a trailhead
            // claim — the same reason the containment gate exists.
            if farLots.contains(key), let d = farMeters[key] {
                let mi = d / 1609.344
                let far = mi < 10 ? String(format: "%.1f mi from trail", mi)
                                  : String(format: "%.0f mi from trail", mi)
                sub = sub.map { "\($0) · \(far)" } ?? far
            }
            ann.subtitle = sub
            mapView.addAnnotation(ann)
        }
    }

    /// Federal agency trailhead POINTS (BLM/USFS layers) — rendered as a
    /// trailhead marker. NPS ("nps"), OSM, and untagged lots are real
    /// parking and keep the "P".
    static func isTrailheadSource(_ source: String?) -> Bool {
        source == "blm" || source == "usfs"
    }

    /// Human agency label for attribution (federal data is public domain;
    /// attribution is a courtesy we still surface in the pin's callout).
    static func attribution(for source: String?) -> String? {
        switch source {
        case "blm": return "BLM"
        case "nps": return "NPS"
        case "usfs": return "USFS"
        default: return nil          // OSM / untagged — no extra credit line
        }
    }

    /// Callout subtitle: the access type, plus a "Data: <agency>" credit
    /// for federal sources and a fee note for parking lots that carry one.
    static func parkingSubtitle(for lot: ParkingLot, isTrailhead: Bool) -> String? {
        var parts: [String] = []
        if isTrailhead {
            parts.append("Trailhead")
        } else if let fee = lot.fee {
            parts.append(fee ? "Paid parking" : "Free parking")
        }
        if let agency = attribution(for: lot.source) {
            parts.append("Data: \(agency)")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {
        // Our own user dot, so the facing cone is always drawn. Apple's
        // shows it only in .followWithHeading.
        if annotation is MKUserLocation {
            let id = "user-heading-dot"
            let view = mapView.dequeueReusableAnnotationView(withIdentifier: id)
                ?? MKAnnotationView(annotation: annotation, reuseIdentifier: id)
            view.annotation = annotation
            view.image = Self.userHeadingDotImage
            view.zPriority = .max
            view.canShowCallout = false
            userDotView = view
            applyHeadingRotation(on: mapView)
            return view
        }
        if annotation is DemoUserDotAnnotation {
            let id = "demo-user-dot"
            let view = mapView.dequeueReusableAnnotationView(withIdentifier: id)
                ?? MKAnnotationView(annotation: annotation, reuseIdentifier: id)
            view.annotation = annotation
            view.image = Self.demoUserDotImage
            view.zPriority = .max
            return view
        }
        if let planned = annotation as? PlannedRouteAnnotation {
            switch planned.role {
            case .step:
                let id = "planned-route-step"
                let view = mapView.dequeueReusableAnnotationView(withIdentifier: id)
                    ?? MKAnnotationView(annotation: annotation, reuseIdentifier: id)
                view.annotation = annotation
                view.image = Self.stepBadgeImage(planned.stepNumbers, selected: planned.isSelectedStep)
                view.canShowCallout = false
                // Badges give way to each other when zoomed out (MapKit
                // hides the colliding lower-priority ones); earlier steps
                // win. The selected step always shows.
                view.collisionMode = .circle
                let first = Float(planned.stepNumbers.min() ?? 1)
                view.displayPriority = planned.isSelectedStep
                    ? .required : MKFeatureDisplayPriority(rawValue: max(300, 740 - first))
                view.zPriority = planned.isSelectedStep ? .max : .defaultSelected
                view.centerOffset = .zero
                view.isAccessibilityElement = true
                view.accessibilityLabel = planned.title
                view.accessibilityIdentifier = "plan-route-step-badge"
                return view
            case .tag:
                let id = "planned-route-tag"
                let view = mapView.dequeueReusableAnnotationView(withIdentifier: id)
                    ?? MKAnnotationView(annotation: annotation, reuseIdentifier: id)
                view.annotation = annotation
                view.image = Self.plannedTagImage(planned.title ?? "")
                view.canShowCallout = false
                view.displayPriority = .required
                view.collisionMode = .none
                // Sit beside the line, clear of the start pin.
                view.centerOffset = CGPoint(x: (view.image?.size.width ?? 0) / 2 + 10, y: 0)
                view.isAccessibilityElement = true
                view.accessibilityLabel = planned.title
                view.accessibilityIdentifier = "plan-route-tag"
                return view
            case .start, .end:
                let isStart: Bool = { if case .start = planned.role { return true } else { return false } }()
                let id = isStart ? "planned-route-start" : "planned-route-end"
                let view = (mapView.dequeueReusableAnnotationView(withIdentifier: id) as? MKMarkerAnnotationView)
                    ?? MKMarkerAnnotationView(annotation: annotation, reuseIdentifier: id)
                view.annotation = annotation
                view.markerTintColor = Self.plannedRouteColor
                view.glyphImage = UIImage(systemName: isStart ? "figure.hiking" : "flag.checkered")
                view.canShowCallout = false
                view.displayPriority = .required
                view.titleVisibility = .hidden
                view.isAccessibilityElement = true
                view.accessibilityLabel = planned.title
                view.accessibilityIdentifier = isStart ? "plan-route-start" : "plan-route-end"
                return view
            }
        }
        if let parking = annotation as? ParkingAnnotation {
            // Reuse-id per style so a recycled trailhead view never keeps a
            // parking glyph (or vice-versa) after MapKit dequeues it.
            let id = parking.isTrailhead ? "trailhead" : "parking"
            let view = (mapView.dequeueReusableAnnotationView(withIdentifier: id)
                        as? MKMarkerAnnotationView)
                ?? MKMarkerAnnotationView(annotation: annotation, reuseIdentifier: id)
            view.annotation = annotation
            if parking.isTrailhead {
                // A trail START, not a lot: green + a hiker glyph so it
                // reads as "the trail begins here," never "park on this dot."
                view.markerTintColor = .systemGreen
                view.glyphImage = UIImage(systemName: "figure.walk")
            } else {
                view.markerTintColor = .systemBlue   // Maps parking convention
                view.glyphImage = nil
                view.glyphText = "P"
            }
            view.canShowCallout = true
            view.displayPriority = .required        // a few per area — always show
            view.isAccessibilityElement = true
            view.accessibilityIdentifier = parking.isFarFallback
                ? "map-far-access-marker"
                : parking.isNearSelectedTrail
                    ? "map-near-access-marker"
                    : "map-area-access-marker"
            let semanticParts = [parking.title, parking.subtitle]
                .compactMap { $0 }
                .filter { !$0.isEmpty }
            view.accessibilityLabel = semanticParts.joined(separator: ", ")
            view.accessibilityHint = "Shows access details"
            return view
        }
        return nil
    }
}
