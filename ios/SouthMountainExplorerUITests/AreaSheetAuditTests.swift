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

    /// Layout anchors per state tag, so the test can ASSERT the layout rather
    /// than only photograph it. Filled by `logFrames`. The search field is no
    /// longer the anchor — it exists ONLY at the full stop now — so the fit
    /// stop is measured by its always-present toolbar (the Start button) and
    /// the first visible trail-row title.
    private var toolbarY: [String: CGFloat] = [:]
    private var firstRowY: [String: CGFloat] = [:]

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

        // ---- 1. As opened: the fit stop (the sheet's opening detent) ------
        capture(app, "sheet-01-fit-initial")
        logFrames(app, "fit-initial")

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

        // ---- 5. Deselect: the toolbar and rows must return to idle --------
        if let name = firstRowName {
            tapElement(app.staticTexts[name].firstMatch)
            settle(3)
        }
        capture(app, "sheet-06-min-trail-deselected")
        logFrames(app, "min-trail-deselected")

        // The fit stop must NOT render the search field — its absence is what
        // makes the keyboard unable to yank the sheet to full.
        XCTAssertFalse(
            app.textFields["Search trails"].firstMatch.exists,
            "The search field rendered at the fit stop; it must exist only at full"
        )

        // ---- 6. Drag up to the full stop (the only other stop now) --------
        dragSheet(app, toBottom: false)
        settle(3)
        capture(app, "sheet-07-full-after-deselect")
        logFrames(app, "full-after-deselect")

        // At full the search-and-filter chrome must be present.
        XCTAssertTrue(
            app.textFields["Search trails"].firstMatch.waitForExistence(timeout: 10),
            "The search field is missing at the full stop, where the chrome lives"
        )

        // ---- 7. Back to min, open the Collection from its explicit button --
        // The horizontal pager is gone; the Collection is a labeled action in
        // the sheet toolbar, presented as its own nested sheet.
        dragSheet(app, toBottom: true)
        settle(2)
        openCollection(app)
        settle(3)
        capture(app, "sheet-08-collection-open")
        logFrames(app, "collection-open")

        // ---- 8. Close the Collection: the sheet underneath must be exactly
        // the idle min-stop layout it was before the presentation.
        closeCollection(app)
        settle(3)
        capture(app, "sheet-09-collection-closed")
        logFrames(app, "collection-closed")

        assertLayoutInvariants()
    }

    /// Turn the audit into a GATE, not just a gallery. Photographs need a human;
    /// these facts do not, and each encodes a bug that shipped to a phone.
    private func assertLayoutInvariants() {
        // 1. Deselecting must return the page to where idle had it — same
        //    toolbar position, same first-row position. Audit run 32204672482
        //    photographed the 16pt lift this catches.
        if let idle = toolbarY["min-idle"], let after = toolbarY["min-trail-deselected"] {
            XCTAssertEqual(
                after, idle, accuracy: 2,
                "Deselect left the toolbar \(Int(idle - after))pt from idle "
                + "(idle y=\(Int(idle)), deselected y=\(Int(after))). "
                + "The sheet returns to its idle height, so the chrome inside it must too."
            )
        } else {
            XCTFail("Missing toolbar measurements: \(toolbarY.keys.sorted())")
        }
        if let idle = firstRowY["min-idle"], let after = firstRowY["min-trail-deselected"] {
            XCTAssertEqual(
                after, idle, accuracy: 2,
                "Deselect left the first row \(Int(idle - after))pt from idle"
            )
        }

        // 2. Scrolling the rows must not move the toolbar — it is FIXED above
        //    the scroll view, so any movement means it became scroll content.
        if let before = toolbarY["min-idle"],
           let scrolled = toolbarY["min-after-scroll-up"],
           let back = toolbarY["min-after-scroll-back"] {
            XCTAssertEqual(scrolled, before, accuracy: 1, "Toolbar moved when the list scrolled")
            XCTAssertEqual(back, before, accuracy: 1, "Toolbar moved when the list scrolled back")
        }

        // 3. Dismissing the Collection must return the sheet to exactly the
        //    idle layout — the nested presentation may not disturb the detent
        //    or the fixed chrome underneath it.
        if let idle = toolbarY["min-idle"] {
            if let closed = toolbarY["collection-closed"] {
                XCTAssertEqual(
                    closed, idle, accuracy: 2,
                    "Closing the Collection left the toolbar \(Int(idle - closed))pt "
                    + "from idle (idle y=\(Int(idle)), after y=\(Int(closed)))"
                )
            } else {
                XCTFail("No toolbar measurement after closing the Collection")
            }
        }
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
            // Sheet top estimated from the always-present toolbar.
            let start = app.buttons["area-record-button"].firstMatch
            let anchorY = start.exists
                ? start.frame.minY - 60
                : app.frame.height * 0.62
            from = app.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: app.frame.width / 2, dy: anchorY))
        }
        // The sheet has exactly two stops (fit + full), so the upward drag
        // must release well above the fit stop's top edge or UIKit snaps
        // back to fit instead of advancing to full.
        let targetY = toBottom ? app.frame.height - 8 : app.frame.height * 0.2
        let to = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: targetY))
        from.press(forDuration: 0.1, thenDragTo: to)
    }

    /// Scroll the trail list itself: a short vertical drag INSIDE the row
    /// region, well below the toolbar so it hits scroll content. (The search
    /// field lives only at the full stop now, so the always-present Start
    /// button is the fit stop's row-band anchor.)
    private func swipeList(_ app: XCUIApplication, up: Bool) {
        let start = app.buttons["area-record-button"].firstMatch
        let topY = start.exists ? start.frame.maxY + 30 : app.frame.height * 0.8
        let a = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: topY + 90))
        let b = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: topY))
        if up { a.press(forDuration: 0.05, thenDragTo: b) }
        else { b.press(forDuration: 0.05, thenDragTo: a) }
    }

    /// Open the area Collection via its explicit toolbar button — the
    /// horizontal pager is gone, so the destination is a labeled tap.
    private func openCollection(_ app: XCUIApplication) {
        let button = app.buttons["area-collection-button"].firstMatch
        guard button.waitForExistence(timeout: 20) else {
            dumpTree(app, "collection-button-missing")
            XCTFail("Collection button not found in area sheet")
            return
        }
        tapElement(button)
    }

    /// Dismiss the Collection sheet via its Done button.
    private func closeCollection(_ app: XCUIApplication) {
        let done = app.buttons["Done"].firstMatch
        guard done.waitForExistence(timeout: 10) else {
            dumpTree(app, "collection-done-missing")
            XCTFail("Collection Done button not found")
            return
        }
        tapElement(done)
    }

    /// Tap the first visible trail row and return its name so the caller
    /// can tap it again to deselect. Rows are identified by their trail
    /// name static text sitting below the fit stop's action toolbar.
    private func tapFirstTrailRow(_ app: XCUIApplication) -> String? {
        let start = app.buttons["area-record-button"].firstMatch
        guard start.exists else {
            dumpTree(app, "no-toolbar-before-row-tap")
            return nil
        }
        let rowBandTop = start.frame.maxY + 4
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
        line("start-button", app.buttons["area-record-button"].firstMatch)
        line("search-field", app.textFields["Search trails"].firstMatch)
        let start = app.buttons["area-record-button"].firstMatch
        if start.exists { toolbarY[tag] = start.frame.minY }
        if let extra = extraText {
            line("selected-row-title", app.staticTexts[extra].firstMatch)
        }
        // The first few trail-title-looking texts, to see row boundaries —
        // banded below the toolbar (fit) or the search field (full).
        let search = app.textFields["Search trails"].firstMatch
        let bandTop = search.exists ? search.frame.maxY
            : (start.exists ? start.frame.maxY : screen.height * 0.6)
        var printed = 0
        var firstTitleY: CGFloat? = nil
        for t in app.staticTexts.allElementsBoundByIndex {
            let f = t.frame
            guard f.minY > bandTop, f.height >= 18 else { continue }
            print("AUDIT[\(tag)] text \"\(t.label.prefix(28))\": y=\(Int(f.minY)) maxY=\(Int(f.maxY))")
            if firstTitleY == nil, !t.label.contains(" mi"), !t.label.contains(" ft") {
                firstTitleY = f.minY
            }
            printed += 1
            if printed >= 8 { break }
        }
        if let y = firstTitleY { firstRowY[tag] = y }
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
        // The Area Progress section sits BELOW Streaks, Records and By Year
        // since #550 inlined Insights into Stats, and List rows do not exist
        // in the accessibility tree until they scroll on-screen — the first
        // audit run failed exactly here, with the row absent from the dumped
        // tree (run 32201804788). Swipe the list up until the row exists.
        let row = app.descendants(matching: .any)[areaRowId].firstMatch
        var swipes = 0
        while !row.exists && swipes < 12 {
            app.swipeUp()
            swipes += 1
            settle(1)
        }
        guard row.waitForExistence(timeout: 10) else {
            dumpTree(app, "area-progress-row-missing")
            XCTFail("Area Progress row not found after \(swipes) swipes")
            return false
        }
        tapElement(row)
        // Context-neutral readiness: the recenter button renders in every
        // sheet context; the search field exists only at the full stop now.
        let recenter = app.buttons["area-recenter-button"].firstMatch
        if recenter.waitForExistence(timeout: 60) { return true }
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
