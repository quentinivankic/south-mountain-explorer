import XCTest

final class FieldTrustAccessibilityTests: XCTestCase {

    private let areaId = "south-mountain-park-and-preserve-az"

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    func testAreaCardOpenAndSaveAreIndependentButtons() {
        let app = launchSeededApp()
        let visibleFrame = exploreVisibleContentFrame(app)

        let continueButton = app.buttons["continue-card"].firstMatch
        XCTAssertTrue(continueButton.waitForExistence(timeout: 30))
        XCTAssertTrue(
            scrollIntoExploreViewport(continueButton, app: app),
            "Continue card did not settle inside the Explore viewport"
        )
        let continueIsOpenAction = continueButton.label.hasPrefix("Open Area,")
        XCTAssertTrue(continueIsOpenAction, "Continue must expose an Open Area action")
        assertInsideFrame(continueButton, frame: visibleFrame)
        assertCompleteStandardTitle(
            app.descendants(matching: .any)["continue-card-title"].firstMatch
        )

        let open = app.buttons["area-open-\(areaId)"].firstMatch
        XCTAssertTrue(
            scrollIntoExploreViewport(open, app: app),
            "Area Open button did not settle inside the Explore viewport"
        )
        let save = app.buttons["area-save-\(areaId)"].firstMatch
        XCTAssertTrue(open.exists, "Area Open button is missing")
        XCTAssertTrue(save.exists, "Area Save button is missing")
        let areaActionIdentifiersAreDistinct = open.identifier != save.identifier
        XCTAssertTrue(areaActionIdentifiersAreDistinct, "Area actions must have distinct identifiers")
        let areaActionLabelsAreDistinct = open.label != save.label
        XCTAssertTrue(areaActionLabelsAreDistinct, "Area actions must have distinct labels")
        let openHasExpectedLabel = open.label.hasPrefix("Open Area,")
        XCTAssertTrue(openHasExpectedLabel, "Area Open button has an unexpected label")
        let saveHasExpectedLabel = save.label == "Remove from Saved Areas"
        XCTAssertTrue(saveHasExpectedLabel, "Area Save button has an unexpected label")
        assertInsideFrame(open, frame: visibleFrame)
        assertInsideFrame(save, frame: visibleFrame)
        assertCompleteStandardTitle(
            app.descendants(matching: .any)["area-card-title-\(areaId)"].firstMatch
        )

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
        XCTAssertTrue(app.tabBars.buttons["Stats"].firstMatch.isHittable)
        XCTAssertTrue(app.tabBars.buttons["Browse"].firstMatch.isHittable)
        XCTAssertTrue(app.tabBars.buttons["Settings"].firstMatch.isHittable)

        XCTAssertTrue(scrollIntoExploreViewport(save, app: app))
        save.tap()
        XCTAssertFalse(
            app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 2),
            "Saving an area opened it"
        )

        app.terminate()
        app.launchArguments = ["--uitest-seed"]
        app.launch()
        _ = app.tabBars.buttons["Explore"].waitForExistence(timeout: 30)

        let resetOpen = app.buttons["area-open-\(areaId)"].firstMatch
        let resetSave = app.buttons["area-save-\(areaId)"].firstMatch
        XCTAssertTrue(scrollIntoExploreViewport(resetOpen, app: app))
        let resetSaveHasExpectedLabel = resetSave.label == "Remove from Saved Areas"
        XCTAssertTrue(resetSaveHasExpectedLabel, "Seeded Area Save button has an unexpected label")
        resetOpen.tap()
        XCTAssertTrue(
            app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60),
            "Opening an area did not show its map"
        )

        let close = app.buttons["area-close-button"].firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 10))
        close.tap()
        let saveAfterOpen = app.buttons["area-save-\(areaId)"].firstMatch
        XCTAssertTrue(scrollIntoExploreViewport(saveAfterOpen, app: app))
        let savedStateWasPreserved = saveAfterOpen.label == "Remove from Saved Areas"
        XCTAssertTrue(savedStateWasPreserved, "Opening an area changed its saved state")
    }

    func testTrailSelectAndCompleteAreIndependentButtons() {
        let app = launchSeededApp()
        let continueButton = app.buttons["continue-card"].firstMatch
        XCTAssertTrue(continueButton.waitForExistence(timeout: 30))
        continueButton.tap()
        XCTAssertTrue(app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60))

        let selectQuery = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "trail-select-")
        )
        let select = selectQuery.firstMatch
        XCTAssertTrue(select.waitForExistence(timeout: 60), "No semantic trail Select button appeared")
        let trailSuffix = String(select.identifier.dropFirst("trail-select-".count))
        let secondary = app.buttons["trail-secondary-\(trailSuffix)"].firstMatch
        XCTAssertTrue(secondary.waitForExistence(timeout: 10), "Trail secondary action is missing")
        let trailActionIdentifiersAreDistinct = select.identifier != secondary.identifier
        XCTAssertTrue(trailActionIdentifiersAreDistinct, "Trail actions must have distinct identifiers")
        let actionLabelsAreDistinct = select.label != secondary.label
        XCTAssertTrue(actionLabelsAreDistinct, "Trail action labels must be distinct")
        let selectHasExpectedLabel = select.label.hasPrefix("Select Trail,")
        XCTAssertTrue(selectHasExpectedLabel, "Trail Select action has an unexpected label")
        let initialWasComplete = secondary.label.hasPrefix("Mark Trail Incomplete,")
        let hasExpectedCompletionAction =
            initialWasComplete || secondary.label.hasPrefix("Mark Trail Complete,")
        XCTAssertTrue(hasExpectedCompletionAction, "Unexpected trail completion action")
        let toggledPrefix = initialWasComplete ? "Mark Trail Complete," : "Mark Trail Incomplete,"

        secondary.tap()
        XCTAssertTrue(
            waitForLabelPrefix(toggledPrefix, element: secondary),
            "Completion action did not toggle completion"
        )
        let selectionStayedInactive = select.label.hasPrefix("Select Trail,")
        XCTAssertTrue(selectionStayedInactive, "Mark Complete selected the trail")

        select.tap()
        XCTAssertTrue(waitForLabelPrefix("Deselect Trail,", element: select))
        XCTAssertTrue(
            waitForLabelPrefix("Record Trail,", element: secondary),
            "Selecting a trail did not expose its independent Record action"
        )

        select.tap()
        XCTAssertTrue(waitForLabelPrefix("Select Trail,", element: select))
        XCTAssertTrue(
            waitForLabelPrefix(toggledPrefix, element: secondary),
            "Selecting and deselecting changed completion state"
        )
    }

    func testAreaSheetAndCollectionAdaptAtAccessibilitySize() {
        let app = XCUIApplication()
        app.launchArguments = [
            "--uitest-seed",
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryAccessibilityXXXL",
        ]
        app.launch()

        let continueButton = app.buttons["continue-card"].firstMatch
        XCTAssertTrue(continueButton.waitForExistence(timeout: 30))
        continueButton.tap()
        XCTAssertTrue(app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60))

        let header = app.descendants(matching: .any)["area-header"].firstMatch
        let title = app.descendants(matching: .any)["area-header-title"].firstMatch
        let metrics = app.descendants(matching: .any)["area-header-metrics"].firstMatch
        let actions = app.descendants(matching: .any)["area-action-group"].firstMatch
        XCTAssertTrue(header.waitForExistence(timeout: 10), "Area header is missing")
        XCTAssertTrue(title.exists, "Area title is missing")
        XCTAssertTrue(metrics.exists, "Area metrics are missing")
        XCTAssertTrue(actions.exists, "Area actions are missing")
        assertInsideScreen(header, app: app)
        assertInsideScreen(title, app: app)
        assertInsideScreen(metrics, app: app)
        assertInsideScreen(actions, app: app)

        XCTAssertEqual(app.buttons["area-record-button"].count, 1)
        XCTAssertEqual(app.buttons["area-search-button"].count, 1)
        XCTAssertEqual(app.buttons["area-collection-button"].count, 1)
        XCTAssertEqual(app.textFields["Search trails"].count, 0)
        XCTAssertEqual(app.buttons["trail-filter-button"].count, 0)

        let select = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "trail-select-")
        ).firstMatch
        XCTAssertTrue(select.waitForExistence(timeout: 30), "Trail Select action is missing")
        let suffix = String(select.identifier.dropFirst("trail-select-".count))
        let secondary = app.buttons["trail-secondary-\(suffix)"].firstMatch
        XCTAssertTrue(secondary.waitForExistence(timeout: 10), "Trail secondary action is missing")
        assertInsideScreen(select, app: app)
        assertInsideScreen(secondary, app: app)
        select.tap()
        XCTAssertTrue(waitForLabelPrefix("Deselect Trail,", element: select))
        XCTAssertTrue(waitForLabelPrefix("Record Trail,", element: secondary))

        let profile = app.descendants(matching: .any)["trail-profile-\(suffix)"].firstMatch
        XCTAssertTrue(profile.waitForExistence(timeout: 10), "Trail profile is missing")
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.waitForExistence(timeout: 10), "Trail list scroll is missing")
        XCTAssertTrue(
            scrollToReachable(profile, in: trailScroll, app: app),
            "Trail profile is not reachable"
        )
        assertInsideScreen(profile, app: app)
        XCTAssertEqual(app.buttons["trail-profile-flip-button"].count, 1)

        XCTAssertTrue(scrollToReachable(select, in: trailScroll, app: app))
        select.tap()
        XCTAssertTrue(waitForLabelPrefix("Select Trail,", element: select))
        let search = app.buttons["area-search-button"].firstMatch
        XCTAssertTrue(search.waitForExistence(timeout: 10), "Fit Search action is missing")
        search.tap()
        XCTAssertTrue(app.textFields["Search trails"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertEqual(app.textFields["Search trails"].count, 1)
        XCTAssertEqual(app.buttons["trail-filter-button"].count, 1)
        XCTAssertEqual(app.buttons["area-search-button"].count, 0)

        let collection = app.buttons["area-collection-button"].firstMatch
        XCTAssertTrue(collection.waitForExistence(timeout: 10), "Collection action is missing")
        collection.tap()
        let collectionScroll = app.scrollViews["collection-scroll"].firstMatch
        XCTAssertTrue(collectionScroll.waitForExistence(timeout: 20), "Collection scroll is missing")
        XCTAssertTrue(
            app.descendants(matching: .any)["collection-category-milestones"].firstMatch.exists,
            "Collection first category is missing"
        )
        let finalContent = app.descendants(matching: .any)["collection-dedication-final"].firstMatch
        XCTAssertTrue(
            scrollToReachable(finalContent, in: collectionScroll, app: app),
            "Collection final content is not reachable"
        )
        assertInsideScreen(finalContent, app: app)
        XCTAssertGreaterThan(
            finalContent.frame.width,
            app.frame.width * 0.7,
            "Accessibility Collection badges must use full-width rows"
        )
    }

    func testRecordingControlsAndSummaryRemainUniqueAtAccessibilitySize() {
        let app = XCUIApplication()
        app.launchArguments = [
            "--uitest-seed",
            "--uitest-recording-gap",
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryAccessibilityXXXL",
        ]
        app.launch()

        let banner = app.buttons["active-recording-banner"].firstMatch
        XCTAssertTrue(banner.waitForExistence(timeout: 30), "Active recording banner is missing")
        assertInsideScreen(banner, app: app)
        let bannerMetadata = banner.value as? String ?? ""
        XCTAssertFalse(bannerMetadata.isEmpty, "Active recording metadata is missing")
        XCTAssertEqual(stopControlCount(app), 1, "A non-contextual screen must expose one Stop control")
        XCTAssertEqual(app.buttons["active-recording-stop-button"].count, 1)

        banner.tap()
        let status = app.descendants(matching: .any)["recording-gps-status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 60), "Recording GPS status is missing")
        XCTAssertEqual(status.label, "GPS recovered", "Recording GPS status has unexpected copy")
        assertInsideScreen(status, app: app)
        XCTAssertEqual(stopControlCount(app), 1, "A contextual recording screen must expose one Stop control")
        XCTAssertEqual(app.buttons["recording-stop-button"].count, 1)
        XCTAssertEqual(app.buttons["active-recording-stop-button"].count, 0)

        let close = app.buttons["area-close-button"].firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 10), "Area close control is missing")
        close.tap()
        XCTAssertTrue(
            app.buttons["active-recording-stop-button"].firstMatch.waitForExistence(timeout: 10),
            "Global Stop did not return after the contextual panel disappeared"
        )
        XCTAssertEqual(stopControlCount(app), 1)

        app.buttons["active-recording-banner"].firstMatch.tap()
        XCTAssertTrue(status.waitForExistence(timeout: 60), "Recording panel did not reopen")
        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        XCTAssertTrue(dashboardScroll.waitForExistence(timeout: 10), "Accessibility recording scroll is missing")
        let elevation = app.descendants(matching: .any)["recording-elevation-summary"].firstMatch
        XCTAssertTrue(scrollToReachable(elevation, in: dashboardScroll, app: app))
        assertInsideScreen(elevation, app: app)
        let metrics = app.descendants(matching: .any)["recording-metrics"].firstMatch
        XCTAssertTrue(scrollToReachable(metrics, in: dashboardScroll, app: app))
        assertInsideScreen(metrics, app: app)
        let estimates = app.descendants(matching: .any)["recording-estimates"].firstMatch
        XCTAssertTrue(scrollToReachable(estimates, in: dashboardScroll, app: app))
        assertInsideScreen(estimates, app: app)

        let stop = app.buttons["recording-stop-button"].firstMatch
        XCTAssertTrue(scrollToReachable(stop, in: dashboardScroll, app: app))
        stop.tap()
        let save = app.buttons["Stop & Save"].firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 10), "Stop & Save action is missing")
        save.tap()

        let done = app.buttons["recording-summary-done"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 60), "Summary Done action is missing")
        XCTAssertEqual(app.buttons["recording-summary-done"].count, 1)
        XCTAssertEqual(
            app.buttons.matching(NSPredicate(format: "label == %@", "Done")).count,
            1,
            "Recording summary must expose exactly one Done action"
        )
        assertInsideScreen(done, app: app)

        let gap = app.descendants(matching: .any)["recording-gap-summary"].firstMatch
        XCTAssertTrue(gap.waitForExistence(timeout: 10), "Summary gap explanation is missing")
        let summaryScroll = app.scrollViews["recording-summary-scroll"].firstMatch
        XCTAssertTrue(summaryScroll.waitForExistence(timeout: 10), "Summary scroll is missing")
        let summaryMetrics = app.descendants(matching: .any)["recording-summary-metrics"].firstMatch
        XCTAssertTrue(scrollToReachable(summaryMetrics, in: summaryScroll, app: app))
        assertInsideScreen(summaryMetrics, app: app)
        let lowerContent = app.descendants(matching: .any)["recording-summary-area-progress"].firstMatch
        XCTAssertTrue(scrollToReachable(lowerContent, in: summaryScroll, app: app))
        assertInsideScreen(lowerContent, app: app)
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
            }
        }
        return false
    }

    private func launchSeededApp() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"]
        app.launch()
        _ = app.tabBars.buttons["Explore"].waitForExistence(timeout: 30)
        return app
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
        let visibleFrame = exploreVisibleContentFrame(app)
        for attempt in 0...12 {
            if element.exists,
               element.isHittable,
               visibleFrame.contains(element.frame) {
                return true
            }
            if attempt < 12 {
                if element.exists, element.frame.minY < visibleFrame.minY {
                    app.swipeDown()
                } else {
                    app.swipeUp()
                }
                sleep(1)
            }
        }
        return false
    }

    private func assertCompleteStandardTitle(_ title: XCUIElement) {
        XCTAssertTrue(title.waitForExistence(timeout: 10), "Area title is missing")
        let isComplete = title.label == "South Mountain Park and Preserve"
        XCTAssertTrue(isComplete, "Area title is incomplete")
        XCTAssertLessThanOrEqual(title.frame.height, 52, "Area title exceeds two standard lines")
    }

    private func assertInsideFrame(_ element: XCUIElement, frame: CGRect) {
        XCTAssertTrue(element.exists, "Explore control is missing")
        XCTAssertTrue(element.isHittable, "Explore control is not hittable")
        XCTAssertTrue(frame.contains(element.frame), "Explore control is outside the visible viewport")
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

    private func waitForLabelPrefix(
        _ prefix: String,
        element: XCUIElement,
        timeout: TimeInterval = 5
    ) -> Bool {
        let predicate = NSPredicate(format: "label BEGINSWITH %@", prefix)
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
        return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
    }

    private func assertInsideScreen(_ element: XCUIElement, app: XCUIApplication) {
        let frame = element.frame
        let screen = app.frame
        let isInside = frame.minX >= screen.minX - 1
            && frame.maxX <= screen.maxX + 1
            && frame.minY >= screen.minY - 1
            && frame.maxY <= screen.maxY + 1
        XCTAssertTrue(isInside, "Control extends outside the app frame")
    }
}
