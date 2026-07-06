import XCTest

/// Drives the app through the five App Store screenshots and attaches
/// each one to the test result bundle. Depends on the DEBUG-only
/// `UITestSupport` seeding hooks (launch arguments below) to populate a
/// deterministic South Mountain demo state — no real hikes or live GPS.
///
/// The CI workflow (`ios-screenshots.yml`) runs only this target, then
/// extracts the attachments from the `.xcresult` with `xcparse`.
///
/// NAVIGATION STRATEGY: every screen is reached via TabView switches and
/// NavigationStack pushes — the two navigation styles that have proven
/// reliable under XCUITest on the CI simulator. Modal presentation
/// (HomeView's `.sheet(item:)`, ContentView's `fullScreenCover`) never
/// presented in four consecutive CI runs regardless of trigger (launch
/// deep-link, card tap, banner tap), so the area is opened by PUSHING
/// AreaView from the Stats tab's "Area Progress" row instead. AreaView's
/// own inner trail-list sheet is the one modal we still depend on; every
/// wait failure dumps the accessibility tree to stdout so a CI log can
/// diagnose exactly what was on screen.
///
/// Screenshot files are numbered to match the App Store submission plan
/// (docs/app-store-submission.md) so they upload in order:
/// 01 completion map · 02 Dex · 03 recording · 04 Stats · 05 hike detail.
/// (Capture order differs — attachments are named, so order is free.)
final class ScreenshotTests: XCTestCase {

    private let areaRowId = "area-progress-south-mountain-park-and-preserve-az"

    override func setUp() {
        super.setUp()
        // Keep going after a soft failure so one flaky screen doesn't
        // abort the whole capture run.
        continueAfterFailure = true
    }

    func testCaptureAppStoreScreenshots() {
        let app = XCUIApplication()

        // ---- Launch A: seeded history ----
        app.launchArguments = ["--uitest-seed"]
        app.launch()

        // Let the launch burst finish BEFORE the first query. Right after
        // launch the app fetches silhouettes for every Explore card,
        // prefetches favorited areas from R2, and rebuilds completions
        // from history — the main thread is busy enough that an early
        // accessibility snapshot can time out ("Failed to get matching
        // snapshots"), which aborts the whole test uncatchably. Every run
        // that visited Stats late succeeded; the one that went at t≈20s
        // died here. Sleeping is immune to snapshot timeouts.
        settle(25)

        // Shot 4 — the Stats tab (totals + hikes-per-month chart).
        openStatsTab(app)
        if !app.staticTexts["Recent Hikes"].firstMatch.waitForExistence(timeout: 60) {
            dumpTree(app, "stats-tab-missing-recent-hikes")
        }
        settle(3)
        capture(app, "04-stats")

        // Shot 5 — a finished hike's detail (route map + elevation),
        // pushed from the Recent Hikes list.
        let featuredRow = app.descendants(matching: .any)["hike-row-demo-national-trail-2"].firstMatch
        if featuredRow.waitForExistence(timeout: 10) {
            tapElement(featuredRow)
            settle(5)
            capture(app, "05-hike-detail")
            goBack(app)
        } else {
            dumpTree(app, "hike-row-missing")
            XCTFail("Featured hike row not found for hike-detail shot")
        }

        // Shots 1 + 2 — push AreaView from the Area Progress row, then
        // wait for the trail-list sheet's Trails/Dex selector.
        if openAreaFromStats(app) {
            settle(8)   // let MapKit tiles + R2 polylines render
            // NOTE: do NOT tap recenter here. It calls
            // requestPermission() when location isn't authorized, and
            // the CI simctl grant doesn't reliably land as authorized
            // before the app reads it — so tapping recenter popped the
            // system location dialog on top of shot 1. The whole-park
            // overview (now halo-free via #252) is the reliable shot.
            capture(app, "01-completion-map")

            // Shot 2 — the Dex badge grid.
            tapSegment(app, "Dex")
            settle(3)
            capture(app, "02-dex")
        } else {
            // Capture anyway — even a partial render (fullscreen map,
            // no sheet) is diagnostic and possibly usable.
            settle(5)
            capture(app, "01-completion-map")
            capture(app, "02-dex")
            XCTFail("Area sheet never appeared (launch A)")
        }

        // ---- Launch B: same seed + a live active recording ----
        // The recording is injected in-memory without starting GPS, so no
        // location-permission alert can appear. Reach the area the same
        // proven way: Stats tab → Area Progress row push. The Trails
        // segment shows the live RecordingPanel because
        // recording.activeRecording.areaId matches the pushed area.
        app.terminate()
        app.launchArguments = ["--uitest-seed", "--uitest-recording"]
        app.launch()

        // Same launch-burst dwell as launch A (see comment there).
        settle(25)

        openStatsTab(app)
        _ = app.staticTexts["Recent Hikes"].firstMatch.waitForExistence(timeout: 60)

        // Shot 3 — the active recording panel (live pace + elevation).
        if openAreaFromStats(app) {
            settle(6)
            capture(app, "03-recording")
        } else {
            settle(5)
            capture(app, "03-recording")
            XCTFail("Area sheet never appeared (recording launch)")
        }
    }

    // MARK: - Navigation helpers

    private func openStatsTab(_ app: XCUIApplication) {
        let statsTab = app.tabBars.buttons["Stats"]
        if statsTab.waitForExistence(timeout: 30) {
            statsTab.tap()
            // Give the tab switch + StatsView's history load a moment
            // before the caller starts polling for content — polling an
            // app that's mid-churn is what risks snapshot timeouts.
            settle(5)
        } else {
            dumpTree(app, "tab-bar-missing")
        }
    }

    /// Push AreaView via the Stats tab's "Area Progress" row and wait
    /// for the trail sheet's Trails/Dex picker. Returns true when the
    /// picker is on screen. Dumps the accessibility tree on any wait
    /// failure so the CI log shows exactly what rendered instead.
    private func openAreaFromStats(_ app: XCUIApplication) -> Bool {
        let row = app.descendants(matching: .any)[areaRowId].firstMatch
        guard row.waitForExistence(timeout: 20) else {
            dumpTree(app, "area-progress-row-missing")
            XCTFail("Area Progress row not found")
            return false
        }
        tapElement(row)

        let picker = app.segmentedControls["area-view-picker"]
        if picker.waitForExistence(timeout: 60) { return true }
        dumpTree(app, "picker-missing-after-area-push")
        return false
    }

    /// Pop the current NavigationStack view via the nav-bar back button,
    /// falling back to an edge swipe if the bar isn't exposed.
    private func goBack(_ app: XCUIApplication) {
        let back = app.navigationBars.buttons.element(boundBy: 0)
        if back.waitForExistence(timeout: 5), back.isHittable {
            back.tap()
        } else {
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.5))
            let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
        settle(2)
    }

    /// Tap a segment of the Trails/Dex segmented control. Segments render
    /// as buttons; try the control's child first, then a bare button
    /// lookup as a fallback across iOS versions.
    private func tapSegment(_ app: XCUIApplication, _ label: String) {
        let inControl = app.segmentedControls["area-view-picker"].buttons[label]
        if inControl.waitForExistence(timeout: 5) {
            tapElement(inControl)
            return
        }
        let bare = app.buttons[label]
        if bare.waitForExistence(timeout: 5) { tapElement(bare) }
    }

    /// Tap with a coordinate fallback so a hit-test quirk can't
    /// hard-abort the run (a failed XCUIElement.tap() is fatal even
    /// with continueAfterFailure).
    private func tapElement(_ element: XCUIElement) {
        if element.isHittable {
            element.tap()
        } else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
    }

    // MARK: - Capture + diagnostics

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

    /// Print the full accessibility tree to stdout — it lands in the
    /// xcodebuild output inside the CI step log, so a failed wait can be
    /// diagnosed from the logs alone (the simulator isn't inspectable
    /// after the run).
    private func dumpTree(_ app: XCUIApplication, _ tag: String) {
        print("===== UI TREE [\(tag)] =====")
        print(app.debugDescription)
        print("===== END UI TREE [\(tag)] =====")
    }
}
