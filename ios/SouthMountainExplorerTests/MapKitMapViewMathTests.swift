import Testing
import CoreLocation
@testable import SouthMountainExplorer

/// Tests for `MapMath` — the namespace that holds the pure
/// flat-earth distance + haversine helpers used by
/// `MapKitMapView.Coordinator.handleTap` for trail hit-testing.
/// Splitting these out of the Coordinator (where they originally
/// lived as private methods) let us exercise them without booting
/// a UIKit window.
struct MapKitMapViewMathTests {

    // MARK: - pointToSegmentDistance

    @Test func pointOnSegmentReturnsZero() {
        // Point lies exactly on the segment midpoint — distance is 0.
        let d = MapMath.pointToSegmentDistance(
            px: 5, py: 0, ax: 0, ay: 0, bx: 10, by: 0
        )
        #expect(abs(d) < 1e-9)
    }

    @Test func perpendicularDistanceToHorizontalSegment() {
        // Segment along the x-axis from (0,0) to (10,0). Point at
        // (5, 3) — perpendicular foot is at (5, 0), distance = 3.
        let d = MapMath.pointToSegmentDistance(
            px: 5, py: 3, ax: 0, ay: 0, bx: 10, by: 0
        )
        #expect(abs(d - 3) < 1e-9)
    }

    @Test func clampsToEndpointWhenPerpendicularFootOutsideSegment() {
        // Segment (0,0)→(10,0), point at (15, 0) — past the right
        // endpoint. Perpendicular foot would be at (15,0), but the
        // segment ends at (10,0); we clamp and return distance to
        // the endpoint = 5.
        let d = MapMath.pointToSegmentDistance(
            px: 15, py: 0, ax: 0, ay: 0, bx: 10, by: 0
        )
        #expect(abs(d - 5) < 1e-9)
    }

    @Test func clampsToStartpointWhenPerpendicularFootBeforeSegment() {
        let d = MapMath.pointToSegmentDistance(
            px: -5, py: 0, ax: 0, ay: 0, bx: 10, by: 0
        )
        #expect(abs(d - 5) < 1e-9)
    }

    @Test func degenerateZeroLengthSegment() {
        // Both segment endpoints coincide. Distance is just point-
        // to-endpoint Euclidean distance.
        let d = MapMath.pointToSegmentDistance(
            px: 3, py: 4, ax: 0, ay: 0, bx: 0, by: 0
        )
        #expect(abs(d - 5) < 1e-9) // 3-4-5 right triangle
    }

    // MARK: - haversineMeters

    @Test func haversine_oneDegreeLatIsAbout111Km() {
        // 1° of latitude at any longitude is ~111 km — the
        // canonical sanity check for haversine. Within 0.5% is
        // good enough.
        let d = MapMath.haversineMeters(lat1: 0, lon1: 0, lat2: 1, lon2: 0)
        let expected = 111_000.0
        let tolerance = expected * 0.005 // 0.5% relative tolerance
        #expect(abs(d - expected) < tolerance,
                "1° lat distance = \(d) m, expected ~\(expected) m")
    }

    @Test func haversine_samePointReturnsZero() {
        let d = MapMath.haversineMeters(
            lat1: 33.3, lon1: -112.0,
            lat2: 33.3, lon2: -112.0
        )
        #expect(d == 0)
    }

    @Test func haversine_symmetric() {
        let a = MapMath.haversineMeters(
            lat1: 33.3, lon1: -112.0,
            lat2: 34.0, lon2: -111.5
        )
        let b = MapMath.haversineMeters(
            lat1: 34.0, lon1: -111.5,
            lat2: 33.3, lon2: -112.0
        )
        #expect(abs(a - b) < 1e-6)
    }

    @Test func haversine_lonDistanceShorterAtHigherLatitude() {
        // 1° of longitude at the equator is ~111 km; 1° of
        // longitude at 60° latitude is ~55 km (cos 60° = 0.5).
        // Haversine should reflect this.
        let equator = MapMath.haversineMeters(lat1: 0, lon1: 0, lat2: 0, lon2: 1)
        let lat60 = MapMath.haversineMeters(lat1: 60, lon1: 0, lat2: 60, lon2: 1)
        #expect(lat60 < equator * 0.6,
                "At 60° lat, 1° lon should be ~half the equatorial distance")
        #expect(lat60 > equator * 0.4)
    }

    // MARK: - distanceFromPoint(toPolylineCoords:)

    @Test func polylineDistance_tapOnFirstPoint() {
        // Tap exactly on the first node of the polyline — distance
        // should be (essentially) zero in flat-earth meters.
        let tap = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.3001, longitude: -112.0),
        ]
        let d = MapMath.distanceFromPoint(tap, toPolylineCoords: coords)
        #expect(d < 0.5, "Tap on first point: distance = \(d) m, expected ~0")
    }

    @Test func polylineDistance_tapOffSegment() {
        // Polyline runs north (constant longitude). Tap is east of
        // the polyline by ~0.0001°, which at lat 33.3 is ~9.3 m.
        // The flat-earth approximation in MapMath should be within
        // a meter or two of that.
        let tap = CLLocationCoordinate2D(latitude: 33.3, longitude: -111.9999)
        let coords = [
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.31, longitude: -112.0),
        ]
        let d = MapMath.distanceFromPoint(tap, toPolylineCoords: coords)
        // At lat 33.3, 0.0001° lon ≈ 111000 * cos(33.3°) * 0.0001
        // ≈ 9.28 m. Allow ±1 m for rounding.
        #expect(abs(d - 9.28) < 1.0, "Off-segment distance = \(d) m, expected ~9.28 m")
    }

    @Test func polylineDistance_degenerateSinglePointReturnsInfinity() {
        let tap = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        let coords = [CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)]
        let d = MapMath.distanceFromPoint(tap, toPolylineCoords: coords)
        #expect(d.isInfinite)
    }

    @Test func polylineDistance_emptyReturnsInfinity() {
        let tap = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        let d = MapMath.distanceFromPoint(tap, toPolylineCoords: [])
        #expect(d.isInfinite)
    }

    @Test func polylineDistance_picksMinAcrossSegments() {
        // Two segments: one passing close to the tap, one far away.
        // The function should pick the closer segment's distance.
        let tap = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        let coords = [
            // Far segment — well to the east.
            CLLocationCoordinate2D(latitude: 33.3, longitude: -111.0),
            CLLocationCoordinate2D(latitude: 33.31, longitude: -111.0),
            // Close segment — passes through the tap.
            CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0),
            CLLocationCoordinate2D(latitude: 33.31, longitude: -112.0),
        ]
        let d = MapMath.distanceFromPoint(tap, toPolylineCoords: coords)
        #expect(d < 1.0, "Should pick the close segment: \(d) m")
    }
}
