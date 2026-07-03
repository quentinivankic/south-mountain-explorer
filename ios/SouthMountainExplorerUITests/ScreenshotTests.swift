import XCTest

/// Drives the app through the five App Store screenshots and attaches
/// each one to the test result bundle. Depends on the DEBUG-only
/// `UITestSupport` seeding hooks (launch arguments below) to populate a
/// deterministic South Mountain demo state — no real hikes or live GPS.
///
/// The CI workflow (`ios-screenshots.yml`) runs only this target, then
/// extracts the attachments from the `.xcresult` with `xcparse`.
///
/// Ordering matches the App Store submission plan
/// (docs/app-store-submission.md): completion map → Dex → live
/// recording → Stats → hike detail. Files are numbered so they upload
/// in that order.
final class ScreenshotTests: XCTestCase {

    override func setUp() {
        super.setUp()
        // Keep going after a soft failure so one flaky screen doesn't
        // abort the whole capture run.
        continueAfterFailure = true
    }

    func testCaptureAppStoreScreenshots() {
        let app = XCUIApplication()

        // ---- Launch A: seeded history, deep-link into South Mountain ----
        app.launchArguments = ["--uitest-seed", "--uitest-open-area"]
        app.launch()

        // Shot 1 — park map + completed (mint) trails + trail-list sheet.
        // Wait for the area sheet's Trails/Dex selector, then let MapKit +
        // the R2 trail geometry finish drawing the polylines.
        let picker = app.segmentedControls["area-view-picker"]
        XCTAssertTrue(picker.waitForExistence(timeout: 90), "Area sheet never appeared")
        settle(8)
        capture(app, "01-completion-map")

        // Shot 2 — the Dex badge grid.
        tapSegment(app, "Dex")
        settle(3)
        capture(app, "02-dex")

        // Back to trails, then close the area to reach the tab bar.
        tapSegment(app, "Trails")
        let close = app.buttons["area-close-button"]
        if close.waitForExistence(timeout: 5) { close.tap() }
        settle(2)

        // Shot 4 — the Stats tab (totals + hikes-per-month chart).
        if app.tabBars.buttons["Stats"].waitForExistence(timeout: 10) {
            app.tabBars.buttons["Stats"].tap()
        }
        _ = app.staticTexts["Recent Hikes"].waitForExistence(timeout: 20)
        settle(3)
        capture(app, "04-stats")

        // Shot 5 — a finished hike's detail (route map + elevation).
        let featuredRow = app.descendants(matching: .any)["hike-row-demo-national-trail-2"]
        if featuredRow.waitForExistence(timeout: 10) {
            featuredRow.tap()
            settle(5)
            capture(app, "05-hike-detail")
        } else {
            XCTFail("Featured hike row not found for hike-detail shot")
        }

        // ---- Launch B: same seed + a live active recording ----
        app.terminate()
        app.launchArguments = ["--uitest-seed", "--uitest-recording", "--uitest-open-area"]
        app.launch()

        // Shot 3 — the active recording panel (live pace + elevation).
        let picker2 = app.segmentedControls["area-view-picker"]
        XCTAssertTrue(picker2.waitForExistence(timeout: 90), "Area sheet never appeared (recording launch)")
        settle(6)
        capture(app, "03-recording")
    }

    // MARK: - Helpers

    /// Tap a segment of the Trails/Dex segmented control. Segments render
    /// as buttons; try the control's child first, then a bare button
    /// lookup as a fallback across iOS versions.
    private func tapSegment(_ app: XCUIApplication, _ label: String) {
        let inControl = app.segmentedControls["area-view-picker"].buttons[label]
        if inControl.waitForExistence(timeout: 5) {
            inControl.tap()
            return
        }
        let bare = app.buttons[label]
        if bare.waitForExistence(timeout: 5) { bare.tap() }
    }

    /// Fixed dwell so animations / map tiles / async loads settle before
    /// the frame is grabbed. UI-test `sleep` is the pragmatic tool here —
    /// there's no queryable "map finished rendering" signal.
    private func settle(_ seconds: UInt32) {
        sleep(seconds)
    }

    private func capture(_ app: XCUIApplication, _ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
