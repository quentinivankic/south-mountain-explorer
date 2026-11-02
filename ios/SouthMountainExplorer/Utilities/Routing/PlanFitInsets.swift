import CoreGraphics

/// Where the planned route must sit on the map so the route and its start pin
/// stay clear of the Plan card ("Plan a Hike"). Pure geometry: the card's
/// measured frame (window coordinates) decides which edge it covers.
enum PlanFitInsets {
    /// Breathing room between the route and the card edge. Covers the pin glyph.
    static let edgePadding: CGFloat = 48
    /// Fraction of the window the card is assumed to cover before it is measured.
    static let fallbackBottomFraction: CGFloat = 0.5
    /// Never reserve more than this share of a dimension.
    static let maxShare: CGFloat = 0.85

    /// - Parameters:
    ///   - cardFrame: the card's frame in window coordinates, nil until measured.
    ///   - window: the window size the map fills.
    ///   - controlsBottom: bottom of the map controls band at the top.
    static func insets(
        cardFrame: CGRect?, window: CGSize, controlsBottom: CGFloat
    ) -> MapViewportInsets {
        let top = controlsBottom + 40
        let side: CGFloat = 20
        guard window.width > 0, window.height > 0 else {
            return MapViewportInsets(top: top, leading: side, bottom: side, trailing: side)
        }
        guard let f = cardFrame, f.width > 1, f.height > 1,
              f.minX.isFinite, f.minY.isFinite, f.maxX.isFinite, f.maxY.isFinite else {
            return MapViewportInsets(
                top: top, leading: side,
                bottom: window.height * fallbackBottomFraction + 24, trailing: side)
        }
        // A floating sheet can be narrower than the window (inset on wide screens), so
        // width alone is not enough: a side panel also runs most of the window height.
        let isSidePanel = f.width < window.width * 0.9 && f.height >= window.height * 0.8
        if isSidePanel {
            // Wide layout: the card is a panel on one side, the full height is usable.
            let bottom: CGFloat = 20
            if f.midX > window.width / 2 {
                let trailing = min(max(window.width - f.minX, 0) + edgePadding, window.width * maxShare)
                return MapViewportInsets(top: top, leading: side, bottom: bottom, trailing: trailing)
            }
            let leading = min(max(f.maxX, 0) + edgePadding, window.width * maxShare)
            return MapViewportInsets(top: top, leading: leading, bottom: bottom, trailing: side)
        }
        let covered = max(window.height - f.minY, 0) + edgePadding
        return MapViewportInsets(
            top: top, leading: side, bottom: min(covered, window.height * maxShare), trailing: side)
    }
}
