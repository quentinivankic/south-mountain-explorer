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
    private let deselectTrailLabelPrefix = "Deselect Trail,"

    private struct TrailSelection {
        let selectIdentifier: String
        let secondaryIdentifier: String
        let profileIdentifier: String
        let subject: String
        let idleSecondaryLabel: String
    }

    private struct ScrollVisibilityResult {
        let isVisible: Bool
        let didScroll: Bool
    }

    private enum KnownTargetPosition: Equatable {
        case earlier
        case later
    }

    private enum ParkingLabelExpectation: Equatable {
        case nearTrailhead
        case farDistance
    }

    private enum ParkingActivation: Equatable {
        case normal
        case promoted
    }

    private enum ParkingStallDecision: Equatable {
        case continueNormally
        case promote
        case fail
    }

    private enum PostDeselectRecovery: Equatable {
        case macroLater
        case microLater
        case microEarlier
        case wait
        case done
    }

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
        addUIInterruptionMonitor(withDescription: "Unexpected system alert") { _ in
            XCTFail("AUDIT[unexpected-system-alert]")
            return true
        }
    }

    func testAuditAreaSheetStates() {
        assertNavigationRegressionControls()
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"]
        app.launch()
        guard assertAuditedAppEnvironment(app) else { return }

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
        guard let firstSelection = tapFirstTrailRow(app) else { return }
        settle(3)
        assertSelectedTrailPresentation(
            app,
            rowIdentifier: firstSelection.selectIdentifier
        )
        assertSelectedMapFraming(app)
        capture(app, "sheet-05-min-trail-selected")
        logFrames(app, "min-trail-selected")

        // ---- 5. Deselect: the toolbar and rows must return to idle --------
        guard deselectTrailAndWait(app, selection: firstSelection) else { return }
        dragSheet(app, toBottom: true)
        settle(2)
        let deselectedTrailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard deselectedTrailScroll.waitForExistence(timeout: 10),
              waitForDeselectedTrailState(
                app,
                selection: firstSelection,
                in: deselectedTrailScroll
              ) else {
            return
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

        // ---- 6. Enter the browse stop through the explicit Search action ----
        // A header drag can scroll AX header content instead of moving the
        // native sheet. Search is the product-supported transition and its
        // field/filter chrome is a fail-closed proof that Browse really landed.
        guard enterBrowseUsingSearch(app) else { return }
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
        let headerAtBrowse = app.descendants(matching: .any)["area-header"].firstMatch
        if nameAtBrowse.exists, headerAtBrowse.exists {
            let mapFraction = headerAtBrowse.frame.minY / app.frame.height
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
        guard let browseSelection = tapFirstTrailRow(app) else { return }
        guard waitForFitChrome(app) else { return }
        settle(3)
        assertTrailActionFrames(
            app,
            rowIdentifier: browseSelection.selectIdentifier,
            selectLabelPrefix: "Deselect Trail,",
            secondaryLabelPrefix: "Record Trail,",
            tag: "trail-actions-browse-record"
        )
        assertSelectedProfileLayout(
            app,
            rowIdentifier: browseSelection.selectIdentifier
        )
        let selectedFromBrowsePixels = capture(app, "sheet-07b-selected-from-browse")
        logFrames(app, "selected-from-browse")
        guard let parkingExpectation = assertSelectedMapFraming(
            app,
            provesFarDisclosure: false
        ),
              let lowerContentDidScroll = scrollSelectedProfileLowerContent(
                app,
                parkingExpectation: parkingExpectation
              ) else {
            return
        }
        let lowerContentPixels = capture(app, "sheet-07d-selected-profile-lower-content")
        if lowerContentDidScroll {
            let lowerContentIsPixelDistinct = lowerContentPixels
                != selectedFromBrowsePixels
            XCTAssertTrue(
                lowerContentIsPixelDistinct,
                "Lower-content capture must be pixel-distinct after scrolling"
            )
        }

        if !isAccessibilityLayout(app) {
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
        }

        // Deselect returns to Browse, since the drop was for the selection.
        guard deselectTrailAndWait(app, selection: browseSelection) else { return }
        guard waitForBrowseChrome(app, requiresKeyboard: false) else { return }
        settle(2)
        capture(app, "sheet-07c-deselected-back-to-browse")
        logFrames(app, "deselected-back-to-browse")

        // ---- 7. Back to min, open the Collection from its explicit button --
        // The horizontal pager is gone; the Collection is a labeled action in
        // the sheet toolbar, presented as its own nested sheet.
        guard moveToFitUsingGrabber(app) else { return }
        let collectionUsesFullWidthRows = isAccessibilityLayout(app)
        openCollection(app)
        settle(3)
        capture(app, "sheet-08-collection-open")
        logFrames(app, "collection-open")

        let collectionScroll = app.scrollViews["collection-scroll"].firstMatch
        XCTAssertTrue(collectionScroll.waitForExistence(timeout: 10), "Collection scroll is missing")

        let milestone = app.descendants(matching: .any)[
            "collection-milestone-representative"
        ].firstMatch
        assertCollectionRepresentative(
            milestone,
            in: collectionScroll,
            app: app,
            expectedWholeWord: "Completionist",
            requiresFullWidth: collectionUsesFullWidthRows,
            tag: "collection-completionist"
        )
        capture(app, "sheet-08b-collection-completionist")

        let difficulty = app.descendants(matching: .any)[
            "collection-difficulty-representative"
        ].firstMatch
        assertCollectionRepresentative(
            difficulty,
            in: collectionScroll,
            app: app,
            expectedWholeWord: "Easygoer",
            requiresFullWidth: collectionUsesFullWidthRows,
            tag: "collection-difficulty"
        )
        capture(app, "sheet-08c-collection-difficulty")

        let finalCollectionContent = app.descendants(matching: .any)[
            "collection-dedication-final"
        ].firstMatch
        XCTAssertTrue(
            scrollToReachable(finalCollectionContent, in: collectionScroll, app: app),
            "Collection final content is not reachable"
        )
        logElementFrame(app, finalCollectionContent, tag: "collection-lower-content")
        XCTAssertGreaterThanOrEqual(
            finalCollectionContent.frame.height,
            44,
            "Collection final row is smaller than a reachable control"
        )
        if collectionUsesFullWidthRows {
            XCTAssertGreaterThan(
                finalCollectionContent.frame.width,
                app.frame.width * 0.7,
                "Accessibility Collection final row is not full width"
            )
        }
        // Pixel proof must follow the final bounded scroll, not precede it.
        capture(app, "sheet-08d-collection-dedication")

        // ---- 8. Close the Collection: the sheet underneath must be exactly
        // the idle min-stop layout it was before the presentation.
        closeCollection(app)
        settle(3)
        capture(app, "sheet-09-collection-closed")
        logFrames(app, "collection-closed")

        assertLayoutInvariants(app)
    }

    func testAuditRecoveredGpsAndGapSummary() {
        assertNavigationRegressionControls()
        guard let app = launchRecordingAudit(arguments: ["--uitest-recording-gap"]) else {
            return
        }
        guard openRecordingArea(app) else { return }

        let status = app.descendants(matching: .any)["recording-gps-status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 10), "Recovered GPS status is missing")
        let recoveredStatusIsExpected = status.label == "GPS recovered"
        print("AUDIT[gps-recovered] expectedState=recovered matched=\(recoveredStatusIsExpected)")
        XCTAssertTrue(recoveredStatusIsExpected, "Recovered GPS status has unexpected copy")
        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        if dashboardScroll.exists {
            if dashboardScroll.frame.intersection(app.frame).isEmpty {
                dragSheet(app, toBottom: false)
                settle(3)
            }
            XCTAssertTrue(
                scrollToReachable(status, in: dashboardScroll, app: app),
                "Recovered GPS status is not reachable"
            )
        }
        logElementFrame(app, status, tag: "gps-recovered")
        assertRecordingAreaHeader(app)
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
            scrollToVisible(
                identifier: "recording-summary-area-progress",
                in: summaryScroll,
                app: app
            ),
            "Complete Summary Area Progress card is not reachable"
        )
        let wholeCardIsInside = app.frame.insetBy(dx: -1, dy: -1).contains(lowerContent.frame)
        let wholeCardIsWide = lowerContent.frame.width > app.frame.width * 0.7
        let progressTitle = app.staticTexts["Area Progress"].firstMatch
        let progressValue = app.descendants(matching: .any)[
            "recording-summary-area-progress-value"
        ].firstMatch
        let progressBar = app.descendants(matching: .any)[
            "recording-summary-area-progress-bar"
        ].firstMatch
        let cardBounds = lowerContent.frame.insetBy(dx: -1, dy: -1)
        let wholeCardHasContent = progressTitle.exists
            && progressValue.exists
            && progressBar.exists
            && cardBounds.contains(progressTitle.frame)
            && cardBounds.contains(progressValue.frame)
            && cardBounds.contains(progressBar.frame)
        print(
            "AUDIT[summary-lower-content] inside=\(wholeCardIsInside) "
            + "wide=\(wholeCardIsWide) complete=\(wholeCardHasContent)"
        )
        XCTAssertTrue(wholeCardIsInside, "Summary Area Progress card is clipped")
        XCTAssertLessThanOrEqual(
            lowerContent.frame.maxY,
            app.frame.maxY + 1,
            "Summary Area Progress card extends below the physical screen"
        )
        XCTAssertTrue(wholeCardIsWide, "Summary Area Progress card is not full width")
        XCTAssertTrue(wholeCardHasContent, "Summary Area Progress card semantics are incomplete")
        XCTAssertFalse(app.staticTexts["New Completions"].firstMatch.exists)
        XCTAssertFalse(app.staticTexts["Previously Completed"].firstMatch.exists)
        XCTAssertFalse(app.staticTexts["Made Progress"].firstMatch.exists)
        logElementFrame(app, lowerContent, tag: "summary-lower-content")
        capture(app, "field-trust-02b-summary-lower-content")
    }

    func testAuditPausedGpsState() {
        guard let app = launchRecordingAudit(arguments: ["--uitest-recording-paused"]) else {
            return
        }
        guard openRecordingArea(app) else { return }

        let status = app.descendants(matching: .any)["recording-gps-status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 10), "Paused GPS status is missing")
        let pausedStatusIsExpected = status.label == "GPS paused—route stays safe"
        print("AUDIT[gps-paused] expectedState=paused matched=\(pausedStatusIsExpected)")
        XCTAssertTrue(pausedStatusIsExpected, "Paused GPS status has unexpected copy")
        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        if dashboardScroll.exists {
            if dashboardScroll.frame.intersection(app.frame).isEmpty {
                dragSheet(app, toBottom: false)
                settle(3)
            }
            XCTAssertTrue(
                scrollToReachable(status, in: dashboardScroll, app: app),
                "Paused GPS status is not reachable"
            )
        }
        logElementFrame(app, status, tag: "gps-paused")
        assertRecordingAreaHeader(app)
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

        auditExploreLocationEmptyState(app)

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
        let areaTitle = app.descendants(matching: .any)[
            "area-card-title-\(areaId)"
        ].firstMatch
        assertCompleteAreaTitle(
            areaTitle,
            app: app,
            tag: "explore-area-card-title"
        )
        assertAreaCardTitleClearance(
            areaTitle,
            open: open,
            save: save,
            visibleFrame: safeFrame
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

    private func auditExploreLocationEmptyState(_ app: XCUIApplication) {
        let state = app.descendants(matching: .any)[
            "explore-location-empty-state"
        ].firstMatch
        let title = app.descendants(matching: .any)[
            "explore-location-empty-title"
        ].firstMatch
        let detail = app.descendants(matching: .any)[
            "explore-location-empty-detail"
        ].firstMatch
        let primaryAction = app.buttons["explore-location-primary-action"].firstMatch
        let browseAction = app.buttons["explore-location-browse-action"].firstMatch
        XCTAssertTrue(
            state.waitForExistence(timeout: 10),
            "Location empty state is missing"
        )

        let elements: [(String, XCUIElement)] = [
            ("location-title", title),
            ("location-detail", detail),
            ("location-primary", primaryAction),
            ("location-browse", browseAction),
        ]
        let tabBar = app.tabBars.firstMatch
        for (tag, element) in elements {
            XCTAssertTrue(
                scrollIntoExploreViewport(element, app: app),
                "Location empty-state content is not reachable"
            )
            let visibleFrame = exploreVisibleContentFrame(app)
            assertInsideFrame(element, frame: visibleFrame, tag: "explore-\(tag)")
            if tabBar.exists {
                XCTAssertTrue(
                    element.frame.intersection(tabBar.frame).isEmpty,
                    "Location empty-state content intersects the tab bar"
                )
            }
        }

        let titleIsExpected = title.label == "Trails near you"
        XCTAssertTrue(titleIsExpected, "Location empty-state title is incomplete")
        XCTAssertTrue(
            settleExplorePair(detail, primaryAction, app: app),
            "Location detail and primary action cannot be shown together"
        )
        let settledFrame = exploreVisibleContentFrame(app)
        assertInsideFrame(detail, frame: settledFrame, tag: "explore-location-detail-settled")
        assertInsideFrame(
            primaryAction,
            frame: settledFrame,
            tag: "explore-location-primary-settled"
        )
        if tabBar.exists {
            XCTAssertTrue(
                detail.frame.intersection(tabBar.frame).isEmpty,
                "Location detail intersects the tab bar"
            )
            XCTAssertTrue(
                primaryAction.frame.intersection(tabBar.frame).isEmpty,
                "Location primary action intersects the tab bar"
            )
        }
        capture(app, "field-trust-00-explore-location-empty-state")
    }

    private func settleExplorePair(
        _ first: XCUIElement,
        _ second: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        let scrollView = app.scrollViews["explore-scroll"].firstMatch
        for attempt in 0...20 {
            let visibleFrame = exploreVisibleContentFrame(app)
            if first.exists,
               second.exists,
               first.isHittable,
               second.isHittable,
               visibleFrame.contains(first.frame),
               visibleFrame.contains(second.frame) {
                return true
            }
            if attempt < 20 {
                let contentIsAbove = first.exists
                    && second.exists
                    && min(first.frame.minY, second.frame.minY) < visibleFrame.minY
                nudgeExploreScroll(
                    scrollView.exists ? scrollView : app,
                    towardTop: contentIsAbove
                )
                settle(1)
            }
        }
        return false
    }

    private func assertAreaCardTitleClearance(
        _ title: XCUIElement,
        open: XCUIElement,
        save: XCUIElement,
        visibleFrame: CGRect
    ) {
        let isInsideOpen = title.exists && open.frame.contains(title.frame)
        let isInsideViewport = title.exists && visibleFrame.contains(title.frame)
        let overlapsSave = title.exists && !title.frame.intersection(save.frame).isEmpty
        print(
            "AUDIT[explore-area-title-save] overlap=\(overlapsSave) "
            + "insideOpen=\(isInsideOpen) insideViewport=\(isInsideViewport)"
        )
        XCTAssertTrue(isInsideOpen, "Area title extends outside its Open card")
        XCTAssertTrue(isInsideViewport, "Area title extends outside the Explore viewport")
        XCTAssertFalse(overlapsSave, "Area title intersects the Save control")
    }

    private func assertMapControlFrames(_ app: XCUIApplication) {
        let controls: [(String, XCUIElement)] = [
            ("close", app.buttons["area-close-button"].firstMatch),
            ("options", app.buttons["area-map-options-button"].firstMatch),
            ("favorite", app.buttons["area-map-favorite-button"].firstMatch),
        ]
        let isAccessibility = isAccessibilityLayout(app)
        for (tag, control) in controls {
            XCTAssertTrue(control.waitForExistence(timeout: 10), "Map control is missing")
            let isInside = isOnScreenAndHittable(control, app: app)
            print(
                "AUDIT[map-control-\(tag)] w=\(Int(control.frame.width)) "
                + "h=\(Int(control.frame.height)) inside=\(isInside)"
            )
            XCTAssertGreaterThanOrEqual(
                control.frame.width,
                44,
                "Map control is below the minimum hit width"
            )
            XCTAssertGreaterThanOrEqual(
                control.frame.height,
                44,
                "Map control is below the minimum hit height"
            )
            let maximumSize: CGFloat = isAccessibility ? 48 : 45
            XCTAssertLessThanOrEqual(
                control.frame.width,
                maximumSize,
                "Map control exceeds its bounded hit width"
            )
            XCTAssertLessThanOrEqual(
                control.frame.height,
                maximumSize,
                "Map control exceeds its bounded hit height"
            )
            XCTAssertTrue(isInside, "Map control extends outside the app frame")
        }

        XCTAssertEqual(
            Set(controls.map { $0.1.identifier }).count,
            controls.count,
            "Map controls must have distinct identifiers"
        )
        XCTAssertEqual(
            Set(controls.map { $0.1.label }).count,
            controls.count,
            "Map controls must have distinct labels"
        )
        for firstIndex in controls.indices {
            for secondIndex in controls.indices where secondIndex > firstIndex {
                XCTAssertTrue(
                    controls[firstIndex].1.frame
                        .intersection(controls[secondIndex].1.frame).isEmpty,
                    "Map controls overlap"
                )
            }
        }
    }

    private func trailActionMatches(
        _ app: XCUIApplication,
        identifier: String
    ) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(identifier: identifier)
    }

    private func trailAction(
        _ app: XCUIApplication,
        identifier: String
    ) -> XCUIElement {
        trailActionMatches(app, identifier: identifier).firstMatch
    }

    private func revealPairedIncompleteTrailActions(
        _ app: XCUIApplication,
        secondaryIdentifier: String,
        in trailScroll: XCUIElement
    ) -> TrailSelection? {
        guard secondaryIdentifier.hasPrefix("trail-secondary-") else { return nil }
        let suffix = String(secondaryIdentifier.dropFirst("trail-secondary-".count))
        guard !suffix.isEmpty else { return nil }
        let selectIdentifier = "trail-select-\(suffix)"

        for attempt in 0...20 {
            let selectMatches = trailActionMatches(app, identifier: selectIdentifier)
            let secondaryMatches = trailActionMatches(app, identifier: secondaryIdentifier)
            if let select = uniqueExistingElement(selectMatches),
               let secondary = uniqueExistingElement(secondaryMatches),
               let subject = actionSubject(select.label, after: "Select Trail,") {
                let secondaryLabel = secondary.label
                let structureIsValid = secondaryLabel == "Mark Trail Complete, \(subject)"
                    && select.identifier == selectIdentifier
                    && secondary.identifier == secondaryIdentifier
                    && select.identifier != secondary.identifier
                    && select.label != secondaryLabel
                    && select.frame.width >= 44
                    && select.frame.height >= 44
                    && secondary.frame.width >= 44
                    && secondary.frame.height >= 44
                    && select.frame.intersection(secondary.frame).isEmpty
                if structureIsValid {
                    return TrailSelection(
                        selectIdentifier: selectIdentifier,
                        secondaryIdentifier: secondaryIdentifier,
                        profileIdentifier: "trail-profile-\(suffix)",
                        subject: subject,
                        idleSecondaryLabel: secondaryLabel
                    )
                }
            }

            guard attempt < 20 else { break }
            let select = uniqueExistingElement(selectMatches)
            let secondary = uniqueExistingElement(secondaryMatches)
            if select != nil, secondary == nil {
                _ = performMeasuredMicroCorrection(
                    identifier: selectIdentifier,
                    toward: .later,
                    in: trailScroll,
                    app: app
                )
            } else if select == nil, secondary != nil {
                _ = performMeasuredMicroCorrection(
                    identifier: secondaryIdentifier,
                    toward: .earlier,
                    in: trailScroll,
                    app: app
                )
            } else if !performRowTraversal(.earlier, in: trailScroll, app: app) {
                return nil
            }
        }
        return nil
    }

    private func assertTrailActionFrames(
        _ app: XCUIApplication,
        rowIdentifier: String,
        selectLabelPrefix: String,
        secondaryLabelPrefix: String,
        tag: String
    ) {
        guard rowIdentifier.hasPrefix("trail-select-") else {
            XCTFail("Trail Select action has an unexpected identifier")
            return
        }
        let suffix = String(rowIdentifier.dropFirst("trail-select-".count))
        let secondaryIdentifier = "trail-secondary-\(suffix)"
        let selectMatches = trailActionMatches(app, identifier: rowIdentifier)
        let secondaryMatches = trailActionMatches(app, identifier: secondaryIdentifier)
        XCTAssertEqual(selectMatches.count, 1, "Trail Select action must be unique")
        XCTAssertEqual(secondaryMatches.count, 1, "Trail secondary action must be unique")
        guard let select = uniqueExistingElement(selectMatches),
              let secondary = uniqueExistingElement(secondaryMatches),
              let subject = actionSubject(select.label, after: selectLabelPrefix),
              actionSubject(secondary.label, after: secondaryLabelPrefix) == subject else {
            XCTFail("Paired trail actions are not uniquely materialized with matching semantics")
            return
        }

        let selectFrame = select.frame
        let secondaryFrame = secondary.frame
        let identifiersAreDistinct = select.identifier != secondary.identifier
        let labelsAreDistinct = select.label != secondary.label
        let actionsDoNotOverlap = selectFrame.intersection(secondaryFrame).isEmpty
        print(
            "AUDIT[\(tag)] selectW=\(Int(selectFrame.width)) "
            + "selectH=\(Int(selectFrame.height)) secondaryW=\(Int(secondaryFrame.width)) "
            + "secondaryH=\(Int(secondaryFrame.height)) overlap=\(!actionsDoNotOverlap)"
        )
        XCTAssertGreaterThanOrEqual(selectFrame.width, 44, "Trail Select hit width is too small")
        XCTAssertGreaterThanOrEqual(selectFrame.height, 44, "Trail Select hit height is too small")
        XCTAssertGreaterThanOrEqual(secondaryFrame.width, 44, "Trail secondary hit width is too small")
        XCTAssertGreaterThanOrEqual(secondaryFrame.height, 44, "Trail secondary hit height is too small")
        if isAccessibilityLayout(app) {
            XCTAssertGreaterThan(
                selectFrame.width,
                app.frame.width * 0.7,
                "Accessibility Trail Select action is not full width"
            )
            XCTAssertGreaterThan(
                secondaryFrame.width,
                app.frame.width * 0.7,
                "Accessibility trail secondary action is not full width"
            )
        } else {
            XCTAssertLessThanOrEqual(selectFrame.height, 47, "Standard Trail Select hit height is no longer compact")
            XCTAssertLessThanOrEqual(secondaryFrame.width, 47, "Standard trail secondary hit width is no longer compact")
            XCTAssertLessThanOrEqual(secondaryFrame.height, 47, "Standard trail secondary hit height is no longer compact")
        }
        XCTAssertTrue(actionsDoNotOverlap, "Trail actions overlap")
        XCTAssertTrue(identifiersAreDistinct, "Trail actions must have distinct identifiers")
        XCTAssertTrue(labelsAreDistinct, "Trail actions must have distinct labels")

        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10),
              let reachedSelect = reachTrailAction(
                app,
                identifier: rowIdentifier,
                toward: .earlier,
                in: trailScroll
              ) else {
            XCTFail("Trail Select action is not independently reachable")
            return
        }
        XCTAssertTrue(isOnScreenAndHittable(reachedSelect, app: app))

        guard let reachedSecondary = reachTrailAction(
            app,
            identifier: secondaryIdentifier,
            toward: .later,
            in: trailScroll
        ) else {
            XCTFail("Trail secondary action is not independently reachable")
            return
        }
        XCTAssertTrue(isOnScreenAndHittable(reachedSecondary, app: app))
    }

    private func assertCollectionRepresentative(
        _ row: XCUIElement,
        in collectionScroll: XCUIElement,
        app: XCUIApplication,
        expectedWholeWord: String,
        requiresFullWidth: Bool,
        tag: String
    ) {
        XCTAssertTrue(
            scrollToReachable(row, in: collectionScroll, app: app),
            "Collection representative row is not reachable"
        )
        let isInside = isOnScreenAndHittable(row, app: app)
        let hasWholeTitle = label(row.label, containsWholeWord: expectedWholeWord)
        let isFullWidth = !requiresFullWidth || row.frame.width > app.frame.width * 0.7
        print(
            "AUDIT[\(tag)] wholeTitle=\(hasWholeTitle) inside=\(isInside) "
            + "fullWidth=\(isFullWidth)"
        )
        XCTAssertTrue(isInside, "Collection representative row extends outside the app frame")
        XCTAssertTrue(hasWholeTitle, "Collection representative title is incomplete")
        XCTAssertTrue(isFullWidth, "Accessibility Collection row is not full width")
    }

    private func label(_ label: String, containsWholeWord word: String) -> Bool {
        let pattern = "\\b" + NSRegularExpression.escapedPattern(for: word) + "\\b"
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return false }
        let range = NSRange(label.startIndex..<label.endIndex, in: label)
        return expression.firstMatch(in: label, range: range) != nil
    }

    private func assertRecordingAreaHeader(_ app: XCUIApplication) {
        let header = app.descendants(matching: .any)["area-header"].firstMatch
        let headerScroll = app.scrollViews["area-header"].firstMatch
        let title = app.descendants(matching: .any)[
            "recording-area-header-title"
        ].firstMatch
        XCTAssertTrue(header.waitForExistence(timeout: 10), "Recording area header is missing")
        XCTAssertTrue(title.waitForExistence(timeout: 10), "Recording area title is missing")

        if headerScroll.exists {
            for attempt in 0...20 {
                let titleIsSettled = title.exists
                    && title.isHittable
                    && app.frame.insetBy(dx: -1, dy: -1).contains(title.frame)
                    && title.frame.minY >= header.frame.minY + 19
                if titleIsSettled { break }
                if attempt < 20 {
                    let moveContentDown = title.exists
                        && title.frame.minY < header.frame.minY + 20
                    let start = headerScroll.coordinate(
                        withNormalizedOffset: CGVector(dx: 0.5, dy: moveContentDown ? 0.35 : 0.65)
                    )
                    let end = headerScroll.coordinate(
                        withNormalizedOffset: CGVector(dx: 0.5, dy: moveContentDown ? 0.60 : 0.40)
                    )
                    start.press(forDuration: 0.05, thenDragTo: end)
                    settle(1)
                }
            }
        }

        let titleIsComplete = title.label == "South Mountain Park and Preserve"
        let titleIsInsideScreen = app.frame.insetBy(dx: -1, dy: -1).contains(title.frame)
        let titleClearsGrabber = title.frame.minY >= header.frame.minY + 19
        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        if headerScroll.exists {
            XCTAssertTrue(
                dashboardScroll.waitForExistence(timeout: 10),
                "Accessibility recording dashboard is missing"
            )
        }
        let headerClearsDashboard = !dashboardScroll.exists
            || header.frame.intersection(dashboardScroll.frame).isEmpty

        let dashboardElements = [
            app.descendants(matching: .any)["recording-gps-status"].firstMatch,
            app.descendants(matching: .any)["recording-elevation-profile"].firstMatch,
            app.descendants(matching: .any)["recording-metrics"].firstMatch,
            app.buttons["recording-stop-button"].firstMatch,
        ]
        for element in dashboardElements {
            if headerScroll.exists {
                XCTAssertTrue(
                    element.waitForExistence(timeout: 10),
                    "Accessibility recording dashboard component is missing"
                )
            }
            if element.exists {
                XCTAssertTrue(
                    title.frame.intersection(element.frame).isEmpty,
                    "Recording area title intersects dashboard content"
                )
            }
        }

        let controls = app.descendants(matching: .any)["area-map-controls"].firstMatch
        XCTAssertTrue(controls.waitForExistence(timeout: 10), "Map controls are missing")
        let mapRegionHeight = header.frame.minY - controls.frame.maxY
        print(
            "AUDIT[recording-area-header] complete=\(titleIsComplete) "
            + "insideScreen=\(titleIsInsideScreen) "
            + "grabberClear=\(titleClearsGrabber) dashboardClear=\(headerClearsDashboard) "
            + "mapHeight=\(Int(mapRegionHeight))"
        )
        XCTAssertTrue(titleIsComplete, "Recording area title is incomplete")
        XCTAssertTrue(titleIsInsideScreen, "Recording area title extends outside the app frame")
        XCTAssertTrue(title.isHittable, "Recording area title is not reachable")
        XCTAssertTrue(titleClearsGrabber, "Recording area title intersects the drag-indicator zone")
        XCTAssertTrue(headerClearsDashboard, "Recording header intersects the dashboard")
        XCTAssertGreaterThan(mapRegionHeight, 0, "Recording state hides the map region")
        XCTAssertEqual(stopControlCount(app), 1, "Recording state must expose exactly one Stop control")
        XCTAssertEqual(app.buttons.matching(identifier: "recording-stop-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "active-recording-stop-button").count, 0)
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
        assertMapControlFrames(app)
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
        let secondaryIdentifier = "trail-secondary-\(suffix)"
        let selectMatches = trailActionMatches(app, identifier: rowIdentifier)
        let secondaryMatches = trailActionMatches(app, identifier: secondaryIdentifier)
        let select = selectMatches.firstMatch
        let secondary = secondaryMatches.firstMatch
        let profile = app.descendants(matching: .any)["trail-profile-\(suffix)"].firstMatch
        XCTAssertTrue(select.waitForExistence(timeout: 10), "Selected trail action is missing")
        XCTAssertTrue(secondary.waitForExistence(timeout: 10), "Selected trail secondary action is missing")
        XCTAssertTrue(profile.waitForExistence(timeout: 10), "Selected trail profile is missing")
        XCTAssertEqual(selectMatches.count, 1, "Selected trail action must be unique")
        XCTAssertEqual(secondaryMatches.count, 1, "Selected trail secondary action must be unique")
        assertTrailActionFrames(
            app,
            rowIdentifier: rowIdentifier,
            selectLabelPrefix: "Deselect Trail,",
            secondaryLabelPrefix: "Record Trail,",
            tag: "trail-actions-fit-record"
        )
        if isAccessibilityLayout(app) {
            // The fit capture above intentionally preserves the compact stop.
            // Full selected-row reachability is exercised from Browse by the
            // focused accessibility class without mutating this state sequence.
            return
        }
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.exists, "Trail list scroll is missing")
        if trailScroll.frame.intersection(app.frame).isEmpty {
            dragSheet(app, toBottom: false)
            settle(3)
        }
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

    private func assertSelectedProfileLayout(
        _ app: XCUIApplication,
        rowIdentifier: String
    ) {
        guard rowIdentifier.hasPrefix("trail-select-") else {
            XCTFail("Selected profile row has an unexpected identifier")
            return
        }
        let suffix = String(rowIdentifier.dropFirst("trail-select-".count))
        let secondaryIdentifier = "trail-secondary-\(suffix)"
        let selectMatches = trailActionMatches(app, identifier: rowIdentifier)
        let secondaryMatches = trailActionMatches(app, identifier: secondaryIdentifier)
        let select = selectMatches.firstMatch
        let secondary = secondaryMatches.firstMatch
        let profile = app.descendants(matching: .any)["trail-profile-\(suffix)"].firstMatch
        XCTAssertTrue(select.waitForExistence(timeout: 10), "Selected trail action is missing")
        XCTAssertTrue(secondary.waitForExistence(timeout: 10), "Selected trail secondary action is missing")
        XCTAssertTrue(profile.waitForExistence(timeout: 10), "Selected trail profile is missing")
        XCTAssertEqual(selectMatches.count, 1, "Selected trail action must be unique")
        XCTAssertEqual(secondaryMatches.count, 1, "Selected trail secondary action must be unique")
        let sheetHeader = app.descendants(matching: .any)["area-header"].firstMatch
        XCTAssertGreaterThan(
            sheetHeader.frame.minY / app.frame.height,
            0.25,
            "Selected profile fit stop hides too much of the map"
        )
        XCTAssertGreaterThanOrEqual(
            profile.frame.minY,
            max(select.frame.maxY, secondary.frame.maxY) - 1,
            "Selected profile overlaps its Select or Record action"
        )

        guard isAccessibilityLayout(app) else { return }
        let direction = app.descendants(matching: .any)["trail-profile-direction"].firstMatch
        let flip = app.buttons["trail-profile-flip-button"].firstMatch
        let range = app.descendants(matching: .any)["trail-profile-range"].firstMatch
        XCTAssertTrue(direction.waitForExistence(timeout: 10), "Profile direction is missing")
        XCTAssertTrue(flip.waitForExistence(timeout: 10), "Profile Flip action is missing")
        XCTAssertTrue(range.waitForExistence(timeout: 10), "Profile range content is missing")
        let profileBounds = profile.frame.insetBy(dx: -1, dy: -1)
        XCTAssertTrue(profileBounds.contains(direction.frame), "Profile direction clips its container")
        XCTAssertTrue(profileBounds.contains(flip.frame), "Profile Flip action clips its container")
        XCTAssertTrue(profileBounds.contains(range.frame), "Profile range clips its container")
        XCTAssertTrue(
            direction.frame.intersection(flip.frame).isEmpty,
            "Profile direction overlaps the Flip action"
        )
        XCTAssertTrue(
            flip.frame.intersection(range.frame).isEmpty,
            "Profile Flip action overlaps lower profile text"
        )
        XCTAssertGreaterThanOrEqual(
            profile.frame.height,
            direction.frame.height + flip.frame.height + range.frame.height + 120,
            "Accessibility profile compressed its chart or text"
        )
    }

    private func scrollSelectedProfileLowerContent(
        _ app: XCUIApplication,
        parkingExpectation: ParkingLabelExpectation
    ) -> Bool? {
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10) else {
            XCTFail("Trail list scroll is missing for selected-profile proof")
            return nil
        }

        var didScroll = false
        if isAccessibilityLayout(app) {
            let rangeResult = scrollToVisibleResult(
                identifier: "trail-profile-range",
                in: trailScroll,
                app: app
            )
            guard rangeResult.isVisible else {
                XCTFail("Selected profile lower range is not reachable")
                return nil
            }
            didScroll = didScroll || rangeResult.didScroll
            let rangeMatches = app.descendants(matching: .any).matching(
                identifier: "trail-profile-range"
            )
            guard let range = uniqueExistingElement(rangeMatches) else {
                XCTFail("Selected profile lower range is not uniquely materialized")
                return nil
            }
            logElementFrame(app, range, tag: "selected-profile-range")
        }

        let parkingResult = scrollToVisibleResult(
            identifier: "selected-trail-parking-detail",
            in: trailScroll,
            app: app
        )
        guard parkingResult.isVisible else {
            XCTFail("Selected profile lower parking content is not reachable")
            return nil
        }
        didScroll = didScroll || parkingResult.didScroll
        guard let parking = exactContainedParkingDetail(
            app,
            in: trailScroll,
            expectation: parkingExpectation
        ) else {
            return nil
        }
        logElementFrame(app, parking, tag: "selected-profile-lower-content")

        if isAccessibilityLayout(app) {
            let rangeMatches = app.descendants(matching: .any).matching(
                identifier: "trail-profile-range"
            )
            guard let range = uniqueExistingElement(rangeMatches) else {
                XCTFail("Profile range disappeared before lower-content proof")
                return nil
            }
            XCTAssertTrue(
                range.frame.intersection(parking.frame).isEmpty,
                "Profile range overlaps selected parking content"
            )
        }
        return didScroll
    }

    /// Near and intentional far-fallback annotations expose distinct generic
    /// roles. Near markers must clear the physical screen, top controls, and
    /// native sheet. Far fallbacks are deliberately excluded from route-frame
    /// fitting and instead prove a truthful distance plus reachability.
    @discardableResult
    private func assertSelectedMapFraming(
        _ app: XCUIApplication,
        provesFarDisclosure: Bool = true
    ) -> ParkingLabelExpectation? {
        let controls = app.descendants(matching: .any)["area-map-controls"].firstMatch
        let sheetHeader = app.descendants(matching: .any)["area-header"].firstMatch
        XCTAssertTrue(controls.waitForExistence(timeout: 10), "Map controls are missing")
        XCTAssertTrue(sheetHeader.waitForExistence(timeout: 10), "Area sheet header is missing")
        guard controls.exists, sheetHeader.exists else { return nil }

        let farMarkers = app.descendants(matching: .any)
            .matching(identifier: "map-far-access-marker")
            .allElementsBoundByIndex.filter { $0.exists }
        let visibleNearMarkers = app.descendants(matching: .any)
            .matching(identifier: "map-near-access-marker")
            .allElementsBoundByIndex.filter { $0.exists }
        let nearMarkers: [XCUIElement]
        if farMarkers.isEmpty || !visibleNearMarkers.isEmpty {
            guard let stableMarkers = stableNearMarkers(app) else { return nil }
            nearMarkers = stableMarkers
        } else {
            nearMarkers = []
        }
        print(
            "AUDIT[selected-map] nearCount=\(nearMarkers.count) "
            + "farCount=\(farMarkers.count)"
        )
        XCTAssertGreaterThan(
            nearMarkers.count + farMarkers.count,
            0,
            "Selected map has no access marker"
        )
        guard !nearMarkers.isEmpty || !farMarkers.isEmpty else { return nil }

        let physicalScreen = app.frame.insetBy(dx: -1, dy: -1)
        for (index, marker) in nearMarkers.enumerated() {
            let frame = marker.frame
            let isContained = physicalScreen.contains(frame)
            let clearsControls = frame.minY >= controls.frame.maxY - 1
            let clearsSheet = frame.maxY <= sheetHeader.frame.minY + 1
            let isHittable = marker.isHittable
            print(
                "AUDIT[selected-near-marker] index=\(index) "
                + "x=\(Int(frame.minX)) y=\(Int(frame.minY)) "
                + "w=\(Int(frame.width)) h=\(Int(frame.height)) "
                + "contained=\(isContained) clearsControls=\(clearsControls) "
                + "clearsSheet=\(clearsSheet) hittable=\(isHittable)"
            )
            XCTAssertTrue(isContained, "Near selected marker extends outside the screen")
            XCTAssertTrue(isHittable, "Near selected marker is not reachable")
            XCTAssertTrue(clearsControls, "Near selected marker intersects top map controls")
            XCTAssertTrue(clearsSheet, "Near selected marker intersects the area sheet")
        }

        for (index, marker) in farMarkers.enumerated() {
            let hasGenericRole = marker.identifier == "map-far-access-marker"
            let hasNonemptyLabel = !marker.label.isEmpty
            let hasTruthfulDistance = marker.label.contains("from trail")
            print(
                "AUDIT[selected-far-marker] index=\(index) "
                + "genericRole=\(hasGenericRole) nonempty=\(hasNonemptyLabel) "
                + "hasDistance=\(hasTruthfulDistance)"
            )
            XCTAssertTrue(hasGenericRole, "Far fallback marker has an unexpected role")
            XCTAssertTrue(hasNonemptyLabel, "Far fallback marker has no semantics")
            XCTAssertTrue(hasTruthfulDistance, "Far fallback marker omits its trail distance")
        }

        let expectation: ParkingLabelExpectation = nearMarkers.isEmpty
            ? .farDistance
            : .nearTrailhead
        if expectation == .farDistance, provesFarDisclosure {
            assertFarOnlyParkingDisclosure(app)
        }
        return expectation
    }

    private func assertFarOnlyParkingDisclosure(_ app: XCUIApplication) {
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10),
              scrollToVisible(
                identifier: "selected-trail-parking-detail",
                in: trailScroll,
                app: app
              ),
              exactContainedParkingDetail(
                app,
                in: trailScroll,
                expectation: .farDistance
              ) != nil else {
            XCTFail("Far-only selected parking detail is not visible")
            return
        }
    }

    private func exactContainedParkingDetail(
        _ app: XCUIApplication,
        in trailScroll: XCUIElement,
        expectation: ParkingLabelExpectation
    ) -> XCUIElement? {
        let parkingMatches = app.descendants(matching: .any).matching(
            identifier: "selected-trail-parking-detail"
        )
        XCTAssertEqual(
            parkingMatches.count,
            1,
            "Selected parking detail must be unique"
        )
        guard let parking = uniqueExistingElement(parkingMatches) else { return nil }

        let viewport = trailScroll.frame.intersection(app.frame)
        let frame = parking.frame
        let elementType = parking.elementType
        let label = parking.label
        let isInformationalText = elementType == .staticText
        let isFullyContained = viewport.contains(frame)
        let hasExpectedLabel = parkingLabelMatches(label, expectation: expectation)
        XCTAssertTrue(
            isInformationalText,
            "Selected parking detail must remain informational text"
        )
        XCTAssertTrue(
            isFullyContained,
            "Selected parking detail extends outside the visible trail list"
        )
        XCTAssertTrue(
            hasExpectedLabel,
            "Selected parking detail violates the marker-derived label contract"
        )
        guard isInformationalText, isFullyContained, hasExpectedLabel else { return nil }
        return parking
    }

    private func parkingLabelMatches(
        _ label: String,
        expectation: ParkingLabelExpectation
    ) -> Bool {
        switch expectation {
        case .nearTrailhead:
            return label == "Parking at the trailhead"
        case .farDistance:
            let pattern = "^Nearest parking: (?:.+, )?[0-9]+(?:\\.[0-9]{1,2})? (?:mi|km) away$"
            guard let expression = try? NSRegularExpression(pattern: pattern) else { return false }
            let range = NSRange(label.startIndex..<label.endIndex, in: label)
            return expression.firstMatch(in: label, range: range) != nil
        }
    }

    private func stableNearMarkers(_ app: XCUIApplication) -> [XCUIElement]? {
        var previousFrames: [CGRect]?
        var stableObservationCount = 0

        for attempt in 0..<10 {
            let markers = app.descendants(matching: .any)
                .matching(identifier: "map-near-access-marker")
                .allElementsBoundByIndex
                .filter { $0.exists }
                .sorted { lhs, rhs in
                    let left = lhs.frame
                    let right = rhs.frame
                    return (left.minX, left.minY, left.width, left.height)
                        < (right.minX, right.minY, right.width, right.height)
                }
            let frames = markers.map { $0.frame }
            let framesAreValid = !frames.isEmpty && frames.allSatisfy { frame in
                [frame.minX, frame.minY, frame.maxX, frame.maxY].allSatisfy(\.isFinite)
                    && frame.width > 0
                    && frame.height > 0
            }

            if framesAreValid {
                if let previousFrames,
                   frames.count == previousFrames.count,
                   zip(frames, previousFrames).allSatisfy({ current, previous in
                       abs(current.minX - previous.minX) <= 1
                           && abs(current.minY - previous.minY) <= 1
                           && abs(current.width - previous.width) <= 1
                           && abs(current.height - previous.height) <= 1
                   }) {
                    stableObservationCount += 1
                } else {
                    stableObservationCount = 1
                }
                previousFrames = frames
                if stableObservationCount == 3 { return markers }
            } else {
                previousFrames = nil
                stableObservationCount = 0
            }

            if attempt < 9 { settle(1) }
        }

        XCTFail("Near selected markers did not produce a stable complete frame set")
        return nil
    }

    private func isAccessibilityLayout(_ app: XCUIApplication) -> Bool {
        app.scrollViews["area-header"].firstMatch.exists
    }

    private func stopControlCount(_ app: XCUIApplication) -> Int {
        app.buttons.matching(NSPredicate(
            format: "identifier == %@ OR identifier == %@ OR identifier == %@",
            "active-recording-stop-button",
            "recording-stop-button",
            "walk-stop-button"
        )).count
    }

    private func scrollToVisible(
        identifier: String,
        in scrollView: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        scrollToVisibleResult(
            identifier: identifier,
            in: scrollView,
            app: app
        ).isVisible
    }

    private func scrollToVisibleResult(
        identifier: String,
        in scrollView: XCUIElement,
        app: XCUIApplication
    ) -> ScrollVisibilityResult {
        guard assertAuditedAppEnvironment(app) else {
            return ScrollVisibilityResult(isVisible: false, didScroll: false)
        }
        var previousCorrection: CGFloat?
        var activation = ParkingActivation.normal
        var didScroll = false

        for attempt in 0...20 {
            let viewport = scrollView.frame.intersection(app.frame)
            guard !viewport.isNull, viewport.width > 0, viewport.height > 0 else {
                XCTFail("Parking scroll has no valid visible viewport")
                return ScrollVisibilityResult(isVisible: false, didScroll: didScroll)
            }
            let containmentViewport = viewport.insetBy(dx: -1, dy: -1)
            let matches = app.descendants(matching: .any).matching(identifier: identifier)
            guard let element = uniqueExistingElement(matches) else {
                XCTFail("Parking scroll target is missing or not unique")
                return ScrollVisibilityResult(isVisible: false, didScroll: didScroll)
            }
            let elementFrame = element.frame
            if containmentViewport.contains(elementFrame) {
                return ScrollVisibilityResult(isVisible: true, didScroll: didScroll)
            }

            guard attempt < 20 else { break }
            let correction = parkingContainmentCorrection(
                elementFrame: elementFrame,
                viewport: viewport,
                previousCorrection: previousCorrection
            )
            guard correction != 0 else { break }
            let crossed = previousCorrection.map { $0 * correction < 0 } ?? false
            let gestureFrame = viewport.insetBy(dx: 8, dy: 8)
            let travel = parkingExecutableTravel(
                correction: correction,
                gestureHeight: gestureFrame.height,
                activation: activation,
                crossed: crossed
            )
            performLowMomentumContentCorrection(
                travel,
                in: scrollView,
                viewport: viewport
            )
            didScroll = true
            settle(1)

            let settledMatches = app.descendants(matching: .any).matching(
                identifier: identifier
            )
            guard let settled = uniqueExistingElement(settledMatches) else {
                XCTFail("Parking scroll target was lost after correction")
                return ScrollVisibilityResult(isVisible: false, didScroll: didScroll)
            }
            let moved = parkingMovedInExpectedDirection(
                from: elementFrame,
                to: settled.frame,
                correction: correction
            )
            switch parkingStallDecision(
                activation: activation,
                moved: moved,
                crossed: crossed
            ) {
            case .continueNormally:
                activation = .normal
            case .promote:
                activation = .promoted
            case .fail:
                print(
                    "AUDIT[parking-scroll-stalled] beforeY=\(Int(elementFrame.midY)) "
                    + "afterY=\(Int(settled.frame.midY)) correction=\(Int(correction))"
                )
                XCTFail("Parking scroll made no progress after promoted correction")
                return ScrollVisibilityResult(isVisible: false, didScroll: didScroll)
            }
            previousCorrection = correction
        }

        return ScrollVisibilityResult(isVisible: false, didScroll: didScroll)
    }

    /// A negative correction is a finger-up/content-up move; a positive one
    /// is finger-down/content-down. Edge overflow, not midpoint, determines it.
    private func parkingContainmentCorrection(
        elementFrame: CGRect?,
        viewport: CGRect,
        previousCorrection: CGFloat?
    ) -> CGFloat {
        let tinyMargin: CGFloat = 2
        let baseCap = min(64, max(24, viewport.height * 0.28))
        let requested: CGFloat
        if let elementFrame {
            if elementFrame.maxY > viewport.maxY {
                requested = -(elementFrame.maxY - viewport.maxY + tinyMargin)
            } else if elementFrame.minY < viewport.minY {
                requested = viewport.minY - elementFrame.minY + tinyMargin
            } else {
                return 0
            }
        } else {
            requested = -baseCap
        }

        var cap = baseCap
        if let previousCorrection,
           previousCorrection != 0,
           requested * previousCorrection < 0 {
            cap = min(cap, max(1, abs(previousCorrection) * 0.5))
        }
        let magnitude = min(abs(requested), cap)
        return requested < 0 ? -magnitude : magnitude
    }

    private func parkingExecutableTravel(
        correction: CGFloat,
        gestureHeight: CGFloat,
        activation: ParkingActivation,
        crossed: Bool
    ) -> CGFloat {
        let cap = min(64, gestureHeight * 0.45)
        let floor: CGFloat = activation == .normal ? 20 : 24
        let magnitude = crossed
            ? min(abs(correction), cap)
            : min(max(abs(correction), floor), cap)
        return correction < 0 ? -magnitude : magnitude
    }

    private func parkingMovedInExpectedDirection(
        from oldFrame: CGRect,
        to newFrame: CGRect,
        correction: CGFloat
    ) -> Bool {
        let movement = newFrame.midY - oldFrame.midY
        return abs(movement) > 1 && movement * correction > 0
    }

    private func parkingStallDecision(
        activation: ParkingActivation,
        moved: Bool,
        crossed: Bool
    ) -> ParkingStallDecision {
        if moved || crossed { return .continueNormally }
        return activation == .normal ? .promote : .fail
    }

    private func performLowMomentumContentCorrection(
        _ travel: CGFloat,
        in scrollView: XCUIElement,
        viewport: CGRect
    ) {
        let gestureFrame = viewport.insetBy(dx: 8, dy: 8)
        let scrollFrame = scrollView.frame
        guard gestureFrame.width > 0,
              gestureFrame.height > 0,
              scrollFrame.width > 0,
              scrollFrame.height > 0,
              travel != 0 else {
            return
        }
        let distance = abs(travel)
        let direction: CGFloat = travel < 0 ? -1 : 1
        let startY = gestureFrame.midY - direction * distance / 2
        let endY = gestureFrame.midY + direction * distance / 2
        let normalizedX = (gestureFrame.midX - scrollFrame.minX) / scrollFrame.width
        let start = scrollView.coordinate(
            withNormalizedOffset: CGVector(
                dx: normalizedX,
                dy: (startY - scrollFrame.minY) / scrollFrame.height
            )
        )
        let end = scrollView.coordinate(
            withNormalizedOffset: CGVector(
                dx: normalizedX,
                dy: (endY - scrollFrame.minY) / scrollFrame.height
            )
        )
        start.press(
            forDuration: 0.15,
            thenDragTo: end,
            withVelocity: .slow,
            thenHoldForDuration: 0.10
        )
    }

    private func exactMatchAllowsPropertyRead(
        matchCount: Int,
        exists: Bool
    ) -> Bool {
        matchCount == 1 && exists
    }

    private func uniqueExistingElement(
        _ matches: XCUIElementQuery
    ) -> XCUIElement? {
        let matchCount = matches.count
        guard matchCount == 1 else { return nil }
        let element = matches.firstMatch
        guard exactMatchAllowsPropertyRead(
            matchCount: matchCount,
            exists: element.exists
        ) else {
            return nil
        }
        return element
    }

    private func knownTargetGestureOffsets(
        _ position: KnownTargetPosition
    ) -> (start: CGFloat, end: CGFloat) {
        switch position {
        case .earlier:
            return (0.20, 0.80)
        case .later:
            return (0.80, 0.20)
        }
    }

    private func materializedTrailActionFrames(
        _ app: XCUIApplication
    ) -> [String: CGRect] {
        let matches = app.descendants(matching: .any).matching(NSPredicate(
            format: "identifier BEGINSWITH %@ OR identifier BEGINSWITH %@",
            "trail-select-",
            "trail-secondary-"
        ))
        var frames: [String: CGRect] = [:]
        for element in matches.allElementsBoundByIndex where element.exists {
            frames[element.identifier] = element.frame
        }
        return frames
    }

    private func actionSnapshotsShowProgress(
        before: [String: CGRect],
        after: [String: CGRect]
    ) -> Bool {
        guard Set(before.keys) == Set(after.keys) else { return true }
        return before.contains { key, oldFrame in
            guard let newFrame = after[key] else { return true }
            return abs(newFrame.minX - oldFrame.minX) > 1
                || abs(newFrame.minY - oldFrame.minY) > 1
                || abs(newFrame.width - oldFrame.width) > 1
                || abs(newFrame.height - oldFrame.height) > 1
        }
    }

    private func performRowTraversal(
        _ position: KnownTargetPosition,
        in trailScroll: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        guard assertAuditedAppEnvironment(app) else { return false }
        for _ in 0..<2 {
            let visible = trailScroll.frame.intersection(app.frame)
            guard !visible.isNull, visible.width > 0, visible.height >= 44 else {
                XCTFail("Trail row traversal has no gesture-safe viewport")
                return false
            }
            let before = materializedTrailActionFrames(app)
            let offsets = knownTargetGestureOffsets(position)
            let start = app.coordinate(withNormalizedOffset: .zero).withOffset(
                CGVector(dx: visible.midX, dy: visible.minY + visible.height * offsets.start)
            )
            let end = app.coordinate(withNormalizedOffset: .zero).withOffset(
                CGVector(dx: visible.midX, dy: visible.minY + visible.height * offsets.end)
            )
            start.press(forDuration: 0.05, thenDragTo: end)
            settle(1)
            let after = materializedTrailActionFrames(app)
            if actionSnapshotsShowProgress(before: before, after: after) { return true }
        }
        let count = materializedTrailActionFrames(app).count
        XCTFail("Trail row traversal made no measured progress; visibleCount=\(count)")
        return false
    }

    private func performMicroChildCorrection(
        _ position: KnownTargetPosition,
        in trailScroll: XCUIElement,
        app: XCUIApplication
    ) {
        let visible = trailScroll.frame.intersection(app.frame).insetBy(dx: 8, dy: 8)
        guard visible.width > 0, visible.height >= 44 else { return }
        let offsets: (start: CGFloat, end: CGFloat) = position == .earlier
            ? (0.35, 0.55)
            : (0.65, 0.45)
        let start = app.coordinate(withNormalizedOffset: .zero).withOffset(
            CGVector(dx: visible.midX, dy: visible.minY + visible.height * offsets.start)
        )
        let end = app.coordinate(withNormalizedOffset: .zero).withOffset(
            CGVector(dx: visible.midX, dy: visible.minY + visible.height * offsets.end)
        )
        start.press(
            forDuration: 0.15,
            thenDragTo: end,
            withVelocity: .slow,
            thenHoldForDuration: 0.10
        )
    }

    private func performMeasuredMicroCorrection(
        identifier: String,
        toward position: KnownTargetPosition,
        in trailScroll: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        for _ in 0..<2 {
            let beforeMatches = trailActionMatches(app, identifier: identifier)
            guard let before = uniqueExistingElement(beforeMatches) else { return false }
            let beforeFrame = before.frame
            performMicroChildCorrection(position, in: trailScroll, app: app)
            settle(1)
            let afterMatches = trailActionMatches(app, identifier: identifier)
            guard let after = uniqueExistingElement(afterMatches) else { return false }
            let correction: CGFloat = position == .earlier ? 1 : -1
            if parkingMovedInExpectedDirection(
                from: beforeFrame,
                to: after.frame,
                correction: correction
            ) {
                return true
            }
        }
        XCTFail("Trail child correction made no measured progress")
        return false
    }

    private func actionIsReachable(
        frame: CGRect,
        viewport: CGRect,
        isHittable: Bool
    ) -> Bool {
        let visible = frame.intersection(viewport)
        return isHittable && visible.width >= 44 && visible.height >= 44
    }

    private func reachTrailAction(
        _ app: XCUIApplication,
        identifier: String,
        toward fallback: KnownTargetPosition,
        in trailScroll: XCUIElement
    ) -> XCUIElement? {
        for attempt in 0...20 {
            let matches = trailActionMatches(app, identifier: identifier)
            if matches.count > 1 {
                XCTFail("Trail action reachability query is not unique")
                return nil
            }
            if let action = uniqueExistingElement(matches) {
                let viewport = trailScroll.frame.intersection(app.frame)
                let frame = action.frame
                if actionIsReachable(
                    frame: frame,
                    viewport: viewport,
                    isHittable: action.isHittable
                ) {
                    return action
                }
                guard attempt < 20 else { break }
                let direction: KnownTargetPosition = frame.minY < viewport.minY
                    ? .earlier
                    : .later
                if !performMeasuredMicroCorrection(
                    identifier: identifier,
                    toward: direction,
                    in: trailScroll,
                    app: app
                ) {
                    return nil
                }
            } else {
                guard attempt < 20,
                      performRowTraversal(fallback, in: trailScroll, app: app) else {
                    return nil
                }
            }
        }
        return nil
    }

    private func pairedTrailIdentifiers(
        for selectIdentifier: String
    ) -> (secondary: String, profile: String)? {
        guard selectIdentifier.hasPrefix("trail-select-") else { return nil }
        let suffix = String(selectIdentifier.dropFirst("trail-select-".count))
        guard !suffix.isEmpty else { return nil }
        return (
            secondary: "trail-secondary-\(suffix)",
            profile: "trail-profile-\(suffix)"
        )
    }

    private func primaryDeselectIsReady(
        label: String,
        frame: CGRect,
        appFrame: CGRect,
        isHittable: Bool
    ) -> Bool {
        actionSubject(label, after: "Deselect Trail,") != nil
            && appFrame.contains(frame)
            && isHittable
    }

    private func postDeselectRecovery(
        hasSelect: Bool,
        hasSecondary: Bool,
        labelsSettled: Bool
    ) -> PostDeselectRecovery {
        if hasSelect, hasSecondary {
            return labelsSettled ? .done : .wait
        }
        if hasSelect { return .microLater }
        if hasSecondary { return .microEarlier }
        return .macroLater
    }

    private func assertNavigationRegressionControls() {
        XCTAssertFalse(exactMatchAllowsPropertyRead(matchCount: 0, exists: false))
        XCTAssertFalse(exactMatchAllowsPropertyRead(matchCount: 2, exists: true))
        XCTAssertTrue(exactMatchAllowsPropertyRead(matchCount: 1, exists: true))

        let earlier = knownTargetGestureOffsets(.earlier)
        let later = knownTargetGestureOffsets(.later)
        XCTAssertEqual(earlier.start, 0.20)
        XCTAssertEqual(earlier.end, 0.80)
        XCTAssertEqual(later.start, 0.80)
        XCTAssertEqual(later.end, 0.20)
        XCTAssertGreaterThan(earlier.end - earlier.start, 0)
        XCTAssertLessThan(later.end - later.start, 0)

        let viewport = CGRect(x: 0, y: 0, width: 100, height: 100)
        let containmentViewport = viewport.insetBy(dx: -1, dy: -1)
        XCTAssertTrue(
            containmentViewport.contains(CGRect(x: 0, y: 0, width: 100, height: 100.5))
        )
        XCTAssertFalse(
            containmentViewport.contains(CGRect(x: 0, y: 0, width: 100, height: 108))
        )

        let below = parkingContainmentCorrection(
            elementFrame: CGRect(x: 0, y: 90, width: 100, height: 30),
            viewport: viewport,
            previousCorrection: nil
        )
        let crossedAbove = parkingContainmentCorrection(
            elementFrame: CGRect(x: 0, y: -20, width: 100, height: 30),
            viewport: viewport,
            previousCorrection: below
        )
        XCTAssertLessThan(below, 0)
        XCTAssertGreaterThan(crossedAbove, 0)
        XCTAssertLessThan(abs(crossedAbove), abs(below))
        XCTAssertEqual(
            parkingExecutableTravel(
                correction: -6,
                gestureHeight: 100,
                activation: .normal,
                crossed: false
            ),
            -20
        )
        XCTAssertEqual(
            parkingExecutableTravel(
                correction: -6,
                gestureHeight: 100,
                activation: .promoted,
                crossed: false
            ),
            -24
        )
        XCTAssertEqual(
            parkingExecutableTravel(
                correction: 100,
                gestureHeight: 100,
                activation: .normal,
                crossed: false
            ),
            45
        )
        XCTAssertEqual(
            parkingExecutableTravel(
                correction: 4,
                gestureHeight: 100,
                activation: .normal,
                crossed: true
            ),
            4
        )
        XCTAssertTrue(
            parkingMovedInExpectedDirection(
                from: CGRect(x: 0, y: 80, width: 10, height: 10),
                to: CGRect(x: 0, y: 72, width: 10, height: 10),
                correction: -8
            )
        )
        XCTAssertFalse(
            parkingMovedInExpectedDirection(
                from: CGRect(x: 0, y: 80, width: 10, height: 10),
                to: CGRect(x: 0, y: 79.5, width: 10, height: 10),
                correction: -8
            )
        )
        XCTAssertEqual(
            parkingStallDecision(activation: .normal, moved: false, crossed: false),
            .promote
        )
        XCTAssertEqual(
            parkingStallDecision(activation: .promoted, moved: false, crossed: false),
            .fail
        )
        XCTAssertEqual(
            parkingStallDecision(activation: .promoted, moved: true, crossed: false),
            .continueNormally
        )

        XCTAssertTrue(parkingLabelMatches("Parking at the trailhead", expectation: .nearTrailhead))
        XCTAssertTrue(parkingLabelMatches("Nearest parking: 0.76 mi away", expectation: .farDistance))
        XCTAssertTrue(parkingLabelMatches("Nearest parking: South Lot, 1.2 km away", expectation: .farDistance))
        XCTAssertFalse(parkingLabelMatches("Parking nearby", expectation: .farDistance))
        XCTAssertFalse(parkingLabelMatches("Nearest parking: 0.76 mi away", expectation: .nearTrailhead))

        let actionViewport = CGRect(x: 0, y: 0, width: 320, height: 205)
        let tallSelect = CGRect(x: 0, y: -80, width: 320, height: 362)
        let secondary = CGRect(x: 0, y: 144, width: 61, height: 61)
        XCTAssertGreaterThan(tallSelect.height + 8 + secondary.height, actionViewport.height)
        XCTAssertTrue(actionIsReachable(frame: tallSelect, viewport: actionViewport, isHittable: true))
        XCTAssertTrue(actionIsReachable(frame: secondary, viewport: actionViewport, isHittable: true))

        XCTAssertEqual(postDeselectRecovery(hasSelect: false, hasSecondary: false, labelsSettled: false), .macroLater)
        XCTAssertEqual(postDeselectRecovery(hasSelect: true, hasSecondary: false, labelsSettled: false), .microLater)
        XCTAssertEqual(postDeselectRecovery(hasSelect: false, hasSecondary: true, labelsSettled: false), .microEarlier)
        XCTAssertEqual(postDeselectRecovery(hasSelect: true, hasSecondary: true, labelsSettled: false), .wait)
        XCTAssertEqual(postDeselectRecovery(hasSelect: true, hasSecondary: true, labelsSettled: true), .done)

        XCTAssertTrue(
            primaryDeselectIsReady(
                label: "Deselect Trail, Regression Trail",
                frame: CGRect(x: 10, y: 10, width: 80, height: 44),
                appFrame: viewport,
                isHittable: true
            )
        )
        let retained = pairedTrailIdentifiers(for: "trail-select-regression-trail")
        XCTAssertEqual(retained?.secondary, "trail-secondary-regression-trail")
        XCTAssertEqual(retained?.profile, "trail-profile-regression-trail")
    }

    private func scrollToReachable(
        _ element: XCUIElement,
        in scrollView: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        for attempt in 0...20 {
            if isOnScreenAndHittable(element, app: app) { return true }
            if attempt < 20 {
                let moveContentDown = element.exists && element.frame.midY < app.frame.midY
                let start = scrollView.coordinate(
                    withNormalizedOffset: CGVector(dx: 0.5, dy: moveContentDown ? 0.35 : 0.65)
                )
                let end = scrollView.coordinate(
                    withNormalizedOffset: CGVector(dx: 0.5, dy: moveContentDown ? 0.55 : 0.45)
                )
                start.press(forDuration: 0.05, thenDragTo: end)
                settle(1)
            }
        }
        return false
    }

    private func isOnScreen(_ element: XCUIElement, app: XCUIApplication) -> Bool {
        guard element.exists else { return false }
        let frame = element.frame
        let screen = app.frame
        return frame.minX >= screen.minX - 1
            && frame.maxX <= screen.maxX + 1
            && frame.minY >= screen.minY - 1
            && frame.maxY <= screen.maxY + 1
    }

    private func isOnScreenAndHittable(_ element: XCUIElement, app: XCUIApplication) -> Bool {
        element.isHittable && isOnScreen(element, app: app)
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

    private func launchRecordingAudit(arguments: [String]) -> XCUIApplication? {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"] + arguments
        app.launch()
        guard assertAuditedAppEnvironment(app) else { return nil }
        settle(25)
        return app
    }

    private func assertAuditedAppEnvironment(_ app: XCUIApplication) -> Bool {
        guard app.wait(for: .runningForeground, timeout: 60),
              app.state == .runningForeground else {
            XCTFail("AUDIT[audited-app-not-foreground]")
            return false
        }
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        guard !springboard.alerts.firstMatch.exists else {
            XCTFail("AUDIT[unexpected-system-alert]")
            return false
        }
        let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
        guard settings.state != .runningForeground else {
            XCTFail("AUDIT[settings-foreground]")
            return false
        }
        return true
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
    private func assertLayoutInvariants(_ app: XCUIApplication) {
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
        if !isAccessibilityLayout(app),
           let before = toolbarY["min-idle"],
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

    private func enterBrowseUsingSearch(_ app: XCUIApplication) -> Bool {
        let search = app.buttons["area-search-button"].firstMatch
        guard search.waitForExistence(timeout: 10), search.isHittable else {
            XCTFail("Fit Search action is unavailable")
            return false
        }
        tapElement(search)
        guard waitForBrowseChrome(app, requiresKeyboard: true) else { return false }
        guard dismissBrowseKeyboard(app) else { return false }
        return waitForBrowseChrome(app, requiresKeyboard: false)
    }

    private func waitForBrowseChrome(
        _ app: XCUIApplication,
        requiresKeyboard: Bool
    ) -> Bool {
        let field = app.textFields["Search trails"].firstMatch
        for attempt in 0...10 {
            let hasBrowseAction = app.buttons.matching(
                identifier: "trail-filter-button"
            ).count == 1 || app.buttons.matching(
                identifier: "trail-search-keyboard-done"
            ).count == 1
            let chromeIsCorrect = field.exists
                && app.buttons.matching(identifier: "area-search-button").count == 0
                && (requiresKeyboard
                    ? hasBrowseAction
                    : app.buttons.matching(identifier: "trail-filter-button").count == 1)
            let keyboardIsCorrect = requiresKeyboard
                ? app.keyboards.firstMatch.exists
                : !app.keyboards.firstMatch.exists
            if chromeIsCorrect && keyboardIsCorrect { return true }
            if attempt < 10 { settle(1) }
        }
        XCTFail(
            requiresKeyboard
                ? "Browse chrome or focused keyboard did not appear"
                : "Browse chrome did not settle with its keyboard dismissed"
        )
        return false
    }

    private func waitForFitChrome(_ app: XCUIApplication) -> Bool {
        for attempt in 0...10 {
            let isFit = app.buttons.matching(identifier: "area-search-button").count == 1
                && app.textFields.matching(identifier: "Search trails").count == 0
                && app.buttons.matching(identifier: "trail-filter-button").count == 0
            if isFit { return true }
            if attempt < 10 { settle(1) }
        }
        XCTFail("Selected Browse handoff did not reach the fit stop")
        return false
    }

    private func dismissBrowseKeyboard(_ app: XCUIApplication) -> Bool {
        let keyboard = app.keyboards.firstMatch
        guard keyboard.exists else {
            XCTFail("Browse Search did not focus the keyboard")
            return false
        }
        if isAccessibilityLayout(app) {
            let done = app.buttons["Dismiss Search Keyboard"].firstMatch
            guard done.waitForExistence(timeout: 5), done.isHittable else {
                XCTFail("Search keyboard Done action is unavailable")
                return false
            }
            done.tap()
        } else {
            let searchField = app.textFields["Search trails"].firstMatch
            guard searchField.waitForExistence(timeout: 5), searchField.isHittable else {
                XCTFail("Focused Browse Search field is unavailable")
                return false
            }
            searchField.typeText("\n")
        }
        for attempt in 0...5 {
            if !keyboard.exists { return true }
            if attempt < 5 { settle(1) }
        }
        XCTFail("Browse Search keyboard did not dismiss")
        return false
    }

    private func restoreSelectedTrailActionAfterLowerContent(
        _ app: XCUIApplication,
        identifier: String,
        in trailScroll: XCUIElement
    ) -> XCUIElement? {
        guard pairedTrailIdentifiers(for: identifier) != nil,
              let selected = reachTrailAction(
                app,
                identifier: identifier,
                toward: .earlier,
                in: trailScroll
              ) else {
            return nil
        }
        let label = selected.label
        let frame = selected.frame
        let isHittable = selected.isHittable
        return primaryDeselectIsReady(
            label: label,
            frame: frame,
            appFrame: app.frame,
            isHittable: isHittable
        ) ? selected : nil
    }

    private func proveSelectedTrailContextBeforeDeselect(
        _ app: XCUIApplication,
        selection: TrailSelection,
        in trailScroll: XCUIElement
    ) -> Bool {
        guard let secondary = reachTrailAction(
            app,
            identifier: selection.secondaryIdentifier,
            toward: .later,
            in: trailScroll
        ),
              secondary.label == "Record Trail, \(selection.subject)" else {
            return false
        }

        for attempt in 0...20 {
            let profileMatches = app.descendants(matching: .any).matching(
                identifier: selection.profileIdentifier
            )
            if uniqueExistingElement(profileMatches) != nil { return true }
            guard attempt < 20,
                  performRowTraversal(.later, in: trailScroll, app: app) else {
                return false
            }
        }
        return false
    }

    private func freshDeselectActionAfterLowerContent(
        _ app: XCUIApplication,
        selection: TrailSelection,
        in trailScroll: XCUIElement
    ) -> XCUIElement? {
        guard restoreSelectedTrailActionAfterLowerContent(
            app,
            identifier: selection.selectIdentifier,
            in: trailScroll
        ) != nil,
              proveSelectedTrailContextBeforeDeselect(
                app,
                selection: selection,
                in: trailScroll
              ),
              restoreSelectedTrailActionAfterLowerContent(
                app,
                identifier: selection.selectIdentifier,
                in: trailScroll
              ) != nil else {
            return nil
        }

        // Reacquire only the exact action that will be tapped after the final
        // recovery gesture; the paired Record/profile proofs are value latches.
        let freshMatches = trailActionMatches(
            app,
            identifier: selection.selectIdentifier
        )
        guard let freshDeselect = uniqueExistingElement(freshMatches) else {
            return nil
        }
        let label = freshDeselect.label
        let frame = freshDeselect.frame
        let isHittable = freshDeselect.isHittable
        guard label == "Deselect Trail, \(selection.subject)",
              primaryDeselectIsReady(
                label: label,
                frame: frame,
                appFrame: app.frame,
                isHittable: isHittable
              ) else {
            return nil
        }
        return freshDeselect
    }

    private func actionSubject(_ label: String, after prefix: String) -> String? {
        guard label.hasPrefix(prefix) else { return nil }
        let subject = String(label.dropFirst(prefix.count))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return subject.isEmpty ? nil : subject
    }

    private func deselectTrailAndWait(
        _ app: XCUIApplication,
        selection: TrailSelection
    ) -> Bool {
        guard let identifiers = pairedTrailIdentifiers(
            for: selection.selectIdentifier
        ),
              identifiers.secondary == selection.secondaryIdentifier,
              identifiers.profile == selection.profileIdentifier else {
            XCTFail("Selected trail identity changed before deselection")
            return false
        }
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10) else {
            XCTFail("Trail list scroll is missing for deselection")
            return false
        }
        guard let freshDeselect = freshDeselectActionAfterLowerContent(
            app,
            selection: selection,
            in: trailScroll
        ) else {
            XCTFail("Selected trail row did not restore its exact action and profile semantics")
            return false
        }

        freshDeselect.tap()
        return waitForDeselectedTrailState(
            app,
            selection: selection,
            in: trailScroll
        )
    }

    private func waitForDeselectedTrailState(
        _ app: XCUIApplication,
        selection: TrailSelection,
        in trailScroll: XCUIElement
    ) -> Bool {
        guard let identifiers = pairedTrailIdentifiers(
            for: selection.selectIdentifier
        ),
              identifiers.secondary == selection.secondaryIdentifier,
              identifiers.profile == selection.profileIdentifier else {
            XCTFail("Deselected trail identity changed")
            return false
        }

        for attempt in 0...20 {
            let selectMatches = trailActionMatches(app, identifier: selection.selectIdentifier)
            let secondaryMatches = trailActionMatches(app, identifier: selection.secondaryIdentifier)
            guard selectMatches.count <= 1, secondaryMatches.count <= 1 else {
                XCTFail("Deselected trail action query is not unique")
                return false
            }
            let select = uniqueExistingElement(selectMatches)
            let secondary = uniqueExistingElement(secondaryMatches)
            var labelsSettled = false
            if let select, let secondary {
                let recordMatches = app.buttons.matching(identifier: "area-record-button")
                if let record = uniqueExistingElement(recordMatches) {
                    labelsSettled = select.identifier == selection.selectIdentifier
                        && secondary.identifier == selection.secondaryIdentifier
                        && select.label == "Select Trail, \(selection.subject)"
                        && secondary.label == selection.idleSecondaryLabel
                        && select.label != secondary.label
                        && record.label == "Start a hike"
                }
            }

            switch postDeselectRecovery(
                hasSelect: select != nil,
                hasSecondary: secondary != nil,
                labelsSettled: labelsSettled
            ) {
            case .done:
                return true
            case .wait:
                if attempt < 20 { settle(1) }
            case .macroLater:
                guard attempt < 20,
                      performRowTraversal(.later, in: trailScroll, app: app) else {
                    return false
                }
            case .microLater:
                guard attempt < 20,
                      performMeasuredMicroCorrection(
                        identifier: selection.selectIdentifier,
                        toward: .later,
                        in: trailScroll,
                        app: app
                      ) else {
                    return false
                }
            case .microEarlier:
                guard attempt < 20,
                      performMeasuredMicroCorrection(
                        identifier: selection.secondaryIdentifier,
                        toward: .earlier,
                        in: trailScroll,
                        app: app
                      ) else {
                    return false
                }
            }
        }
        XCTFail("Trail deselection did not settle exact retained-row idle semantics")
        return false
    }

    private func moveToFitUsingGrabber(_ app: XCUIApplication) -> Bool {
        for attempt in 0...3 {
            let isFit = app.buttons.matching(identifier: "area-search-button").count == 1
                && app.textFields.matching(identifier: "Search trails").count == 0
            if isFit { return true }
            if attempt < 3 {
                dragSheet(app, toBottom: true)
                settle(2)
            }
        }
        XCTFail("Native sheet did not reach the fit stop")
        return false
    }

    /// Drag from the clear system-grabber band at the top of the native sheet,
    /// never from AX header text or the trail-list scroll surface.
    private func dragSheet(_ app: XCUIApplication, toBottom: Bool) {
        let header = app.descendants(matching: .any)["area-header"].firstMatch
        let anchorY = header.exists ? header.frame.minY + 6 : app.frame.height * 0.62
        let from = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: anchorY))
        let targetY = toBottom ? app.frame.height - 8 : app.frame.height * 0.2
        let to = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: targetY))
        from.press(forDuration: 0.1, thenDragTo: to)
    }

    /// Scroll only the true trail-list surface. Intersecting its semantic frame
    /// with the app frame prevents an AX row extending below the screen from
    /// turning this into a sheet/header gesture.
    private func swipeList(_ app: XCUIApplication, up: Bool) {
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.exists else {
            XCTFail("Trail list scroll is missing")
            return
        }
        let visible = trailScroll.frame.intersection(app.frame).insetBy(dx: 8, dy: 12)
        guard visible.width > 0, visible.height >= 44 else {
            XCTFail("Trail list has no gesture-safe visible surface")
            return
        }
        let high = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: visible.midX, dy: visible.minY))
        let low = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: visible.midX, dy: visible.maxY))
        if up { low.press(forDuration: 0.05, thenDragTo: high) }
        else { high.press(forDuration: 0.05, thenDragTo: low) }
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

    /// Tap the first reachable incomplete trail's semantic Select button and
    /// return its stable identifier so callers can address the same row after
    /// its label changes to Deselect. The bounded search first proves the
    /// independent Mark Complete target without exposing trail data.
    private func tapFirstTrailRow(_ app: XCUIApplication) -> TrailSelection? {
        let start = app.buttons["area-record-button"].firstMatch
        guard start.exists else {
            dumpTree(app, "no-toolbar-before-row-tap")
            return nil
        }
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.exists else {
            dumpTree(app, "no-trail-scroll-before-row-tap")
            return nil
        }
        let rowBandTop = start.frame.maxY + 4
        for attempt in 0...20 {
            let completionActions = app.descendants(matching: .any).matching(NSPredicate(
                format: "identifier BEGINSWITH %@ AND label BEGINSWITH %@",
                "trail-secondary-",
                "Mark Trail Complete,"
            )).allElementsBoundByIndex
            if let visibleSecondary = completionActions
                .filter({
                    $0.exists
                        && $0.isHittable
                        && $0.frame.minY > rowBandTop
                        && $0.frame.maxY <= app.frame.maxY
                })
                .min(by: { $0.frame.minY < $1.frame.minY }) {
                let secondaryIdentifier = visibleSecondary.identifier
                guard let pair = revealPairedIncompleteTrailActions(
                    app,
                    secondaryIdentifier: secondaryIdentifier,
                    in: trailScroll
                ) else {
                    XCTFail("Paired trail Select action is not reachable")
                    return nil
                }
                let rowIdentifier = pair.selectIdentifier
                assertTrailActionFrames(
                    app,
                    rowIdentifier: rowIdentifier,
                    selectLabelPrefix: "Select Trail,",
                    secondaryLabelPrefix: "Mark Trail Complete,",
                    tag: "trail-actions-complete"
                )
                guard let freshSelect = reachTrailAction(
                    app,
                    identifier: rowIdentifier,
                    toward: .earlier,
                    in: trailScroll
                ) else {
                    XCTFail("Trail Select action is not reachable immediately before tapping")
                    return nil
                }
                print("AUDIT tapping first incomplete trail row")
                tapElement(freshSelect)

                guard let identifiers = pairedTrailIdentifiers(for: rowIdentifier) else {
                    XCTFail("Paired trail identity is invalid")
                    return nil
                }
                for postconditionAttempt in 0...5 {
                    let selectedMatches = trailActionMatches(
                        app,
                        identifier: rowIdentifier
                    )
                    let secondaryMatches = trailActionMatches(
                        app,
                        identifier: identifiers.secondary
                    )
                    if let selected = uniqueExistingElement(selectedMatches),
                       let secondary = uniqueExistingElement(secondaryMatches),
                       selected.label == "\(deselectTrailLabelPrefix) \(pair.subject)",
                       secondary.label == "Record Trail, \(pair.subject)" {
                        return pair
                    }
                    if postconditionAttempt < 5 { settle(1) }
                }
                XCTFail("Trail Select action did not settle exact selected semantics")
                return nil
            }
            if attempt < 20,
               !performRowTraversal(.later, in: trailScroll, app: app) {
                return nil
            }
        }
        dumpTree(app, "no-incomplete-trail-row-found")
        XCTFail("No reachable Mark Complete trail action was found")
        return nil
    }

    // MARK: - Frame logging

    /// Print the frames that decide whether this layout is clipped. The
    /// screen height is printed alongside so `maxY > screen` is readable
    /// straight off the CI log.
    private func logFrames(_ app: XCUIApplication, _ tag: String) {
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

    @discardableResult
    private func capture(_ app: XCUIApplication, _ name: String) -> Data {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        return shot.pngRepresentation
    }

    private func dumpTree(_ app: XCUIApplication, _ tag: String) {
        let elementCount = app.descendants(matching: .any).count
        print("AUDIT[\(tag)] hierarchy unavailable; elementCount=\(elementCount)")
    }
}
