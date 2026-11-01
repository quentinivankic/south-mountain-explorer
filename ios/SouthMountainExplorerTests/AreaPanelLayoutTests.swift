import CoreGraphics
import Testing
@testable import SouthMountainExplorer

struct AreaPanelLayoutTests {
    // MARK: gate

    @Test func compactWindowsStayOnTheSheet() {
        // iPhone, folded Duo outer screen, and a window just under the threshold.
        for w: CGFloat in [393, 440, 466, 599] {
            #expect(!AreaPanelLayout.isWide(windowWidth: w, usableWidth: w, accessibility: false))
        }
    }

    @Test func unfoldedDuoIsWide() {
        #expect(AreaPanelLayout.isWide(windowWidth: 669, usableWidth: 629, accessibility: false))
        #expect(AreaPanelLayout.isWide(windowWidth: 951, usableWidth: 911, accessibility: true))
    }

    @Test func wideFallsBackToCompactWhenPanelAndMapCannotFit() {
        // Accessibility panel min 360 + map min 280 = 640 usable.
        #expect(!AreaPanelLayout.isWide(windowWidth: 669, usableWidth: 630, accessibility: true))
        #expect(AreaPanelLayout.isWide(windowWidth: 669, usableWidth: 640, accessibility: true))
        #expect(!AreaPanelLayout.isWide(windowWidth: .nan, usableWidth: 700, accessibility: false))
    }

    // MARK: clamp

    @Test func defaultIsHalfAndClampedToMinimums() {
        // 629 usable: half = 314.5, raised to the 320 minimum.
        #expect(AreaPanelLayout.panelWidth(fraction: nil, usableWidth: 629, accessibility: false) == 320)
        #expect(AreaPanelLayout.panelWidth(fraction: 0, usableWidth: 911, accessibility: false) == 455.5)
        // Accessibility default is 55%, minimum 360.
        #expect(AreaPanelLayout.panelWidth(fraction: nil, usableWidth: 911, accessibility: true) == 911 * 0.55)
    }

    @Test func mapAlwaysKeepsItsMinimumAndPanelStaysWithinFractions() {
        let requests: [CGFloat] = [-50, 0, 100, 300, 500, 900, 5000, .infinity, .nan]
        for usable: CGFloat in [600, 629, 700, 911] {
            for requested in requests {
                let w = AreaPanelLayout.clampedWidth(requested, usableWidth: usable, accessibility: false)
                #expect(w >= AreaPanelLayout.minPanelWidth)
                #expect(usable - w >= AreaPanelLayout.minMapWidth)
                #expect(w <= max(0.7 * usable, AreaPanelLayout.minPanelWidth))
            }
        }
    }

    @Test func fractionRoundTripsAndIsBounded() {
        let usable: CGFloat = 911
        let w = AreaPanelLayout.clampedWidth(500, usableWidth: usable, accessibility: false)
        let f = AreaPanelLayout.fraction(forWidth: w, usableWidth: usable)
        let back = AreaPanelLayout.panelWidth(fraction: f, usableWidth: usable, accessibility: false)
        #expect(abs(back - w) < 0.001)
        #expect(AreaPanelLayout.fraction(forWidth: 10, usableWidth: usable) == AreaPanelLayout.minFraction)
        #expect(AreaPanelLayout.fraction(forWidth: 900, usableWidth: usable) == AreaPanelLayout.maxFraction)
        #expect(AreaPanelLayout.fraction(forWidth: 400, usableWidth: 0) == AreaPanelLayout.defaultFraction)
    }

    @Test func storedFractionSurvivesAWindowResize() {
        let a = AreaPanelLayout.panelWidth(fraction: 0.6, usableWidth: 700, accessibility: false)
        let b = AreaPanelLayout.panelWidth(fraction: 0.6, usableWidth: 911, accessibility: false)
        #expect(abs(a - 420) < 0.001)
        #expect(abs(b - 546.6) < 0.001)
    }

    // MARK: VoiceOver

    @Test func voiceOverStepsFivePercentAndClamps() {
        let usable: CGFloat = 900
        let start: CGFloat = 450
        let up = AreaPanelLayout.steppedWidth(from: start, steps: 1, usableWidth: usable, accessibility: false)
        let down = AreaPanelLayout.steppedWidth(from: start, steps: -1, usableWidth: usable, accessibility: false)
        #expect(abs(up - 495) < 0.001)
        #expect(abs(down - 405) < 0.001)
        #expect(AreaPanelLayout.percent(width: up, usableWidth: usable) == 55)
        // 70% cap is 630, and the map keeps 280 (max 620): stepping past clamps.
        let pinned = AreaPanelLayout.steppedWidth(from: 620, steps: 1, usableWidth: usable, accessibility: false)
        #expect(abs(pinned - 620) < 0.001)
        #expect(AreaPanelLayout.dividerHitWidth >= 44)
    }

    // MARK: map viewport

    @Test func compactMapViewportEqualsWindow() {
        let window = CGSize(width: 440, height: 956)
        // A full-bleed map measures the window, so camera math is unchanged.
        #expect(MapViewportSizing.resolve(measured: window, fallback: window) == window)
        // Before first layout, or on a bad measurement, the window is used.
        #expect(MapViewportSizing.resolve(measured: .zero, fallback: window) == window)
        #expect(MapViewportSizing.resolve(measured: CGSize(width: CGFloat.nan, height: 10), fallback: window) == window)
        // A side-panel map uses its own, narrower size.
        let pane = CGSize(width: 335, height: 951)
        #expect(MapViewportSizing.resolve(measured: pane, fallback: CGSize(width: 669, height: 951)) == pane)
    }

    // MARK: Task A, loading handoff

    @Test func mapMountsUnderTheSilhouetteUntilTheFloorElapses() {
        // Area landed before the 1.5 s floor: the silhouette still overlays a mounted map.
        #expect(AreaLoadingVisibility.overlayVisible(hasArea: true, isLoading: false, floorElapsed: false))
        #expect(AreaLoadingVisibility.overlayVisible(hasArea: true, isLoading: true, floorElapsed: false))
        // Floor elapsed: the overlay fades out over the live map.
        #expect(!AreaLoadingVisibility.overlayVisible(hasArea: true, isLoading: false, floorElapsed: true))
        // A Try Again reload with the area present never re-covers the map.
        #expect(!AreaLoadingVisibility.overlayVisible(hasArea: true, isLoading: true, floorElapsed: true))
    }

    @Test func loadingAndFailureStatesStayExplicit() {
        #expect(AreaLoadingVisibility.overlayVisible(hasArea: false, isLoading: true, floorElapsed: true))
        #expect(AreaLoadingVisibility.overlayVisible(hasArea: false, isLoading: true, floorElapsed: false))
        #expect(!AreaLoadingVisibility.overlayVisible(hasArea: false, isLoading: false, floorElapsed: false))
        #expect(AreaLoadingVisibility.showsFailure(hasArea: false, isLoading: false))
        #expect(!AreaLoadingVisibility.showsFailure(hasArea: false, isLoading: true))
        #expect(!AreaLoadingVisibility.showsFailure(hasArea: true, isLoading: false))
    }
}
