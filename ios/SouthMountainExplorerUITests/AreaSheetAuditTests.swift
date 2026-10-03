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

    private let areaId = "south-mountain-park-and-preserve-az"
    private let areaRowId = "area-progress-south-mountain-park-and-preserve-az"

    /// Layout anchors per state tag, so the test can ASSERT the layout rather
    /// than only photograph it. Filled by `logFrames`. The search field is no
    /// longer the anchor — it exists ONLY at the browse stop now — so the fit
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

        // Photograph the Home card layouts before leaving Explore. This runs
        // in every device/content-size matrix entry, so the accessibility-size
        // AreaCard and ContinueCard branches are actual visual gates rather
        // than source-only assertions.
        auditExploreCards(app)

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
        assertFitAreaPresentation(app)

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
        let firstRowIdentifier = tapFirstTrailRow(app)
        settle(3)
        assertSelectedTrailPresentation(app, rowIdentifier: firstRowIdentifier)
        assertSelectedMapFraming(app)
        capture(app, "sheet-05-min-trail-selected")
        logFrames(app, "min-trail-selected")

        // ---- 5. Deselect: the toolbar and rows must return to idle --------
        if let identifier = firstRowIdentifier {
            let select = app.descendants(matching: .any)[identifier].firstMatch
            let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
            XCTAssertTrue(scrollToReachable(select, in: trailScroll, app: app))
            tapElement(select)
            settle(3)
        }
        capture(app, "sheet-06-min-trail-deselected")
        logFrames(app, "min-trail-deselected")

        // The fit stop must NOT render the search field — its absence is what
        // makes the keyboard unable to yank the sheet taller.
        XCTAssertFalse(
            app.textFields["Search trails"].firstMatch.exists,
            "The search field rendered at the fit stop; it must exist only at browse"
        )
        XCTAssertEqual(app.buttons.matching(identifier: "area-search-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "trail-filter-button").count, 0)

        // ---- 6. Drag up to the browse stop (the only other stop) ----------
        dragSheet(app, toBottom: false)
        settle(3)
        capture(app, "sheet-07-browse-after-deselect")
        logFrames(app, "browse-after-deselect")

        // At browse the search-and-filter chrome must be present.
        XCTAssertTrue(
            app.textFields["Search trails"].firstMatch.waitForExistence(timeout: 10),
            "The search field is missing at the browse stop, where the chrome lives"
        )
        XCTAssertEqual(app.textFields.matching(identifier: "Search trails").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "trail-filter-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-search-button").count, 0)

        // The map must stay visible even at the tallest stop: the area name
        // heads the sheet, so its top edge is the sheet's top edge, and it
        // must sit well below the top of the screen.
        let nameAtBrowse = app.staticTexts["South Mountain Park and Preserve"].firstMatch
        if nameAtBrowse.exists {
            let mapFraction = nameAtBrowse.frame.minY / app.frame.height
            XCTAssertGreaterThan(
                mapFraction, 0.25,
                "Browse stop covers the map: sheet top at \(Int(mapFraction * 100))% of screen height"
            )
        } else {
            dumpTree(app, "area-name-missing-at-browse")
            XCTFail("Area name not found at the browse stop; cannot verify the map stays visible")
        }

        // ---- 6b. Select from browse: the sheet hands off to the map -------
        // Tapping a trail is a question about WHERE it is, so selecting from
        // the tall stop drops the sheet to fit and the trail is framed above.
        let browseRowIdentifier = tapFirstTrailRow(app)
        settle(3)
        assertSelectedMapFraming(app)
        capture(app, "sheet-07b-selected-from-browse")
        logFrames(app, "selected-from-browse", extraRowIdentifier: browseRowIdentifier)
        if let identifier = browseRowIdentifier {
            if let atBrowse = toolbarY["browse-after-deselect"],
               let afterSelect = toolbarY["selected-from-browse"] {
                // Larger minY = lower on screen = the sheet dropped. The design
                // guarantees at least a 40pt step between the stops; allow a
                // couple of points for measurement so an exact 40 passes.
                XCTAssertGreaterThanOrEqual(
                    afterSelect - atBrowse, 38,
                    "Selecting from browse did not drop the sheet toward the fit stop "
                    + "(browse y=\(Int(atBrowse)), selected y=\(Int(afterSelect)))"
                )
            } else {
                XCTFail("Missing toolbar measurements for the select-from-browse handoff: \(toolbarY.keys.sorted())")
            }
            // Deselect returns to browse, since the drop was for the selection.
            tapElement(app.buttons[identifier].firstMatch)
            settle(3)
            capture(app, "sheet-07c-deselected-back-to-browse")
            logFrames(app, "deselected-back-to-browse")
        } else {
            XCTFail("No trail row found at the browse stop to select")
        }

        // ---- 7. Back to min, open the Collection from its explicit button --
        // The horizontal pager is gone; the Collection is a labeled action in
        // the sheet toolbar, presented as its own nested sheet.
        dragSheet(app, toBottom: true)
        settle(2)
        openCollection(app)
        settle(3)
        capture(app, "sheet-08-collection-open")
        logFrames(app, "collection-open")

        let collectionScroll = app.scrollViews["collection-scroll"].firstMatch
        XCTAssertTrue(collectionScroll.waitForExistence(timeout: 10), "Collection scroll is missing")
        let finalCollectionContent = app.descendants(matching: .any)[
            "collection-dedication-final"
        ].firstMatch
        XCTAssertTrue(
            scrollToReachable(finalCollectionContent, in: collectionScroll, app: app),
            "Collection final content is not reachable"
        )
        logElementFrame(app, finalCollectionContent, tag: "collection-lower-content")
        capture(app, "sheet-08b-collection-lower-content")

        // ---- 8. Close the Collection: the sheet underneath must be exactly
        // the idle min-stop layout it was before the presentation.
        closeCollection(app)
        settle(3)
        capture(app, "sheet-09-collection-closed")
        logFrames(app, "collection-closed")

        assertLayoutInvariants()
    }

    func testAuditRecoveredGpsAndGapSummary() {
        let app = launchRecordingAudit(arguments: ["--uitest-recording-gap"])
        guard openRecordingArea(app) else { return }

        let status = app.descendants(matching: .any)["recording-gps-status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 10), "Recovered GPS status is missing")
        let recoveredStatusIsExpected = status.label == "GPS recovered"
        print("AUDIT[gps-recovered] expectedState=recovered matched=\(recoveredStatusIsExpected)")
        XCTAssertTrue(recoveredStatusIsExpected, "Recovered GPS status has unexpected copy")
        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        if dashboardScroll.exists {
            XCTAssertTrue(
                scrollToReachable(status, in: dashboardScroll, app: app),
                "Recovered GPS status is not reachable"
            )
        }
        logElementFrame(app, status, tag: "gps-recovered")
        XCTAssertEqual(stopControlCount(app), 1, "Recovered state must expose exactly one Stop control")
        XCTAssertEqual(app.buttons.matching(identifier: "recording-stop-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "active-recording-stop-button").count, 0)
        capture(app, "field-trust-01-gps-recovered")

        let stop = app.buttons["recording-stop-button"].firstMatch
        guard stop.waitForExistence(timeout: 10) else {
            dumpTree(app, "recording-stop-missing")
            XCTFail("Recording stop control is missing")
            return
        }
        if dashboardScroll.exists {
            XCTAssertTrue(
                scrollToReachable(stop, in: dashboardScroll, app: app),
                "Recording stop control is not reachable"
            )
        }
        tapElement(stop)
        let save = app.buttons["Stop & Save"].firstMatch
        guard save.waitForExistence(timeout: 10) else {
            dumpTree(app, "stop-save-dialog-missing")
            XCTFail("Stop & Save action is missing")
            return
        }
        tapElement(save)

        let gapCopy = app.descendants(matching: .any)["recording-gap-summary"].firstMatch
        XCTAssertTrue(gapCopy.waitForExistence(timeout: 60), "Saved hike gap explanation is missing")
        logElementFrame(app, gapCopy, tag: "gap-summary")
        capture(app, "field-trust-02-gap-summary")
        let done = app.buttons["recording-summary-done"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 10), "Summary Done action is missing")
        XCTAssertEqual(app.buttons.matching(identifier: "recording-summary-done").count, 1)
        XCTAssertEqual(
            app.buttons.matching(NSPredicate(format: "label == %@", "Done")).count,
            1,
            "Recording summary must expose exactly one Done action"
        )
        logElementFrame(app, done, tag: "summary-done")

        let summaryScroll = app.scrollViews["recording-summary-scroll"].firstMatch
        XCTAssertTrue(summaryScroll.waitForExistence(timeout: 10), "Summary scroll is missing")
        let lowerContent = app.descendants(matching: .any)["recording-summary-area-progress"].firstMatch
        XCTAssertTrue(
            scrollToReachable(lowerContent, in: summaryScroll, app: app),
            "Summary lower content is not reachable"
        )
        logElementFrame(app, lowerContent, tag: "summary-lower-content")
        capture(app, "field-trust-02b-summary-lower-content")
    }

    func testAuditPausedGpsState() {
        let app = launchRecordingAudit(arguments: ["--uitest-recording-paused"])
        guard openRecordingArea(app) else { return }

        let status = app.descendants(matching: .any)["recording-gps-status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 10), "Paused GPS status is missing")
        let pausedStatusIsExpected = status.label == "GPS paused—route stays safe"
        print("AUDIT[gps-paused] expectedState=paused matched=\(pausedStatusIsExpected)")
        XCTAssertTrue(pausedStatusIsExpected, "Paused GPS status has unexpected copy")
        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        if dashboardScroll.exists {
            XCTAssertTrue(
                scrollToReachable(status, in: dashboardScroll, app: app),
                "Paused GPS status is not reachable"
            )
        }
        logElementFrame(app, status, tag: "gps-paused")
        XCTAssertEqual(stopControlCount(app), 1, "Paused state must expose exactly one Stop control")
        XCTAssertEqual(app.buttons.matching(identifier: "recording-stop-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "active-recording-stop-button").count, 0)
        capture(app, "field-trust-03-gps-paused")
    }

    private func auditExploreCards(_ app: XCUIApplication) {
        let safeFrame = exploreVisibleContentFrame(app)
        print(
            "AUDIT[explore-viewport] x=\(Int(safeFrame.minX)) y=\(Int(safeFrame.minY)) "
            + "w=\(Int(safeFrame.width)) h=\(Int(safeFrame.height))"
        )

        let continueButton = app.buttons["continue-card"].firstMatch
        guard continueButton.waitForExistence(timeout: 30) else {
            dumpTree(app, "continue-card-missing")
            XCTFail("Continue card is missing from the seeded Explore screen")
            return
        }
        XCTAssertTrue(
            scrollIntoExploreViewport(continueButton, app: app),
            "Continue card did not settle inside the Explore viewport"
        )
        assertInsideFrame(continueButton, frame: safeFrame, tag: "explore-continue-card")
        assertCompleteAreaTitle(
            app.descendants(matching: .any)["continue-card-title"].firstMatch,
            app: app,
            tag: "explore-continue-title"
        )
        capture(app, "field-trust-00-explore-continue-card")

        let open = app.buttons["area-open-\(areaId)"].firstMatch
        guard scrollIntoExploreViewport(open, app: app) else {
            dumpTree(app, "area-card-actions-not-visible")
            XCTFail("Area card did not settle inside the Explore viewport")
            return
        }
        let save = app.buttons["area-save-\(areaId)"].firstMatch
        guard open.exists, save.exists else {
            dumpTree(app, "area-card-actions-missing")
            XCTFail("Distinct Area card actions are missing")
            return
        }
        let areaActionIdentifiersAreDistinct = open.identifier != save.identifier
        XCTAssertTrue(areaActionIdentifiersAreDistinct, "Area actions must have distinct identifiers")
        let areaActionLabelsAreDistinct = open.label != save.label
        XCTAssertTrue(areaActionLabelsAreDistinct, "Area actions must have distinct labels")
        assertInsideFrame(open, frame: safeFrame, tag: "explore-area-card-open")
        assertInsideFrame(save, frame: safeFrame, tag: "explore-area-card-save")
        assertCompleteAreaTitle(
            app.descendants(matching: .any)["area-card-title-\(areaId)"].firstMatch,
            app: app,
            tag: "explore-area-card-title"
        )
        capture(app, "field-trust-00-explore-area-card")

        let savedHeading = app.staticTexts["Saved Areas"].firstMatch
        XCTAssertTrue(
            scrollIntoExploreViewport(savedHeading, app: app),
            "Saved Areas did not remain reachable by scrolling down"
        )
        XCTAssertTrue(
            scrollIntoExploreViewport(continueButton, app: app),
            "Continue card did not remain reachable by scrolling up"
        )
        XCTAssertTrue(app.buttons["All Areas Map"].firstMatch.isHittable)
        XCTAssertTrue(app.tabBars.buttons["Explore"].firstMatch.isHittable)
        XCTAssertTrue(app.tabBars.buttons["Browse"].firstMatch.isHittable)
        XCTAssertTrue(app.tabBars.buttons["Stats"].firstMatch.isHittable)
        XCTAssertTrue(app.tabBars.buttons["Settings"].firstMatch.isHittable)
    }

    private func assertFitAreaPresentation(_ app: XCUIApplication) {
        let header = app.descendants(matching: .any)["area-header"].firstMatch
        let title = app.descendants(matching: .any)["area-header-title"].firstMatch
        let actions = app.descendants(matching: .any)["area-action-group"].firstMatch
        XCTAssertTrue(header.exists, "Area header is missing")
        XCTAssertTrue(title.exists, "Area title is missing")
        XCTAssertTrue(actions.exists, "Area action group is missing")
        logElementFrame(app, header, tag: "area-header")
        logElementFrame(app, title, tag: "area-title")
        logElementFrame(app, actions, tag: "area-actions")
        XCTAssertEqual(app.buttons.matching(identifier: "area-record-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-search-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-collection-button").count, 1)
    }

    private func assertSelectedTrailPresentation(
        _ app: XCUIApplication,
        rowIdentifier: String?
    ) {
        guard let rowIdentifier else {
            XCTFail("Selected trail row identifier is missing")
            return
        }
        let suffix = String(rowIdentifier.dropFirst("trail-select-".count))
        let select = app.buttons[rowIdentifier].firstMatch
        let secondary = app.buttons["trail-secondary-\(suffix)"].firstMatch
        let profile = app.descendants(matching: .any)["trail-profile-\(suffix)"].firstMatch
        XCTAssertTrue(select.exists, "Selected trail action is missing")
        XCTAssertTrue(secondary.exists, "Selected trail secondary action is missing")
        XCTAssertTrue(profile.exists, "Selected trail profile is missing")
        let actionIdentifiersAreDistinct = select.identifier != secondary.identifier
        let actionLabelsAreDistinct = select.label != secondary.label
        XCTAssertTrue(actionIdentifiersAreDistinct, "Selected trail actions must be distinct")
        XCTAssertTrue(actionLabelsAreDistinct, "Selected trail action labels must be distinct")
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.exists, "Trail list scroll is missing")
        XCTAssertTrue(
            scrollToReachable(select, in: trailScroll, app: app),
            "Selected trail action is not reachable"
        )
        logElementFrame(app, select, tag: "trail-select")
        XCTAssertTrue(
            scrollToReachable(secondary, in: trailScroll, app: app),
            "Selected trail secondary action is not reachable"
        )
        logElementFrame(app, secondary, tag: "trail-secondary")
        XCTAssertTrue(
            scrollToReachable(profile, in: trailScroll, app: app),
            "Selected trail profile is not reachable"
        )
        logElementFrame(app, profile, tag: "trail-profile")
    }

    /// Selected annotations expose only generic marker identifiers. Their
    /// frames must remain entirely between the measured top controls and the
    /// native sheet. Diagnostics intentionally report counts/booleans only.
    private func assertSelectedMapFraming(_ app: XCUIApplication) {
        let controls = app.descendants(matching: .any)["area-map-controls"].firstMatch
        let sheetHeader = app.descendants(matching: .any)["area-header"].firstMatch
        XCTAssertTrue(controls.waitForExistence(timeout: 10), "Map controls are missing")
        XCTAssertTrue(sheetHeader.waitForExistence(timeout: 10), "Area sheet header is missing")
        guard controls.exists, sheetHeader.exists else { return }

        let screen = app.frame
        let safeTop = controls.frame.maxY
        let safeBottom = sheetHeader.frame.minY
        let safeFrame = CGRect(
            x: screen.minX + 20,
            y: safeTop,
            width: max(0, screen.width - 40),
            height: max(0, safeBottom - safeTop)
        )
        let markers = app.descendants(matching: .any).matching(NSPredicate(
            format: "identifier == %@ OR identifier == %@",
            "map-parking-marker",
            "map-trailhead-marker"
        )).allElementsBoundByIndex
        let presentMarkers = markers.filter { $0.exists }
        print(
            "AUDIT[selected-map] markerCount=\(presentMarkers.count) "
            + "safeW=\(Int(safeFrame.width)) safeH=\(Int(safeFrame.height))"
        )
        XCTAssertGreaterThan(presentMarkers.count, 0, "Selected map has no generic access marker")
        for marker in presentMarkers {
            let isContained = safeFrame.contains(marker.frame)
            let clearsControls = !marker.frame.intersects(controls.frame)
            let clearsSheet = !marker.frame.intersects(sheetHeader.frame)
            print(
                "AUDIT[selected-marker] contained=\(isContained) "
                + "clearsControls=\(clearsControls) clearsSheet=\(clearsSheet)"
            )
            XCTAssertTrue(isContained, "Selected marker is outside the safe map frame")
            XCTAssertTrue(clearsControls, "Selected marker intersects top map controls")
            XCTAssertTrue(clearsSheet, "Selected marker intersects the area sheet")
        }
    }

    private func stopControlCount(_ app: XCUIApplication) -> Int {
        app.buttons.matching(NSPredicate(
            format: "identifier == %@ OR identifier == %@ OR identifier == %@",
            "active-recording-stop-button",
            "recording-stop-button",
            "walk-stop-button"
        )).count
    }

    private func scrollToReachable(
        _ element: XCUIElement,
        in scrollView: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        for attempt in 0...10 {
            if isOnScreenAndHittable(element, app: app) { return true }
            if attempt < 10 {
                if element.exists, element.frame.maxY < app.frame.minY {
                    scrollView.swipeDown()
                } else {
                    scrollView.swipeUp()
                }
                settle(1)
            }
        }
        return false
    }

    private func isOnScreenAndHittable(_ element: XCUIElement, app: XCUIApplication) -> Bool {
        guard element.exists, element.isHittable else { return false }
        let frame = element.frame
        let screen = app.frame
        return frame.minX >= screen.minX - 1
            && frame.maxX <= screen.maxX + 1
            && frame.minY >= screen.minY - 1
            && frame.maxY <= screen.maxY + 1
    }

    private func exploreVisibleContentFrame(_ app: XCUIApplication) -> CGRect {
        let navigationBar = app.navigationBars.firstMatch
        let tabBar = app.tabBars.firstMatch
        _ = navigationBar.waitForExistence(timeout: 10)
        _ = tabBar.waitForExistence(timeout: 10)

        let screen = app.frame
        let top = navigationBar.exists ? navigationBar.frame.maxY : screen.minY
        let bottom = tabBar.exists ? tabBar.frame.minY : screen.maxY
        return CGRect(
            x: screen.minX,
            y: top,
            width: screen.width,
            height: max(0, bottom - top)
        ).insetBy(dx: 1, dy: 4)
    }

    private func scrollIntoExploreViewport(
        _ element: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        let safeFrame = exploreVisibleContentFrame(app)
        let scrollView = app.scrollViews["explore-scroll"].firstMatch
        for attempt in 0...20 {
            if element.exists,
               element.isHittable,
               safeFrame.contains(element.frame) {
                return true
            }
            if attempt < 20 {
                nudgeExploreScroll(
                    scrollView.exists ? scrollView : app,
                    towardTop: element.exists && element.frame.minY < safeFrame.minY
                )
                settle(1)
            }
        }
        return false
    }

    private func nudgeExploreScroll(_ surface: XCUIElement, towardTop: Bool) {
        let start = surface.coordinate(
            withNormalizedOffset: CGVector(dx: 0.5, dy: towardTop ? 0.35 : 0.65)
        )
        let end = surface.coordinate(
            withNormalizedOffset: CGVector(dx: 0.5, dy: towardTop ? 0.55 : 0.45)
        )
        start.press(forDuration: 0.05, thenDragTo: end)
    }

    private func assertInsideFrame(
        _ element: XCUIElement,
        frame: CGRect,
        tag: String
    ) {
        let isContained = element.exists
            && element.isHittable
            && frame.contains(element.frame)
        print("AUDIT[\(tag)] contained=\(isContained)")
        XCTAssertTrue(isContained, "Explore control is outside the visible viewport")
    }

    private func assertCompleteAreaTitle(
        _ title: XCUIElement,
        app: XCUIApplication,
        tag: String
    ) {
        let exists = title.waitForExistence(timeout: 10)
        let isComplete = exists && title.label == "South Mountain Park and Preserve"
        let isAccessibility = title.frame.height > 80
            || app.launchArguments.contains("UICTContentSizeCategoryAccessibilityXXXL")
        let isWithinStandardLineLimit = isAccessibility || (exists && title.frame.height <= 52)
        print(
            "AUDIT[\(tag)] complete=\(isComplete) "
            + "standardLineBound=\(isWithinStandardLineLimit)"
        )
        XCTAssertTrue(isComplete, "Area title is incomplete")
        XCTAssertTrue(isWithinStandardLineLimit, "Area title exceeds two standard lines")
    }

    private func launchRecordingAudit(arguments: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"] + arguments
        app.launch()
        settle(25)
        return app
    }

    private func openRecordingArea(_ app: XCUIApplication) -> Bool {
        openStatsTab(app)
        _ = app.staticTexts["Recent Hikes"].firstMatch.waitForExistence(timeout: 60)
        guard openAreaFromStats(app) else {
            capture(app, "field-trust-area-never-opened")
            XCTFail("Recording area never appeared")
            return false
        }
        settle(8)
        return true
    }

    private func logElementFrame(_ app: XCUIApplication, _ element: XCUIElement, tag: String) {
        guard element.exists else {
            print("AUDIT[\(tag)] element: MISSING")
            return
        }
        let frame = element.frame
        let screen = app.frame
        print("AUDIT[\(tag)] element: x=\(Int(frame.minX)) y=\(Int(frame.minY)) "
              + "w=\(Int(frame.width)) h=\(Int(frame.height)) maxY=\(Int(frame.maxY)) "
              + "screenW=\(Int(screen.width)) screenH=\(Int(screen.height))")
        XCTAssertGreaterThanOrEqual(frame.minX, screen.minX - 1)
        XCTAssertLessThanOrEqual(frame.maxX, screen.maxX + 1)
        XCTAssertGreaterThanOrEqual(frame.minY, screen.minY - 1)
        XCTAssertLessThanOrEqual(frame.maxY, screen.maxY + 1)
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

        // 3. Deselecting after a select-from-browse must return the sheet to
        //    the browse stop — the drop was for the selection, and undoing it
        //    must not cost the user their place in the list.
        if let browse = toolbarY["browse-after-deselect"],
           let back = toolbarY["deselected-back-to-browse"] {
            XCTAssertEqual(
                back, browse, accuracy: 2,
                "Deselect did not return the sheet to browse (browse y=\(Int(browse)), after y=\(Int(back)))"
            )
        } else {
            XCTFail("Missing toolbar measurements for deselect-returns-to-browse: \(toolbarY.keys.sorted())")
        }

        // 4. Dismissing the Collection must return the sheet to exactly the
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
        // The sheet has exactly two stops (fit + browse), so the upward drag
        // must release well above the fit stop's top edge or UIKit snaps
        // back to fit instead of advancing to browse. Releasing above the
        // browse stop's top edge snaps to browse (the topmost stop).
        let targetY = toBottom ? app.frame.height - 8 : app.frame.height * 0.2
        let to = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: targetY))
        from.press(forDuration: 0.1, thenDragTo: to)
    }

    /// Scroll the trail list itself: a short vertical drag INSIDE the row
    /// region, well below the toolbar so it hits scroll content. (The search
    /// field lives only at the browse stop now, so the always-present Start
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

    /// Tap the first visible trail's semantic Select button and return its
    /// stable identifier so callers can address the same row after its label
    /// changes to Deselect. This avoids guessing from localized/static text.
    private func tapFirstTrailRow(_ app: XCUIApplication) -> String? {
        let start = app.buttons["area-record-button"].firstMatch
        guard start.exists else {
            dumpTree(app, "no-toolbar-before-row-tap")
            return nil
        }
        let rowBandTop = start.frame.maxY + 4
        let candidates = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "trail-select-")
        ).allElementsBoundByIndex
        let row = candidates
            .filter { $0.exists && $0.frame.minY > rowBandTop && $0.frame.minY < app.frame.maxY }
            .min { $0.frame.minY < $1.frame.minY }
        guard let row else {
            dumpTree(app, "no-trail-row-found")
            return nil
        }
        print("AUDIT tapping first trail row")
        tapElement(row)
        return row.identifier
    }

    // MARK: - Frame logging

    /// Print the frames that decide whether this layout is clipped. The
    /// screen height is printed alongside so `maxY > screen` is readable
    /// straight off the CI log.
    private func logFrames(_ app: XCUIApplication, _ tag: String, extraRowIdentifier: String? = nil) {
        let screen = app.frame
        func line(_ label: String, _ e: XCUIElement) {
            guard e.exists else { print("AUDIT[\(tag)] \(label): MISSING"); return }
            let f = e.frame
            print("AUDIT[\(tag)] \(label): x=\(Int(f.minX)) y=\(Int(f.minY)) w=\(Int(f.width)) h=\(Int(f.height)) maxY=\(Int(f.maxY)) screenH=\(Int(screen.height))")
            XCTAssertGreaterThanOrEqual(f.minX, screen.minX - 1, "\(label) clips past the leading screen edge")
            XCTAssertLessThanOrEqual(f.maxX, screen.maxX + 1, "\(label) clips past the trailing screen edge")
            XCTAssertGreaterThanOrEqual(f.minY, screen.minY - 1, "\(label) clips past the top screen edge")
            XCTAssertLessThanOrEqual(f.maxY, screen.maxY + 1, "\(label) clips past the bottom screen edge")
        }
        print("AUDIT[configuration] width=\(Int(screen.width)) height=\(Int(screen.height))")
        print("AUDIT[\(tag)] ---- frames ----")
        line("area-name", app.staticTexts["South Mountain Park and Preserve"].firstMatch)
        line("start-button", app.buttons["area-record-button"].firstMatch)
        line("search-field", app.textFields["Search trails"].firstMatch)
        let start = app.buttons["area-record-button"].firstMatch
        if start.exists { toolbarY[tag] = start.frame.minY }
        if let identifier = extraRowIdentifier {
            line("selected-row", app.buttons[identifier].firstMatch)
        }
        // The first few trail-title-looking texts, to see row boundaries —
        // banded below the toolbar (fit) or the search field (browse).
        let search = app.textFields["Search trails"].firstMatch
        let bandTop = search.exists ? search.frame.maxY
            : (start.exists ? start.frame.maxY : screen.height * 0.6)
        var printed = 0
        var firstTitleY: CGFloat? = nil
        for t in app.staticTexts.allElementsBoundByIndex {
            let f = t.frame
            guard f.minY > bandTop, f.height >= 18 else { continue }
            print("AUDIT[\(tag)] text-index=\(printed): y=\(Int(f.minY)) maxY=\(Int(f.maxY))")
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
        // sheet context; the search field exists only at the browse stop now.
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
        let elementCount = app.descendants(matching: .any).count
        print("AUDIT[\(tag)] hierarchy unavailable; elementCount=\(elementCount)")
    }
}
