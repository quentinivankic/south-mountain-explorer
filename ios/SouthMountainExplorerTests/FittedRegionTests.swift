import Testing
import UIKit
@testable import SouthMountainExplorer

/// Tests for `TrailMapView.fittedRegion` and `regionCoveringArea`.
/// These compute the camera framing for the area map: how big a
/// region to show, where to center it, and how to shift the center
/// when bottom UI chrome (recording panel, trail list sheet) covers
/// part of the map.
///
/// The shift math depends on the screen height, which on a device /
/// simulator comes from `UIScreen.main.bounds.height`. Tests use
/// the simulator's screen height implicitly — we assert relative
/// properties (direction of shift, sign of inflation) rather than
/// exact magnitudes so the tests stay portable across simulator
/// device sizes.
struct FittedRegionTests {

    // MARK: - fittedRegion: bottomInset == 0 produces exact passthrough

    @Test func fittedRegion_zeroBottomInset_passesThrough() {
        let target = TrailMapView.fittedRegion(
            centerLat: 33.3,
            centerLon: -112.0,
            latDelta: 0.02,
            lonDelta: 0.025,
            bottomInset: 0,
            screenHeight: 800,
            screenWidth: 400
        )
        switch target {
        case .region(let lat, let lon, let latDelta, let lonDelta):
            #expect(lat == 33.3)
            #expect(lon == -112.0)
            #expect(latDelta == 0.02)
            #expect(lonDelta == 0.025)
        case .camera:
            Issue.record("Expected .region, got .camera")
        case .followCenter:
            Issue.record("Expected .region, got .followCenter")
        }
    }

    // MARK: - fittedRegion: bottomInset > 0 shifts center south + inflates lat

    @Test func fittedRegion_positiveBottomInset_shiftsCenterSouth() {
        let zero = TrailMapView.fittedRegion(
            centerLat: 33.3, centerLon: -112.0,
            latDelta: 0.02, lonDelta: 0.025,
            bottomInset: 0,
            screenHeight: 800,
            screenWidth: 400
        )
        let shifted = TrailMapView.fittedRegion(
            centerLat: 33.3, centerLon: -112.0,
            latDelta: 0.02, lonDelta: 0.025,
            bottomInset: 200,
            screenHeight: 800,
            screenWidth: 400
        )
        guard case .region(let zLat, let zLon, let zDLat, let zDLon) = zero,
              case .region(let sLat, let sLon, let sDLat, let sDLon) = shifted
        else {
            Issue.record("Expected .region cases on both calls")
            return
        }
        // Center longitude unchanged — panel doesn't constrain horizontally.
        #expect(sLon == zLon)
        // Center latitude shifted south (smaller in the northern hemisphere).
        #expect(sLat < zLat, "Center should shift south when bottomInset > 0")
        // Latitudinal span inflated so the requested content still
        // fits in the un-occluded portion of the screen.
        #expect(sDLat > zDLat, "latDelta should inflate when bottomInset > 0")
        // Longitudinal span unchanged for the same reason.
        #expect(sDLon == zDLon)
    }

    // MARK: - fittedRegion: clamping

    @Test func fittedRegion_minSpan() {
        // A vanishingly small target region (e.g. a single GPS
        // point) is clamped up to 0.005° in both axes so MapKit's
        // setRegion has something to work with.
        let target = TrailMapView.fittedRegion(
            centerLat: 33.3, centerLon: -112.0,
            latDelta: 0.0001, lonDelta: 0.0001,
            bottomInset: 0,
            screenHeight: 800,
            screenWidth: 400
        )
        guard case .region(_, _, let latDelta, let lonDelta) = target else {
            Issue.record("Expected .region")
            return
        }
        #expect(latDelta >= 0.005)
        #expect(lonDelta >= 0.005)
    }

    // MARK: - regionCoveringArea: bbox from trails

    @Test func regionCoveringArea_centersOnTrailBboxMidpoint() {
        // Two trails on opposite corners of a bounding box. The
        // returned region should center on the bbox midpoint.
        let trail1 = Trail(
            id: "t1", name: "T1", distanceMi: 1.0, difficulty: .easy,
            segments: [[[33.3, -112.0]]]
        )
        let trail2 = Trail(
            id: "t2", name: "T2", distanceMi: 1.0, difficulty: .easy,
            segments: [[[33.4, -111.9]]]
        )
        let area = Area(
            id: "test", name: "Test", subtitle: "AZ",
            centerLat: 0, centerLon: 0,
            zoom: 12, bbox: nil,
            trails: [trail1, trail2],
            trailCount: 2, totalMi: 2.0, cachedAt: nil
        )
        let target = TrailMapView.regionCoveringArea(area: area, bottomInset: 0, screenHeight: 800, screenWidth: 400)
        guard case .region(let lat, let lon, _, _) = target else {
            Issue.record("Expected .region")
            return
        }
        #expect(abs(lat - 33.35) < 1e-9, "Latitude should center on bbox midpoint")
        #expect(abs(lon - (-111.95)) < 1e-9, "Longitude should center on bbox midpoint")
    }

    @Test func regionCoveringArea_spanCoversBothTrails_with130PercentPadding() {
        let trail1 = Trail(
            id: "t1", name: "T1", distanceMi: 1.0, difficulty: .easy,
            segments: [[[33.3, -112.0]]]
        )
        let trail2 = Trail(
            id: "t2", name: "T2", distanceMi: 1.0, difficulty: .easy,
            segments: [[[33.4, -111.9]]]
        )
        let area = Area(
            id: "test", name: "Test", subtitle: "AZ",
            centerLat: 0, centerLon: 0,
            zoom: 12, bbox: nil,
            trails: [trail1, trail2],
            trailCount: 2, totalMi: 2.0, cachedAt: nil
        )
        let target = TrailMapView.regionCoveringArea(area: area, bottomInset: 0, screenHeight: 800, screenWidth: 400)
        guard case .region(_, _, let latDelta, let lonDelta) = target else {
            Issue.record("Expected .region")
            return
        }
        // Raw bbox span is 0.1° in both axes. With 1.3× padding the
        // span should be ~0.13° (and at least 0.01° from the floor).
        #expect(abs(latDelta - 0.13) < 1e-9)
        #expect(abs(lonDelta - 0.13) < 1e-9)
    }

    @Test func regionCoveringArea_fallsBackToBbox_whenNoTrails() {
        // No trails but the Area has a bbox — use it.
        let area = Area(
            id: "test", name: "Test", subtitle: "AZ",
            centerLat: 33.35, centerLon: -111.95,
            zoom: 12,
            bbox: [-112.0, 33.3, -111.9, 33.4],
            trails: [],
            trailCount: 0, totalMi: 0, cachedAt: nil
        )
        let target = TrailMapView.regionCoveringArea(area: area, bottomInset: 0, screenHeight: 800, screenWidth: 400)
        guard case .region(let lat, let lon, _, _) = target else {
            Issue.record("Expected .region")
            return
        }
        #expect(abs(lat - 33.35) < 1e-9)
        #expect(abs(lon - (-111.95)) < 1e-9)
    }

    @Test func regionCoveringArea_fallsBackToCamera_whenNoTrailsNoBbox() {
        let area = Area(
            id: "test", name: "Test", subtitle: "AZ",
            centerLat: 33.3, centerLon: -112.0,
            zoom: 12, bbox: nil,
            trails: [],
            trailCount: 0, totalMi: 0, cachedAt: nil
        )
        let target = TrailMapView.regionCoveringArea(area: area, bottomInset: 0, screenHeight: 800, screenWidth: 400)
        guard case .camera(let lat, let lon, let distance, let heading) = target else {
            Issue.record("Expected .camera fallback")
            return
        }
        #expect(lat == 33.3)
        #expect(lon == -112.0)
        #expect(distance == 5000)
        #expect(heading == 0)
    }

    // MARK: - fittedRegion: wide area centers in the visible area

    @Test func fittedRegion_wideArea_shiftsMoreThanSquare() {
        // A wide, thin area (South Mountain is ~20 mi × 3 mi) is
        // width-constrained: MapKit displays far MORE latitude than the
        // area's own span, so the south-shift must scale with that DISPLAYED
        // span — otherwise the area lands near the full-screen center (low,
        // behind the sheet) with the surrounding city filling the top. So a
        // wide area must shift south MORE than a square one at the same inset.
        // (The old code shifted by the area's own latDelta, giving both the
        // same shift — this test fails against that bug.)
        let square = TrailMapView.fittedRegion(
            centerLat: 33.3, centerLon: -112.0,
            latDelta: 0.05, lonDelta: 0.05,
            bottomInset: 400, screenHeight: 900, screenWidth: 400
        )
        let wide = TrailMapView.fittedRegion(
            centerLat: 33.3, centerLon: -112.0,
            latDelta: 0.05, lonDelta: 0.30,   // 6× wider, same height
            bottomInset: 400, screenHeight: 900, screenWidth: 400
        )
        guard case .region(let sqLat, _, _, _) = square,
              case .region(let wideLat, _, _, _) = wide else {
            Issue.record("Expected .region cases"); return
        }
        #expect((33.3 - wideLat) > (33.3 - sqLat),
                "A wide area must shift south more than a square one to sit centered in the visible area")
    }

    @Test func fittedRegion_wideArea_shiftFollowsMercatorLatitude() {
        // A width-constrained region's displayed latitude span is a Mercator
        // quantity: the map draws a degree of latitude 1/cos(lat) times as
        // tall as a degree of longitude, so the height holds
        // `lonDelta * (height/width) * cos(lat)` degrees. The south-shift is
        // half the occluded fraction of THAT, so the same wide shape at 60°N
        // must shift half as far (cos 60° = 0.5) as at the equator. The old
        // math ignored the cosine, so both shifted the same and the northern
        // park landed far too high — this test fails against that bug.
        let equator = TrailMapView.fittedRegion(
            centerLat: 0.0, centerLon: -60.0,
            latDelta: 0.05, lonDelta: 0.30,
            bottomInset: 400, screenHeight: 900, screenWidth: 400
        )
        let north = TrailMapView.fittedRegion(
            centerLat: 60.0, centerLon: -150.0,
            latDelta: 0.05, lonDelta: 0.30,
            bottomInset: 400, screenHeight: 900, screenWidth: 400
        )
        guard case .region(let eqLat, _, _, _) = equator,
              case .region(let noLat, _, _, _) = north else {
            Issue.record("Expected .region cases"); return
        }
        let equatorShift = 0.0 - eqLat
        let northShift = 60.0 - noLat
        // Both width-constrained: 0.30 × (900/400) × cos(lat) = 0.675 / 0.3375°
        // displayed, times p/2 = (400/900)/2. Exact to floating point.
        #expect(abs(equatorShift - 0.675 * (400.0 / 900.0) / 2) < 1e-9,
                "Equator shift should be half the occluded fraction of the displayed span, got \(equatorShift)")
        #expect(abs(northShift - equatorShift / 2) < 1e-9,
                "At 60°N a width-constrained view holds half the latitude, so the shift must halve, got \(northShift)")
    }

    // MARK: - lonCenterAndSpan: antimeridian crossing

    /// Alaska Maritime National Wildlife Refuge runs the Aleutians from
    /// -166.7256° to +173.1396°. The naive midpoint puts its center at 3.21°
    /// (off Africa) and the naive span is 339.87°, which after 1.3x padding
    /// asks MKCoordinateSpan for 441° — wider than the planet. setRegion traps
    /// on that, so tapping the refuge crashed the app every time.
    @Test func lonCenterAndSpan_antimeridian_usesShortWayRound() {
        let r = TrailMapView.lonCenterAndSpan(minLon: -166.7256, maxLon: 173.1396)
        #expect(r.span > 0 && r.span < 180,
                "An antimeridian crosser must resolve to the SHORT way round, got \(r.span)")
        #expect(abs(r.span - 20.1348) < 0.001, "Expected ~20.13° span, got \(r.span)")
        #expect(r.center < -170,
                "Center must land in the Aleutians (negative, near -177), got \(r.center)")
        #expect(abs(r.center - (-176.793)) < 0.001, "Expected ~-176.79°, got \(r.center)")
        // The actual crash condition: padded span must stay inside MapKit's range.
        #expect(r.span * 1.3 <= 360, "Padded longitude span must never exceed 360°")
    }

    /// The ordinary case must be untouched by the antimeridian branch.
    @Test func lonCenterAndSpan_normalExtent_isPlainMidpoint() {
        let r = TrailMapView.lonCenterAndSpan(minLon: -112.1, maxLon: -112.0)
        #expect(abs(r.center - (-112.05)) < 1e-9, "Expected plain midpoint, got \(r.center)")
        #expect(abs(r.span - 0.1) < 1e-9, "Expected plain span, got \(r.span)")
    }

    /// A wide-but-not-crossing extent (the contiguous US) must NOT be treated
    /// as an antimeridian crosser — its span is under the 180° threshold.
    @Test func lonCenterAndSpan_wideContiguousExtent_notTreatedAsCrossing() {
        let r = TrailMapView.lonCenterAndSpan(minLon: -124.7, maxLon: -66.9)
        #expect(abs(r.center - (-95.8)) < 1e-9, "Expected plain midpoint, got \(r.center)")
        #expect(abs(r.span - 57.8) < 1e-9, "Expected plain span, got \(r.span)")
    }

    // MARK: - Selected-route directional safe region

    @Test func directionalBottomOnlyFit_isExistingFit() {
        let existing = TrailMapView.fittedRegion(
            centerLat: 33.3, centerLon: -112,
            latDelta: 0.02, lonDelta: 0.03,
            bottomInset: 240,
            screenHeight: 800, screenWidth: 400
        )
        let directional = TrailMapView.fittedRegion(
            centerLat: 33.3, centerLon: -112,
            latDelta: 0.02, lonDelta: 0.03,
            viewportInsets: MapViewportInsets(bottom: 240),
            screenHeight: 800, screenWidth: 400,
            maximumVerticalObstructionFraction: 0.85
        )
        #expect(directional == existing)
    }

    @Test func selectedRouteRegion_accessibilityObstructionFitsThreeNearMarkers() {
        let screenHeight: CGFloat = 956
        let screenWidth: CGFloat = 440
        let minLat = 33.308428
        let maxLat = 33.362899
        let minLon = -112.147982
        let maxLon = -111.985179
        let routeCenterLat = (minLat + maxLat) / 2
        let routeCenterLon = (minLon + maxLon) / 2
        let routeLatDelta = (maxLat - minLat) * 1.4
        let routeLonDelta = (maxLon - minLon) * 1.4
        let nearMarkers: [(lat: Double, lon: Double)] = [
            (33.330292, -112.144294),
            (33.342873, -112.044261),
            (33.362764, -111.985179),
        ]
        let obstruction = MapViewportInsets(
            top: 174,
            leading: 20,
            bottom: 746,
            trailing: 20
        )
        let compatibility = TrailMapView.fittedRegion(
            centerLat: routeCenterLat,
            centerLon: routeCenterLon,
            latDelta: routeLatDelta,
            lonDelta: routeLonDelta,
            viewportInsets: obstruction,
            screenHeight: screenHeight,
            screenWidth: screenWidth
        )
        let selected = TrailMapView.selectedRouteRegion(
            points: [(minLat, minLon), (maxLat, maxLon)] + nearMarkers,
            viewportInsets: obstruction,
            screenHeight: screenHeight,
            screenWidth: screenWidth
        )
        guard case .region(
            let oldLat, let oldLon, let oldLatDelta, let oldLonDelta
        ) = compatibility,
              let selected,
              case .region(
                let selectedLat, let selectedLon,
                let selectedLatDelta, let selectedLonDelta
              ) = selected else {
            Issue.record("Expected compatibility and selected-route regions")
            return
        }

        func project(
            _ point: (lat: Double, lon: Double),
            centerLat: Double,
            centerLon: Double,
            latDelta: Double,
            lonDelta: Double
        ) -> CGPoint {
            let mercatorLatPerLon = max(0.05, cos(routeCenterLat * .pi / 180))
            let displayedLatDelta = max(
                latDelta,
                lonDelta * Double(screenHeight / screenWidth) * mercatorLatPerLon
            )
            let displayedLonDelta = max(
                lonDelta,
                latDelta * Double(screenWidth / screenHeight) / mercatorLatPerLon
            )
            return CGPoint(
                x: CGFloat(
                    (0.5 + (point.lon - centerLon) / displayedLonDelta)
                        * Double(screenWidth)
                ),
                y: CGFloat(
                    (0.5 - (point.lat - centerLat) / displayedLatDelta)
                        * Double(screenHeight)
                )
            )
        }

        let measuredFirstFrame = CGRect(x: 67, y: 290, width: 31, height: 34)
        let oldFirstPoint = project(
            nearMarkers[0],
            centerLat: oldLat,
            centerLon: oldLon,
            latDelta: oldLatDelta,
            lonDelta: oldLonDelta
        )
        let measuredOffset = CGPoint(
            x: measuredFirstFrame.minX - oldFirstPoint.x,
            y: measuredFirstFrame.minY - oldFirstPoint.y
        )
        func renderedFrames(
            centerLat: Double,
            centerLon: Double,
            latDelta: Double,
            lonDelta: Double
        ) -> [CGRect] {
            nearMarkers.map { marker in
                let oldPoint = project(
                    marker,
                    centerLat: oldLat,
                    centerLon: oldLon,
                    latDelta: oldLatDelta,
                    lonDelta: oldLonDelta
                )
                let targetPoint = project(
                    marker,
                    centerLat: centerLat,
                    centerLon: centerLon,
                    latDelta: latDelta,
                    lonDelta: lonDelta
                )
                return CGRect(
                    x: oldPoint.x + measuredOffset.x + (targetPoint.x - oldPoint.x).rounded(),
                    y: oldPoint.y + measuredOffset.y + (targetPoint.y - oldPoint.y).rounded(),
                    width: 31,
                    height: 34
                )
            }
        }
        func paddedEnvelopeIsInsideMeasuredCorridor(_ frame: CGRect) -> Bool {
            frame.minX >= 6
                && frame.maxX <= screenWidth - 6
                && frame.minY >= 134 + 6
                && frame.maxY <= 290 - 6
        }

        let compatibilityFrames = renderedFrames(
            centerLat: oldLat,
            centerLon: oldLon,
            latDelta: oldLatDelta,
            lonDelta: oldLonDelta
        )
        let selectedFrames = renderedFrames(
            centerLat: selectedLat,
            centerLon: selectedLon,
            latDelta: selectedLatDelta,
            lonDelta: selectedLonDelta
        )
        #expect(!compatibilityFrames.allSatisfy(paddedEnvelopeIsInsideMeasuredCorridor))
        #expect(selectedFrames.count == 3)
        #expect(selectedFrames.allSatisfy(paddedEnvelopeIsInsideMeasuredCorridor))
    }

    @Test func selectedVerticalCeiling_keepsStandardDirectionalGeometryUnchanged() {
        let standardInsets = MapViewportInsets(
            top: 100,
            leading: 120,
            bottom: 300,
            trailing: 40
        )
        let compatibility = TrailMapView.fittedRegion(
            centerLat: 33.35,
            centerLon: -111.95,
            latDelta: 0.14,
            lonDelta: 0.14,
            viewportInsets: standardInsets,
            screenHeight: 800,
            screenWidth: 400
        )
        let selectedCeiling = TrailMapView.fittedRegion(
            centerLat: 33.35,
            centerLon: -111.95,
            latDelta: 0.14,
            lonDelta: 0.14,
            viewportInsets: standardInsets,
            screenHeight: 800,
            screenWidth: 400,
            maximumVerticalObstructionFraction: 0.85
        )
        #expect(selectedCeiling == compatibility)
    }

    @Test func selectedRouteRegion_includesRouteEndpointsAndNearbyParking() {
        let trail = Trail(
            id: "selected-route",
            name: "Selected Route",
            distanceMi: 1,
            difficulty: .easy,
            segments: [[
                [33.30, -112.00],
                [33.40, -111.90],
            ]]
        )
        let near = ParkingLot(
            lat: 33.3005,
            lon: -112.0005,
            name: nil,
            fee: nil,
            trailhead: nil,
            source: "osm"
        )
        let far = ParkingLot(
            lat: 33.50,
            lon: -112.50,
            name: nil,
            fee: nil,
            trailhead: nil,
            source: "osm"
        )
        let nearbyParking = Area.nearestParking(lots: [near, far], for: trail)
        let farFallback = Area.nearestParkingWithFallback(lots: [far], for: trail)
        #expect(nearbyParking == [near])
        #expect(farFallback.count == 1)
        #expect(farFallback.first?.lot == far)
        #expect(farFallback.first?.isNear == false)

        guard let points = TrailMapView.selectedRoutePoints(
            segments: trail.segments,
            additionalPoints: nearbyParking.map { (lat: $0.lat, lon: $0.lon) }
        ) else {
            Issue.record("Expected valid route and nearby parking points")
            return
        }
        #expect(points.contains { $0.lat == near.lat && $0.lon == near.lon })
        #expect(!points.contains { $0.lat == far.lat && $0.lon == far.lon })

        let target = TrailMapView.selectedRouteRegion(
            points: points,
            viewportInsets: .zero,
            screenHeight: 800,
            screenWidth: 400
        )
        guard let target,
              case .region(let centerLat, let centerLon, let latDelta, let lonDelta) = target else {
            Issue.record("Expected a finite selected-route region")
            return
        }
        #expect(centerLat.isFinite && centerLon.isFinite)
        #expect(latDelta.isFinite && lonDelta.isFinite)
        for point in points {
            #expect(abs(point.lat - centerLat) <= latDelta / 2)
            #expect(abs(point.lon - centerLon) <= lonDelta / 2)
        }
    }

    @Test func selectedRouteRegion_usesShortAntimeridianExtent() {
        let target = TrailMapView.selectedRouteRegion(
            points: [(51, 179.5), (51.2, -179.5)],
            viewportInsets: .zero,
            screenHeight: 800,
            screenWidth: 400
        )
        guard let target,
              case .region(_, let centerLon, _, let lonDelta) = target else {
            Issue.record("Expected an antimeridian-safe selected-route region")
            return
        }
        #expect(abs(centerLon) == 180)
        #expect(lonDelta < 2)
    }

    @Test func selectedRouteRegion_directionalObstructionsContainVerticalBounds() {
        let points: [(lat: Double, lon: Double)] = [
            (33.30, -112.00),
            (33.40, -111.90),
        ]
        let target = TrailMapView.selectedRouteRegion(
            points: points,
            viewportInsets: MapViewportInsets(top: 100, bottom: 300),
            screenHeight: 800,
            screenWidth: 400
        )
        guard let target,
              case .region(let centerLat, _, let latDelta, _) = target else {
            Issue.record("Expected a directionally fitted selected-route region")
            return
        }
        let topFraction = 100.0 / 800.0
        let bottomFraction = 1 - 300.0 / 800.0
        for point in points {
            let normalizedY = 0.5 - (point.lat - centerLat) / latDelta
            #expect(normalizedY >= topFraction - 1e-9)
            #expect(normalizedY <= bottomFraction + 1e-9)
        }
        #expect(centerLat < 33.35, "A larger bottom obstruction must move content upward")
    }

    @Test func selectedRouteRegion_asymmetricSideObstructionsShiftLongitude() {
        let centered = TrailMapView.selectedRouteRegion(
            points: [(33.3, -112.0), (33.4, -111.9)],
            viewportInsets: .zero,
            screenHeight: 800,
            screenWidth: 400
        )
        let leading = TrailMapView.selectedRouteRegion(
            points: [(33.3, -112.0), (33.4, -111.9)],
            viewportInsets: MapViewportInsets(leading: 80),
            screenHeight: 800,
            screenWidth: 400
        )
        guard let centered,
              let leading,
              case .region(_, let centeredLon, _, _) = centered,
              case .region(_, let shiftedLon, _, let shiftedSpan) = leading else {
            Issue.record("Expected directional longitude regions")
            return
        }
        #expect(shiftedLon < centeredLon)
        #expect(shiftedSpan > 0.14)
    }

    @Test func markerFramesAreComplete_rejectsIncompleteSet() {
        let frames = [CGRect(x: 20, y: 20, width: 28, height: 40)]
        #expect(!MapKitMapView.markerFramesAreComplete(frames, expectedCount: 3))
    }

    @Test func markerFramesAreComplete_rejectsInvalidCompleteSet() {
        let frames = [
            CGRect(x: 20, y: 20, width: 28, height: 40),
            CGRect(x: CGFloat.nan, y: 30, width: 28, height: 40),
            CGRect(x: 40, y: 40, width: 0, height: 40),
        ]
        #expect(!MapKitMapView.markerFramesAreComplete(frames, expectedCount: 3))
    }

    @Test func markerFramesAreComplete_acceptsFiniteThreeOfThreeSet() {
        let frames = [
            CGRect(x: 20, y: 20, width: 28, height: 40),
            CGRect(x: 60, y: 30, width: 28, height: 40),
            CGRect(x: 100, y: 40, width: 28, height: 40),
        ]
        #expect(MapKitMapView.markerFramesAreComplete(frames, expectedCount: 3))
    }

    @Test func markerOcclusionCorrection_aggregatesEveryFrame() {
        let correction = MapKitMapView.markerOcclusionCorrection(
            markerFrames: [
                CGRect(x: 40, y: 4, width: 10, height: 10),
                CGRect(x: 4, y: 40, width: 10, height: 10),
                CGRect(x: 40, y: 84, width: 10, height: 10),
                CGRect(x: 84, y: 40, width: 10, height: 10),
            ],
            mapBounds: CGRect(x: 0, y: 0, width: 100, height: 100),
            visibleInsets: MapViewportInsets(top: 10, leading: 10, bottom: 10, trailing: 10),
            padding: 0
        )
        #expect(correction.top == 6)
        #expect(correction.leading == 6)
        #expect(correction.bottom == 4)
        #expect(correction.trailing == 4)
    }

    @Test func markerOcclusionCorrection_movesOnlyForRealSheetOverlap() {
        let correction = MapKitMapView.markerOcclusionCorrection(
            markerFrames: [CGRect(x: 150, y: 272, width: 28, height: 24)],
            mapBounds: CGRect(x: 0, y: 0, width: 375, height: 667),
            visibleInsets: MapViewportInsets(top: 88, bottom: 372),
            padding: 6
        )
        #expect(correction.top == 0)
        #expect(correction.leading == 0)
        #expect(correction.bottom == 7)
        #expect(correction.trailing == 0)
    }

    @Test func markerOcclusionCorrection_clearsMeasuredAccessibilitySheetOverlap() {
        let correction = MapKitMapView.markerOcclusionCorrection(
            markerFrames: [CGRect(x: 241, y: 256, width: 31, height: 34)],
            mapBounds: CGRect(x: 0, y: 0, width: 440, height: 956),
            visibleInsets: MapViewportInsets(top: 134, bottom: 667)
        )
        #expect(correction == MapViewportInsets(bottom: 9))
        #expect(TrailMapView.authorizesSelectedMarkerCorrection(
            isArmed: true,
            correction: correction
        ))
        #expect(!TrailMapView.authorizesSelectedMarkerCorrection(
            isArmed: false,
            correction: correction
        ))
    }

    @Test func markerOcclusionCorrection_keepsVisibleLargeMarkerUnchanged() {
        let correction = MapKitMapView.markerOcclusionCorrection(
            // Fully inside the physical screen/control/sheet viewport, while
            // deliberately crossing the old synthetic 20-point side inset.
            markerFrames: [CGRect(x: 4, y: 220, width: 28, height: 40)],
            mapBounds: CGRect(x: 0, y: 0, width: 440, height: 956),
            visibleInsets: MapViewportInsets(top: 134, bottom: 388),
            padding: 6
        )
        #expect(correction == .zero)
    }

    @Test func markerAudit_completeZeroObservationLeavesGenerationOpen() {
        let frames = [CGRect(x: 40, y: 40, width: 28, height: 40)]
        #expect(MapKitMapView.markerFramesAreComplete(frames, expectedCount: 1))
        let correction = MapKitMapView.markerOcclusionCorrection(
            markerFrames: frames,
            mapBounds: CGRect(x: 0, y: 0, width: 100, height: 100),
            visibleInsets: .zero,
            padding: 0
        )
        #expect(correction == .zero)

        var lastConsumedGeneration = 0
        let emitted = MapKitMapView.consumeMarkerAuditGenerationIfNeeded(
            generation: 7,
            correction: correction,
            lastConsumedGeneration: &lastConsumedGeneration
        )
        #expect(!emitted)
        #expect(lastConsumedGeneration == 0)
    }

    @Test func markerAudit_laterSameGenerationOcclusionEmitsOnce() {
        var lastConsumedGeneration = 0
        var reportCount = 0
        let generation = 11

        if MapKitMapView.consumeMarkerAuditGenerationIfNeeded(
            generation: generation,
            correction: .zero,
            lastConsumedGeneration: &lastConsumedGeneration
        ) {
            reportCount += 1
        }
        if MapKitMapView.consumeMarkerAuditGenerationIfNeeded(
            generation: generation,
            correction: MapViewportInsets(bottom: 7),
            lastConsumedGeneration: &lastConsumedGeneration
        ) {
            reportCount += 1
        }

        #expect(reportCount == 1)
        #expect(lastConsumedGeneration == generation)
    }

    @Test func markerAudit_nonzeroReportConsumesBeforeCallback() {
        var lastConsumedGeneration = 0
        var generationObservedByCallback = 0
        let generation = 13

        if MapKitMapView.consumeMarkerAuditGenerationIfNeeded(
            generation: generation,
            correction: MapViewportInsets(leading: 4),
            lastConsumedGeneration: &lastConsumedGeneration
        ) {
            generationObservedByCallback = lastConsumedGeneration
        }

        #expect(generationObservedByCallback == generation)
    }

    @Test func markerAudit_duplicateNonzeroReportIsBlocked() {
        var lastConsumedGeneration = 0
        var reportCount = 0
        let generation = 17
        let correction = MapViewportInsets(trailing: 5)

        for _ in 0..<2 {
            if MapKitMapView.consumeMarkerAuditGenerationIfNeeded(
                generation: generation,
                correction: correction,
                lastConsumedGeneration: &lastConsumedGeneration
            ) {
                reportCount += 1
            }
        }

        #expect(reportCount == 1)
        #expect(lastConsumedGeneration == generation)
    }

    @Test func selectedMarkerCorrection_lateOwnerShutdownCausesZeroCameraMoves() {
        let lateCorrection = MapViewportInsets(bottom: 7)
        let ownerShutdowns = [
            "gesture",
            "follow",
            "recenter",
            "recording",
            "switched-trail-retarget",
        ]
        var simulatedCameraMoves = 0
        for _ in ownerShutdowns {
            if TrailMapView.authorizesSelectedMarkerCorrection(
                isArmed: false,
                correction: lateCorrection
            ) {
                simulatedCameraMoves += 1
            }
        }

        #expect(simulatedCameraMoves == 0)
        #expect(TrailMapView.authorizesSelectedMarkerCorrection(
            isArmed: true,
            correction: lateCorrection
        ))
        #expect(!TrailMapView.authorizesSelectedMarkerCorrection(
            isArmed: true,
            correction: .zero
        ))
        #expect(!TrailMapView.authorizesSelectedMarkerCorrection(
            isArmed: true,
            correction: MapViewportInsets(top: CGFloat.nan)
        ))
    }

    @Test func selectedRouteRegion_malformedPointFailsClosed() {
        #expect(TrailMapView.selectedRoutePoints(
            segments: [[[33.3, -112], [33.4]]]
        ) == nil)
        #expect(TrailMapView.selectedRoutePoints(
            segments: [],
            additionalPoints: [(33.3, -112)]
        ) == nil)

        let valid: [(lat: Double, lon: Double)] = [(33.3, -112.0)]
        #expect(TrailMapView.selectedRouteRegion(
            points: valid + [(Double.nan, -112)],
            viewportInsets: .zero,
            screenHeight: 800,
            screenWidth: 400
        ) == nil)
        #expect(TrailMapView.selectedRouteRegion(
            points: valid + [(33.4, Double.infinity)],
            viewportInsets: .zero,
            screenHeight: 800,
            screenWidth: 400
        ) == nil)
        #expect(TrailMapView.selectedRouteRegion(
            points: valid + [(91, -112)],
            viewportInsets: .zero,
            screenHeight: 800,
            screenWidth: 400
        ) == nil)
        #expect(TrailMapView.selectedRouteRegion(
            points: valid,
            viewportInsets: .zero,
            screenHeight: 0,
            screenWidth: 400
        ) == nil)
        #expect(TrailMapView.selectedRouteRegion(
            points: valid,
            viewportInsets: MapViewportInsets(top: .nan),
            screenHeight: 800,
            screenWidth: 400
        ) == nil)
    }
}
