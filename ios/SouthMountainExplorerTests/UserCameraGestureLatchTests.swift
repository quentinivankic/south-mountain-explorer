import Testing
import MapKit
import UIKit
@testable import SouthMountainExplorer

/// Tests for `MapKitMapView.UserCameraGestureLatch` — the check that tells a
/// user camera gesture (from MapKit's own recognizer states) apart from our
/// programmatic camera moves, and reports it once per gesture. It replaced
/// the observer pan / pinch / rotate recognizers that blocked MapKit's
/// gestures on iOS 27.3.
struct UserCameraGestureLatchTests {
    typealias Latch = MapKitMapView.UserCameraGestureLatch

    @Test func activeStatesCountAsUserGesture() {
        #expect(Latch.isUserGestureActive([.began]))
        #expect(Latch.isUserGestureActive([.changed]))
        // Discrete zoom taps (double-tap, two-finger tap) are `.ended`
        // (== `.recognized`) while their zoom starts.
        #expect(Latch.isUserGestureActive([.ended]))
        #expect(Latch.isUserGestureActive([.possible, .changed, .possible]))
    }

    @Test func idleStatesAreNotUserGesture() {
        #expect(!Latch.isUserGestureActive([]))
        #expect(!Latch.isUserGestureActive([.possible, .possible, .possible]))
        #expect(!Latch.isUserGestureActive([.failed, .cancelled, .possible]))
    }

    @Test func programmaticCameraMoveNeverReports() {
        // Selecting a trail animates the camera with every MapKit
        // recognizer at `.possible`.
        var latch = Latch()
        let first = latch.regionWillChange(recognizerStates: [.possible, .possible])
        latch.regionDidChange()
        let second = latch.regionWillChange(recognizerStates: [.possible, .possible])
        #expect(!first)
        #expect(!second)
        #expect(!latch.reported)
    }

    @Test func userGestureReportsOncePerGesture() {
        var latch = Latch()
        let began = latch.regionWillChange(recognizerStates: [.began, .possible])
        // Further region-will-change calls inside the same gesture stay quiet.
        let midA = latch.regionWillChange(recognizerStates: [.changed, .possible])
        let midB = latch.regionWillChange(recognizerStates: [.changed, .possible])
        latch.regionDidChange()
        // The next gesture reports again.
        let next = latch.regionWillChange(recognizerStates: [.possible, .ended])
        #expect(began)
        #expect(!midA)
        #expect(!midB)
        #expect(next)
    }

    /// The check reads MapKit's recognizers from the map view and its
    /// direct subviews. Prove MKMapView keeps its pan and pinch there, or
    /// the latch would never see a finger and the opening framing would
    /// never release.
    @MainActor
    @Test func mapKitPanAndPinchAreWhereTheCheckLooks() {
        let mapView = MKMapView(frame: CGRect(x: 0, y: 0, width: 400, height: 600))
        mapView.layoutIfNeeded()
        let recognizers = ([mapView as UIView] + mapView.subviews)
            .flatMap { $0.gestureRecognizers ?? [] }
        #expect(recognizers.contains { $0 is UIPanGestureRecognizer })
        #expect(recognizers.contains { $0 is UIPinchGestureRecognizer })
    }

    @Test func programmaticMoveBetweenGesturesDoesNotReport() {
        var latch = Latch()
        let gesture = latch.regionWillChange(recognizerStates: [.began])
        latch.regionDidChange()
        let programmatic = latch.regionWillChange(recognizerStates: [.possible])
        latch.regionDidChange()
        let nextGesture = latch.regionWillChange(recognizerStates: [.changed])
        #expect(gesture)
        #expect(!programmatic)
        #expect(nextGesture)
    }
}
