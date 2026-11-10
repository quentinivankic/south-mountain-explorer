import Testing
import CoreGraphics
import MapKit
@testable import SouthMountainExplorer

/// Scale math behind constant-width trail lines on realistic-elevation maps.
struct LineDisplayZoomScaleTests {
    private let view = CGSize(width: 466, height: 678)

    @Test func northUpScaleIsViewWidthOverVisibleWidth() {
        let s = LineDisplayZoomScale.zoomScale(viewSize: view, visibleMapWidth: 97_792, headingDegrees: 0)
        #expect(abs(Double(s ?? 0) - 466.0 / 97_792) < 1e-12)
    }

    @Test func rotatedMapReadsSameScaleAsUnrotated() {
        // A 0.004 scale viewport rotated 30 degrees has a bounding box
        // (w·cos + h·sin) / 0.004 map points wide.
        let scale = 0.004
        let r = 30.0 * .pi / 180
        let box = (466 * cos(r) + 678 * sin(r)) / scale
        let s = LineDisplayZoomScale.zoomScale(viewSize: view, visibleMapWidth: box, headingDegrees: 30)
        #expect(abs(Double(s ?? 0) - scale) < 1e-12)
        let sideways = LineDisplayZoomScale.zoomScale(viewSize: view, visibleMapWidth: 678 / scale, headingDegrees: 270)
        #expect(abs(Double(sideways ?? 0) - scale) < 1e-12)
    }

    @Test func degenerateInputsGiveNil() {
        #expect(LineDisplayZoomScale.zoomScale(viewSize: .zero, visibleMapWidth: 1000, headingDegrees: 0) == nil)
        #expect(LineDisplayZoomScale.zoomScale(viewSize: view, visibleMapWidth: 0, headingDegrees: 0) == nil)
        #expect(LineDisplayZoomScale.zoomScale(viewSize: view, visibleMapWidth: .infinity, headingDegrees: 0) == nil)
    }

    @Test func redrawsOnlyOnRealScaleChange() {
        #expect(!LineDisplayZoomScale.needsRedraw(from: nil, to: nil))
        #expect(LineDisplayZoomScale.needsRedraw(from: nil, to: 0.004))
        #expect(LineDisplayZoomScale.needsRedraw(from: 0.004, to: nil))
        #expect(!LineDisplayZoomScale.needsRedraw(from: 0.004, to: 0.00404))
        #expect(LineDisplayZoomScale.needsRedraw(from: 0.004, to: 0.0048))
        #expect(LineDisplayZoomScale.needsRedraw(from: 0.004, to: 0.002))
    }

    @Test func sharedValueRoundTrips() {
        let box = LineDisplayZoomScale()
        #expect(box.value == nil)
        box.value = 0.0078125
        #expect(box.value == 0.0078125)
        box.value = nil
        #expect(box.value == nil)
    }
}
