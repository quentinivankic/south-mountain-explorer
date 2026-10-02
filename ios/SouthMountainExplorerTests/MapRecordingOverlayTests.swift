import MapKit
import Testing
@testable import SouthMountainExplorer

@MainActor
struct MapRecordingOverlayTests {
    private typealias Coordinator = MapKitMapView.Coordinator

    private func point(_ latitude: Double, _ timestampMs: Double) -> GpsPoint {
        [latitude, -112.0, timestampMs]
    }

    private func coordinates(of overlay: MKPolyline) -> [CLLocationCoordinate2D] {
        var coordinates = Array(
            repeating: CLLocationCoordinate2D(),
            count: overlay.pointCount
        )
        overlay.getCoordinates(
            &coordinates,
            range: NSRange(location: 0, length: overlay.pointCount)
        )
        return coordinates
    }

    private func latitudes(of overlay: MKPolyline, match expected: [CLLocationDegrees]) -> Bool {
        let actual = coordinates(of: overlay).map(\.latitude)
        return actual.count == expected.count
            && zip(actual, expected).allSatisfy { abs($0 - $1) <= 0.000_001 }
    }

    @Test func continuousPathProducesOneOverlay() {
        let mapView = MKMapView()
        var overlays: [MKPolyline] = []
        var overlayIds: Set<ObjectIdentifier> = []

        Coordinator.reconcileRecordingOverlays(
            on: mapView,
            path: [point(33.30, 0), point(33.31, 2_000), point(33.32, 4_000)],
            overlays: &overlays,
            overlayIds: &overlayIds
        )

        #expect(overlays.count == 1)
        #expect(overlays[0].pointCount == 3)
        let overlayIdentitySetMatches = overlayIds == Set([ObjectIdentifier(overlays[0])])
        #expect(overlayIdentitySetMatches, "Recording overlay identity tracking is inconsistent")
    }

    @Test func materialGapProducesIndependentPolylinesWithNoConnector() {
        let mapView = MKMapView()
        var overlays: [MKPolyline] = []
        var overlayIds: Set<ObjectIdentifier> = []
        let path = [
            point(33.30, 0), point(33.31, 2_000),
            point(33.40, 100_000), point(33.41, 102_000),
        ]

        Coordinator.reconcileRecordingOverlays(
            on: mapView,
            path: path,
            overlays: &overlays,
            overlayIds: &overlayIds
        )

        #expect(overlays.count == 2)
        let firstRunCoordinatesMatch = latitudes(of: overlays[0], match: [33.30, 33.31])
        #expect(firstRunCoordinatesMatch, "The first continuous run has unexpected coordinates")
        let secondRunCoordinatesMatch = latitudes(of: overlays[1], match: [33.40, 33.41])
        #expect(secondRunCoordinatesMatch, "The resumed run has unexpected coordinates")
        #expect(overlays.allSatisfy { $0.pointCount == 2 })
    }

    @Test func onePointResumedRunDrawsNoConnector() {
        let mapView = MKMapView()
        var overlays: [MKPolyline] = []
        var overlayIds: Set<ObjectIdentifier> = []

        Coordinator.reconcileRecordingOverlays(
            on: mapView,
            path: [point(33.30, 0), point(33.31, 2_000), point(33.40, 100_000)],
            overlays: &overlays,
            overlayIds: &overlayIds
        )

        #expect(overlays.count == 1)
        let retainedRunCoordinatesMatch = latitudes(of: overlays[0], match: [33.30, 33.31])
        #expect(retainedRunCoordinatesMatch, "A one-point resumed run changed the prior run")
    }

    @Test func appendReplacesOnlyActiveRunAndPreservesUnrelatedOverlay() {
        let mapView = MKMapView()
        let unrelated = MKCircle(center: CLLocationCoordinate2D(latitude: 33.0, longitude: -112.0), radius: 10)
        mapView.addOverlay(unrelated)
        var overlays: [MKPolyline] = []
        var overlayIds: Set<ObjectIdentifier> = []

        Coordinator.reconcileRecordingOverlays(
            on: mapView,
            path: [point(33.30, 0), point(33.31, 2_000)],
            overlays: &overlays,
            overlayIds: &overlayIds
        )
        let finalizedOverlay = overlays[0]

        Coordinator.reconcileRecordingOverlays(
            on: mapView,
            path: [
                point(33.30, 0), point(33.31, 2_000),
                point(33.40, 100_000), point(33.41, 102_000),
            ],
            overlays: &overlays,
            overlayIds: &overlayIds
        )
        let firstActiveOverlay = overlays[1]

        Coordinator.reconcileRecordingOverlays(
            on: mapView,
            path: [
                point(33.30, 0), point(33.31, 2_000),
                point(33.40, 100_000), point(33.41, 102_000), point(33.42, 104_000),
            ],
            overlays: &overlays,
            overlayIds: &overlayIds
        )

        let finalizedIdentityWasPreserved = overlays[0] === finalizedOverlay
        #expect(finalizedIdentityWasPreserved, "Appending replaced a finalized recording run")
        let activeIdentityWasReplaced = overlays[1] !== firstActiveOverlay
        #expect(activeIdentityWasReplaced, "Appending did not replace the active recording run")
        #expect(mapView.overlays.contains { ObjectIdentifier($0) == ObjectIdentifier(unrelated) })
        let trackedIdentitiesMatch = overlayIds == Set(overlays.map { ObjectIdentifier($0) })
        #expect(trackedIdentitiesMatch, "Tracked recording overlay identities are inconsistent")
    }

    @Test func endingRecordingRemovesOnlyRecordingOverlays() {
        let mapView = MKMapView()
        let unrelated = MKCircle(center: CLLocationCoordinate2D(latitude: 33.0, longitude: -112.0), radius: 10)
        mapView.addOverlay(unrelated)
        var overlays: [MKPolyline] = []
        var overlayIds: Set<ObjectIdentifier> = []
        Coordinator.reconcileRecordingOverlays(
            on: mapView,
            path: [point(33.30, 0), point(33.31, 2_000)],
            overlays: &overlays,
            overlayIds: &overlayIds
        )

        Coordinator.removeRecordingOverlays(
            from: mapView,
            overlays: &overlays,
            overlayIds: &overlayIds
        )

        #expect(overlays.isEmpty)
        #expect(overlayIds.isEmpty)
        #expect(mapView.overlays.count == 1)
        let unrelatedIdentityWasPreserved =
            ObjectIdentifier(mapView.overlays[0]) == ObjectIdentifier(unrelated)
        #expect(unrelatedIdentityWasPreserved, "Recording cleanup removed an unrelated overlay")
    }
}
