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
}
