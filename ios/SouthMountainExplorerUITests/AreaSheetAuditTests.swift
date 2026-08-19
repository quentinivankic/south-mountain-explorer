import XCTest

/// Photographs the area sheet in every state the clipping has ever been
/// reported in, so the layout can be SEEN from CI instead of reasoned
/// about from source. This exists because the sheet was "fixed" blind
/// seven-plus times: there is no Mac in the dev loop, so every previous
/// attempt shipped to the user's phone untested and several made it worse.
///
/// Run via `ios-screenshots.yml` with `test_class: AreaSheetAuditTests`.
/// Reuses ScreenshotTests' proven navigation: Stats tab → Area Progress
/// row → pushed AreaView (modal presentation never fired under XCUITest
/// on the CI simulator; the push always does).
///
/// Every capture also logs the FRAMES of the load-bearing elements
/// (search field, first row, area name) so the CI log carries numbers
/// alongside the pixels — a frame whose maxY exceeds the screen's is a
/// clip even before a human looks at the PNG.
final class AreaSheetAuditTests: XCTestCase {

    private let areaRowId = "area-progress-south-mountain-park-and-preserve-az"

    override func setUp() {
        super.setUp()
        continueAfterFailure = true
    }

    func testAuditAreaSheetStates() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"]
        app.launch()

        // Let the launch burst (silhouette fetches, R2 prefetch, history
        // rebuild) finish before the first accessibility query — an early
        // snapshot can time out, which aborts the run uncatchably.
        settle(25)

        openStatsTab(app)
        _ = app.staticTexts["Recent Hikes"].firstMatch.waitForExistence(timeout: 60)

        guard openAreaFromStats(app) else {
            capture(app, "sheet-00-area-never-opened")
            XCTFail("Area sheet never appeared")
            return
        }

        // Trail geometry comes from R2 at runtime; give the list a moment
        // beyond the search field's appearance so rows exist to photograph.
        settle(8)

        // ---- 1. As opened: the medium stop --------------------------------
        capture(app, "sheet-01-medium-initial")
        logFrames(app, "medium-initial")

        // ---- 2. The smallest stop: the state in every bug report ----------
        dragSheet(app, toBottom: true)
        settle(3)
        capture(app, "sheet-02-min-idle")
        logFrames(app, "min-idle")

        // ---- 3. Scroll the list at the min stop, then let it settle -------
        // The 298-era clip was a stale scroll offset; this state either
        // reproduces an offset problem or proves scrolling is clean.
        swipeList(app, up: true)
        settle(3)
        capture(app, "sheet-03-min-after-scroll-up")
        logFrames(app, "min-after-scroll-up")

        swipeList(app, up: false)
        settle(3)
        capture(app, "sheet-04-min-after-scroll-back")
        logFrames(app, "min-after-scroll-back")

        // ---- 4. Select a trail at the min stop ----------------------------
        let firstRowName = tapFirstTrailRow(app)
        settle(3)
        capture(app, "sheet-05-min-trail-selected")
        logFrames(app, "min-trail-selected", extraText: firstRowName)

        // ---- 5. Deselect: the search bar must come back whole -------------
        if let name = firstRowName {
            tapElement(app.staticTexts[name].firstMatch)
            settle(3)
        }
        capture(app, "sheet-06-min-trail-deselected")
        logFrames(app, "min-trail-deselected")

        // ---- 6. Drag up to the half stop ----------------------------------
        dragSheet(app, toBottom: false)
        settle(3)
        capture(app, "sheet-07-half-after-deselect")
        logFrames(app, "half-after-deselect")

        // ---- 7. Back to min, swipe to the Record page ---------------------
        dragSheet(app, toBottom: true)
        settle(2)
        swipePage(app, toward: .right)   // Record page sits LEFT of Trails
        settle(3)
        capture(app, "sheet-08-min-record-page")
        logFrames(app, "min-record-page")
    }

    // MARK: - Sheet + list gestures

    /// Drag the sheet by its header region. The area name is the sheet's
    /// one always-draggable, always-identifiable handle: it is not inside
    /// the scroll view, so dragging it moves the SHEET, not the list.
    /// When the name is hidden (a trail selected at the min stop), fall
    /// back to a coordinate just under the drag indicator, derived from
    /// the search field's frame.
    private func dragSheet(_ app: XCUIApplication, toBottom: Bool) {
        let name = app.staticTexts["South Mountain Park and Preserve"].firstMatch
        let from: XCUICoordinate
        if name.exists, name.isHittable {
            from = name.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        } else {
            // Sheet top estimated from whatever chrome is visible.
            let search = app.textFields["Search trails"].firstMatch
            let anchorY = search.exists
                ? search.frame.minY - 30
                : app.frame.height * 0.62
            from = app.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: app.frame.width / 2, dy: anchorY))
        }
        let targetY = toBottom ? app.frame.height - 8 : app.frame.height * 0.5
        let to = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: targetY))
        from.press(forDuration: 0.1, thenDragTo: to)
    }

    /// Scroll the trail list itself: a short vertical drag INSIDE the row
    /// region, well below the search field so it hits scroll content.
    private func swipeList(_ app: XCUIApplication, up: Bool) {
        let search = app.textFields["Search trails"].firstMatch
        let topY = search.exists ? search.frame.maxY + 60 : app.frame.height * 0.8
        let a = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: topY + 90))
        let b = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: topY))
        if up { a.press(forDuration: 0.05, thenDragTo: b) }
        else { b.press(forDuration: 0.05, thenDragTo: a) }
    }

    private enum PageDirection { case left, right }

    /// Swipe horizontally across the sheet's page region.
    private func swipePage(_ app: XCUIApplication, toward: PageDirection) {
        let y = 0.85
        let fromX = toward == .right ? 0.15 : 0.85
        let toX = toward == .right ? 0.85 : 0.15
        let from = app.coordinate(withNormalizedOffset: CGVector(dx: fromX, dy: y))
        let to = app.coordinate(withNormalizedOffset: CGVector(dx: toX, dy: y))
        from.press(forDuration: 0.05, thenDragTo: to)
    }

    /// Tap the first visible trail row and return its name so the caller
    /// can tap it again to deselect. Rows are identified by their trail
    /// name static text sitting below the search field.
    private func tapFirstTrailRow(_ app: XCUIApplication) -> String? {
        let search = app.textFields["Search trails"].firstMatch
        guard search.exists else {
            dumpTree(app, "no-search-field-before-row-tap")
            return nil
        }
        let rowBandTop = search.frame.maxY + 4
        // Find the topmost static text below the chrome that looks like a
        // trail title (skips distance/difficulty captions by height).
        let texts = app.staticTexts.allElementsBoundByIndex
        var best: XCUIElement?
        var bestY = CGFloat.greatestFiniteMagnitude
        for t in texts {
            let f = t.frame
            guard f.minY > rowBandTop, f.height >= 18, f.minX < app.frame.width * 0.5 else { continue }
            let label = t.label
            guard !label.isEmpty, !label.contains(" mi"), !label.contains(" ft") else { continue }
            if f.minY < bestY { bestY = f.minY; best = t }
        }
        guard let row = best else {
            dumpTree(app, "no-trail-row-found")
            return nil
        }
        let name = row.label
        print("AUDIT tapping first trail row: \(name)")
        tapElement(row)
        return name
    }

    // MARK: - Frame logging

    /// Print the frames that decide whether this layout is clipped. The
    /// screen height is printed alongside so `maxY > screen` is readable
    /// straight off the CI log.
    private func logFrames(_ app: XCUIApplication, _ tag: String, extraText: String? = nil) {
        let screen = app.frame
        func line(_ label: String, _ e: XCUIElement) {
            guard e.exists else { print("AUDIT[\(tag)] \(label): MISSING"); return }
            let f = e.frame
            print("AUDIT[\(tag)] \(label): x=\(Int(f.minX)) y=\(Int(f.minY)) w=\(Int(f.width)) h=\(Int(f.height)) maxY=\(Int(f.maxY)) screenH=\(Int(screen.height))")
        }
        print("AUDIT[\(tag)] ---- frames ----")
        line("area-name", app.staticTexts["South Mountain Park and Preserve"].firstMatch)
        line("search-field", app.textFields["Search trails"].firstMatch)
        if let extra = extraText {
            line("selected-row-title", app.staticTexts[extra].firstMatch)
        }
        // The first few trail-title-looking texts, to see row boundaries.
        let search = app.textFields["Search trails"].firstMatch
        let bandTop = search.exists ? search.frame.maxY : screen.height * 0.6
        var printed = 0
        for t in app.staticTexts.allElementsBoundByIndex {
            let f = t.frame
            guard f.minY > bandTop, f.height >= 18 else { continue }
            print("AUDIT[\(tag)] text \"\(t.label.prefix(28))\": y=\(Int(f.minY)) maxY=\(Int(f.maxY))")
            printed += 1
            if printed >= 8 { break }
        }
        print("AUDIT[\(tag)] ---- end frames ----")
    }

    // MARK: - Shared helpers (duplicated from ScreenshotTests; both
    // classes keep them private so neither can drift the other)

    private func openStatsTab(_ app: XCUIApplication) {
        let statsTab = app.tabBars.buttons["Stats"]
        guard statsTab.waitForExistence(timeout: 30) else {
            dumpTree(app, "tab-bar-missing")
            return
        }
        for attempt in 1...4 {
            statsTab.tap()
            settle(5)
            if statsTab.isSelected { return }
            print("Stats tab tap #\(attempt) didn't land (still not selected); retrying")
        }
        dumpTree(app, "stats-tab-never-selected")
    }

    private func openAreaFromStats(_ app: XCUIApplication) -> Bool {
        let row = app.descendants(matching: .any)[areaRowId].firstMatch
        guard row.waitForExistence(timeout: 20) else {
            dumpTree(app, "area-progress-row-missing")
            XCTFail("Area Progress row not found")
            return false
        }
        tapElement(row)
        let search = app.textFields["Search trails"]
        if search.waitForExistence(timeout: 60) { return true }
        dumpTree(app, "area-sheet-missing-after-area-push")
        return false
    }

    private func tapElement(_ element: XCUIElement) {
        if element.isHittable {
            element.tap()
        } else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
    }

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

    private func dumpTree(_ app: XCUIApplication, _ tag: String) {
        print("===== UI TREE [\(tag)] =====")
        print(app.debugDescription)
        print("===== END UI TREE [\(tag)] =====")
    }
}
