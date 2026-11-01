import XCTest

extension XCUICoordinate {
    /// Deliberate list scroll for the audits. The old `press(0.05s)` + drag
    /// registered as a tap on the row's Select button when the travel was
    /// short (small phone and accessibility text sizes keep the trail list
    /// viewport near 170-205 pt), and its release velocity flung the list past
    /// the target. Here the travel is never shorter than a clear scroll
    /// (`minimumTravel`), and the finger holds still before lifting so the
    /// list stops where the drag ended instead of coasting.
    func listDrag(to end: XCUICoordinate, minimumTravel: CGFloat = 64) {
        let dy = end.screenPoint.y - screenPoint.y
        var target = end
        if abs(dy) < minimumTravel {
            let extra = (dy < 0 ? -minimumTravel : minimumTravel) - dy
            target = end.withOffset(CGVector(dx: 0, dy: extra))
        }
        press(
            forDuration: 0.05,
            thenDragTo: target,
            withVelocity: .default,
            thenHoldForDuration: 0.3
        )
    }
}

/// One measured scroll step that brings `element` toward the part of
/// `scrollView` that is actually on screen. The old step picked its direction
/// from the screen midpoint and its points from the scroll view's full AX
/// frame, which on a short sheet can extend below the screen: the drag then
/// started off-screen or kept pushing a target that was already above the
/// viewport further away. Here both the direction and the distance come from
/// the visible viewport, and the travel never exceeds most of that viewport.
func stepTowardVisible(
    _ element: XCUIElement,
    in scrollView: XCUIElement,
    app: XCUIApplication
) {
    let fallback = scrollView.frame.intersection(app.frame)
    let viewport = (fallback.isNull || fallback.height < 44 ? app.frame : fallback)
        .insetBy(dx: 0, dy: 8)
    var delta: CGFloat = -viewport.height * 0.4 // content up (reveal later)
    if element.exists {
        let frame = element.frame
        if frame.minY < viewport.minY {
            delta = viewport.minY - frame.minY
        } else if frame.maxY > viewport.maxY {
            delta = -(frame.maxY - viewport.maxY)
        }
    }
    let limit = viewport.height * 0.6
    delta = max(-limit, min(limit, delta))
    let startY = delta > 0 ? viewport.minY + viewport.height * 0.2 : viewport.maxY - viewport.height * 0.2
    let origin = app.coordinate(withNormalizedOffset: .zero)
    let start = origin.withOffset(CGVector(dx: viewport.midX, dy: startY))
    let end = origin.withOffset(CGVector(dx: viewport.midX, dy: startY + delta))
    start.listDrag(to: end)
}
