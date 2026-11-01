import CoreGraphics
import Testing
import UIKit
@testable import SouthMountainExplorer

struct DuoMapBleedAndLoadingTests {
    // MARK: map under the right rail
    @Test func wideLayoutBleedsUnderTheRail() {
        #expect(AreaPanelLayout.mapTrailingBleed(trailingInset: 40, isWide: true) == 40)
    }

    @Test func compactLayoutNeverBleeds() {
        #expect(AreaPanelLayout.mapTrailingBleed(trailingInset: 40, isWide: false) == 0)
    }

    @Test func badInsetsDoNotBleed() {
        #expect(AreaPanelLayout.mapTrailingBleed(trailingInset: 0, isWide: true) == 0)
        #expect(AreaPanelLayout.mapTrailingBleed(trailingInset: -5, isWide: true) == 0)
        #expect(AreaPanelLayout.mapTrailingBleed(trailingInset: .nan, isWide: true) == 0)
        #expect(AreaPanelLayout.mapTrailingBleed(trailingInset: 900, isWide: true) == 120)
    }

    @Test func cameraInsetKeepsFittedTrailsClearOfTheRail() {
        #expect(AreaPanelLayout.mapTrailingCameraInset(margin: 20, bleed: 40) == 60)
        #expect(AreaPanelLayout.mapTrailingCameraInset(margin: 20, bleed: 0) == 20)
    }

    // MARK: divider grabber
    @Test func grabberIsTallerThanItWasAndFitsTheHitTarget() {
        #expect(AreaPanelDivider.grabberWidth >= 5)
        #expect(AreaPanelDivider.grabberHeight >= 48)
        #expect(AreaPanelDivider.grabberWidth < AreaPanelLayout.dividerHitWidth)
        #expect(AreaPanelLayout.dividerHitWidth >= 44)
    }

    // MARK: loading draw-in ordering
    private func line(_ d: String, _ n: Int) -> SilhouetteLine {
        SilhouetteLine(d: d, p: (0..<n).map { [Double($0) * 0.001, 0] })
    }

    @Test func orderedIsDifficultyThenLongestFirst() {
        let out = LoadingSilhouetteCanvas.ordered([
            line("h", 5), line("e", 3), line("m", 9), line("e", 8), line("h", 2),
        ])
        #expect(out.map(\.d) == ["e", "e", "m", "h", "h"])
        #expect(out.map(\.p.count) == [8, 3, 9, 5, 2])
    }

    @Test func orderedDropsDegenerateLinesAndIsNeverEmptyForRealLines() {
        let out = LoadingSilhouetteCanvas.ordered([line("e", 1), line("e", 0), line("m", 2)])
        #expect(out.count == 1)
        #expect(out.first?.d == "m")
    }

    @Test func viewHoldsOrderedLinesFromInit() {
        // Regression: lines were @State filled in onAppear and never reached the
        // Canvas. The view must carry them the moment it is created.
        let sil = AreaSilhouette(
            b: [0, 0, 1, 1], l: [line("h", 4), line("e", 4)]
        )
        let view = LoadingSilhouetteCanvas(silhouette: sil)
        let mirror = Mirror(reflecting: view)
        let lines = mirror.children.first { $0.label == "orderedLines" }?.value as? [SilhouetteLine]
        #expect(lines?.map(\.d) == ["e", "h"])
    }

    // MARK: draw-in stagger and projection
    @Test func staggerLandsTheLastLineAtTheTotalDuration() {
        let n = 11
        let last = LoadingDrawIn.beginOffset(index: n - 1, count: n) + LoadingDrawIn.perLineDuration(count: n)
        #expect(abs(last - LoadingDrawIn.totalDuration) < 1e-9)
        #expect(LoadingDrawIn.beginOffset(index: 0, count: n) == 0)
        let offsets = (0..<n).map { LoadingDrawIn.beginOffset(index: $0, count: n) }
        #expect(offsets == offsets.sorted())
    }

    @Test func singleLineGetsTheWholeDuration() {
        #expect(LoadingDrawIn.perLineDuration(count: 1) == 1.0)
        #expect(LoadingDrawIn.stagger(count: 1) == 0)
        #expect(LoadingDrawIn.perLineDuration(count: 5) == 0.4)
    }

    @Test func projectionFitsAKnownPolylineIntoBounds() throws {
        // lat 0...1, lon 0...1 near the equator (cos(0.5°) ≈ 1): square area, 100x100 view, pad 24 -> 52x52 centered.
        let box: LoadingDrawIn.BBox = (w: 0, s: 0, e: 1, n: 1)
        let fit = try #require(LoadingDrawIn.fit(bbox: box, size: CGSize(width: 100, height: 100)))
        let pts = LoadingDrawIn.project([[0, 0], [1, 1], [0.5]], bbox: box, fit: fit)
        #expect(pts.count == 2)
        #expect(abs(pts[0].x - 24) < 0.01 && abs(pts[0].y - 76) < 0.01)
        #expect(abs(pts[1].x - 76) < 0.01 && abs(pts[1].y - 24) < 0.01)
    }

    @Test func projectionCentersWhenAspectDiffers() throws {
        let box: LoadingDrawIn.BBox = (w: 0, s: 0, e: 1, n: 1)
        let fit = try #require(LoadingDrawIn.fit(bbox: box, size: CGSize(width: 248, height: 100)))
        let pts = LoadingDrawIn.project([[0, 0], [1, 1]], bbox: box, fit: fit)
        #expect(abs(pts[0].x - 98) < 0.01)   // (248 - 52) / 2
        #expect(abs(pts[1].x - 150) < 0.01)
        #expect(LoadingDrawIn.fit(bbox: box, size: .zero) == nil)
    }

    @Test func difficultyColorsMatchTheirCodes() {
        #expect(LoadingDrawIn.color(forDifficulty: "e") == .systemGreen)
        #expect(LoadingDrawIn.color(forDifficulty: "m") == .systemOrange)
        #expect(LoadingDrawIn.color(forDifficulty: "h") == .systemRed)
    }

    // MARK: mount gate
    @Test func mapIsNotMountedBeforeRevealCompletes() {
        #expect(!AreaLoadingVisibility.mapMounted(hasArea: true, revealComplete: false))
        #expect(AreaLoadingVisibility.mapMounted(hasArea: true, revealComplete: true))
        #expect(!AreaLoadingVisibility.mapMounted(hasArea: false, revealComplete: true))
    }

    @Test func failureIsNeverGatedOnTheReveal() {
        #expect(AreaLoadingVisibility.showsFailure(hasArea: false, isLoading: false))
        #expect(AreaLoadingVisibility.revealDuration >= LoadingDrawIn.totalDuration)
        #expect(AreaLoadingVisibility.revealDuration < AreaLoadingVisibility.floorDuration)
    }
}
