import CoreGraphics
import Testing
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
}
