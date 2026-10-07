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
        assertCompleteAreaTitle(
            app.descendants(matching: .any)["continue-card-title"].firstMatch,
            app: app
        )
        assertExploreLocationEmptyState(app)

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
        let areaTitle = app.descendants(matching: .any)[
            "area-card-title-\(areaId)"
        ].firstMatch
        assertCompleteAreaTitle(areaTitle, app: app)
        assertAreaCardTitleClearance(
            areaTitle,
            open: open,
            save: save,
            visibleFrame: visibleFrame
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
        let app = launchSeededApp(arguments: ["--uitest-completed", "0"])
        let continueButton = app.buttons["continue-card"].firstMatch
        XCTAssertTrue(continueButton.waitForExistence(timeout: 30))
        continueButton.tap()
        XCTAssertTrue(app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60))
        _ = openBrowseSearch(app)
        dismissSearchKeyboard(app)

        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.waitForExistence(timeout: 10), "Trail list scroll is missing")
        let secondary = app.buttons.matching(NSPredicate(
            format: "identifier BEGINSWITH %@ AND label BEGINSWITH %@",
            "trail-secondary-",
            "Mark Trail Complete,"
        )).firstMatch
        XCTAssertTrue(
            scrollToReachable(secondary, in: trailScroll, app: app),
            "No incomplete trail action appeared"
        )
        let trailSuffix = String(secondary.identifier.dropFirst("trail-secondary-".count))
        let select = app.buttons["trail-select-\(trailSuffix)"].firstMatch
        XCTAssertTrue(
            scrollToReachable(select, in: trailScroll, app: app),
            "No paired semantic trail Select button appeared"
        )
        assertTrailActionFrames(
            app,
            select: select,
            secondary: secondary,
            selectLabelPrefix: "Select Trail,",
            secondaryLabelPrefix: "Mark Trail Complete,"
        )
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
        let toggledSecondary = app.buttons["trail-secondary-\(trailSuffix)"].firstMatch
        XCTAssertTrue(
            waitForLabelPrefix(toggledPrefix, element: toggledSecondary),
            "Completion action did not toggle completion"
        )
        let inactiveSelect = app.buttons["trail-select-\(trailSuffix)"].firstMatch
        let selectionStayedInactive = inactiveSelect.label.hasPrefix("Select Trail,")
        XCTAssertTrue(selectionStayedInactive, "Mark Complete selected the trail")

        inactiveSelect.tap()
        let selectedControl = app.buttons["trail-select-\(trailSuffix)"].firstMatch
        let recordControl = app.buttons["trail-secondary-\(trailSuffix)"].firstMatch
        XCTAssertTrue(waitForLabelPrefix("Deselect Trail,", element: selectedControl))
        XCTAssertTrue(
            waitForLabelPrefix("Record Trail,", element: recordControl),
            "Selecting a trail did not expose its independent Record action"
        )
        assertTrailActionFrames(
            app,
            select: selectedControl,
            secondary: recordControl,
            selectLabelPrefix: "Deselect Trail,",
            secondaryLabelPrefix: "Record Trail,"
        )
        sleep(3)
        assertSelectedMapFraming(app)

        selectedControl.tap()
        let finalSelect = app.buttons["trail-select-\(trailSuffix)"].firstMatch
        let finalSecondary = app.buttons["trail-secondary-\(trailSuffix)"].firstMatch
        XCTAssertTrue(waitForLabelPrefix("Select Trail,", element: finalSelect))
        XCTAssertTrue(
            waitForLabelPrefix(toggledPrefix, element: finalSecondary),
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
        XCTAssertTrue(
            scrollToReachable(title, in: header, app: app),
            "Area title is not reachable"
        )
        assertInsideScreen(title, app: app)
        XCTAssertTrue(
            scrollToReachable(metrics, in: header, app: app),
            "Area metrics are not reachable"
        )
        assertInsideScreen(metrics, app: app)
        assertInsideScreen(actions, app: app)

        XCTAssertEqual(app.buttons.matching(identifier: "area-record-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-search-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-collection-button").count, 1)
        XCTAssertEqual(app.textFields.matching(identifier: "Search trails").count, 0)
        XCTAssertEqual(app.buttons.matching(identifier: "trail-filter-button").count, 0)

        _ = openBrowseSearch(app)
        dismissSearchKeyboard(app)
        XCTAssertEqual(app.textFields.matching(identifier: "Search trails").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "trail-filter-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-search-button").count, 0)

        let select = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "trail-select-")
        ).firstMatch
        XCTAssertTrue(select.waitForExistence(timeout: 30), "Trail Select action is missing")
        let suffix = String(select.identifier.dropFirst("trail-select-".count))
        let secondary = app.buttons["trail-secondary-\(suffix)"].firstMatch
        XCTAssertTrue(secondary.waitForExistence(timeout: 10), "Trail secondary action is missing")
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.waitForExistence(timeout: 10), "Trail list scroll is missing")
        XCTAssertTrue(scrollToReachable(select, in: trailScroll, app: app))
        XCTAssertTrue(scrollToReachable(secondary, in: trailScroll, app: app))
        assertInsideScreen(select, app: app)
        assertInsideScreen(secondary, app: app)
        assertTrailActionFrames(
            app,
            select: select,
            secondary: secondary,
            selectLabelPrefix: "Select Trail,",
            secondaryLabelPrefix: "Mark Trail "
        )
        select.tap()
        XCTAssertTrue(waitForLabelPrefix("Deselect Trail,", element: select))
        XCTAssertTrue(waitForLabelPrefix("Record Trail,", element: secondary))
        assertTrailActionFrames(
            app,
            select: select,
            secondary: secondary,
            selectLabelPrefix: "Deselect Trail,",
            secondaryLabelPrefix: "Record Trail,"
        )
        sleep(3)
        assertSelectedMapFraming(app)

        let profile = app.descendants(matching: .any)["trail-profile-\(suffix)"].firstMatch
        XCTAssertTrue(profile.waitForExistence(timeout: 10), "Trail profile is missing")
        let selectedSheetHeader = app.descendants(matching: .any)["area-header"].firstMatch
        XCTAssertGreaterThan(
            selectedSheetHeader.frame.minY / app.frame.height,
            0.25,
            "Accessibility selected profile hides too much of the map"
        )
        let direction = app.descendants(matching: .any)["trail-profile-direction"].firstMatch
        let flip = app.buttons["trail-profile-flip-button"].firstMatch
        let range = app.descendants(matching: .any)["trail-profile-range"].firstMatch
        XCTAssertTrue(direction.waitForExistence(timeout: 10), "Profile direction is missing")
        XCTAssertTrue(flip.waitForExistence(timeout: 10), "Profile Flip action is missing")
        XCTAssertTrue(range.waitForExistence(timeout: 10), "Profile range is missing")
        XCTAssertGreaterThanOrEqual(
            profile.frame.minY,
            max(select.frame.maxY, secondary.frame.maxY) - 1,
            "Selected profile overlaps Select or Record"
        )
        let profileBounds = profile.frame.insetBy(dx: -1, dy: -1)
        XCTAssertTrue(profileBounds.contains(direction.frame))
        XCTAssertTrue(profileBounds.contains(flip.frame))
        XCTAssertTrue(profileBounds.contains(range.frame))
        XCTAssertTrue(direction.frame.intersection(flip.frame).isEmpty)
        XCTAssertTrue(flip.frame.intersection(range.frame).isEmpty)
        XCTAssertGreaterThanOrEqual(
            profile.frame.height,
            direction.frame.height + flip.frame.height + range.frame.height + 120,
            "Accessibility profile compressed its chart or lower text"
        )
        XCTAssertTrue(scrollToReachable(flip, in: trailScroll, app: app))
        assertInsideScreen(flip, app: app)
        XCTAssertTrue(scrollToVisible(range, in: trailScroll, app: app))
        assertInsideScreen(range, app: app)
        let parking = app.descendants(matching: .any)[
            "selected-trail-parking-detail"
        ].firstMatch
        XCTAssertTrue(parking.waitForExistence(timeout: 10), "Selected parking detail is missing")
        XCTAssertTrue(scrollToVisible(parking, in: trailScroll, app: app))
        assertInsideScreen(parking, app: app)
        XCTAssertTrue(
            range.frame.intersection(parking.frame).isEmpty,
            "Profile range overlaps selected parking content"
        )

        let selectedControl = app.buttons["trail-select-\(suffix)"].firstMatch
        XCTAssertTrue(scrollToReachable(selectedControl, in: trailScroll, app: app))
        selectedControl.tap()
        let deselectedControl = app.buttons["trail-select-\(suffix)"].firstMatch
        XCTAssertTrue(waitForLabelPrefix("Select Trail,", element: deselectedControl))

        let collection = app.buttons["area-collection-button"].firstMatch
        XCTAssertTrue(collection.waitForExistence(timeout: 10), "Collection action is missing")
        collection.tap()
        let collectionScroll = app.scrollViews["collection-scroll"].firstMatch
        XCTAssertTrue(collectionScroll.waitForExistence(timeout: 20), "Collection scroll is missing")
        XCTAssertTrue(
            app.descendants(matching: .any)["collection-category-milestones"].firstMatch.exists,
            "Collection first category is missing"
        )
        let milestone = app.descendants(matching: .any)[
            "collection-milestone-representative"
        ].firstMatch
        assertCollectionRepresentative(
            milestone,
            in: collectionScroll,
            app: app,
            expectedWholeWord: "Completionist"
        )
        let difficulty = app.descendants(matching: .any)[
            "collection-difficulty-representative"
        ].firstMatch
        assertCollectionRepresentative(
            difficulty,
            in: collectionScroll,
            app: app,
            expectedWholeWord: "Easygoer"
        )
        let finalContent = app.descendants(matching: .any)["collection-dedication-final"].firstMatch
        XCTAssertTrue(
            scrollToReachable(finalContent, in: collectionScroll, app: app),
            "Collection final content is not reachable"
        )
        assertInsideScreen(finalContent, app: app)
        XCTAssertGreaterThanOrEqual(
            finalContent.frame.height,
            44,
            "Collection final row is smaller than a reachable control"
        )
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
        XCTAssertEqual(app.buttons.matching(identifier: "active-recording-stop-button").count, 1)

        banner.tap()
        let status = app.descendants(matching: .any)["recording-gps-status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 60), "Recording GPS status is missing")
        XCTAssertEqual(status.label, "GPS recovered", "Recording GPS status has unexpected copy")
        assertRecordingAreaHeader(app)
        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        XCTAssertTrue(dashboardScroll.waitForExistence(timeout: 10), "Accessibility recording scroll is missing")
        if dashboardScroll.frame.intersection(app.frame).isEmpty {
            expandAreaSheet(app)
        }
        XCTAssertTrue(scrollToVisible(status, in: dashboardScroll, app: app))
        assertInsideScreen(status, app: app)
        XCTAssertEqual(stopControlCount(app), 1, "A contextual recording screen must expose one Stop control")
        XCTAssertEqual(app.buttons.matching(identifier: "recording-stop-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "active-recording-stop-button").count, 0)

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
        let reopenedDashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        XCTAssertTrue(
            reopenedDashboardScroll.waitForExistence(timeout: 10),
            "Reopened accessibility recording scroll is missing"
        )
        let elevation = app.descendants(matching: .any)["recording-elevation-summary"].firstMatch
        XCTAssertTrue(elevation.waitForExistence(timeout: 15), "Recording elevation summary is missing")
        XCTAssertTrue(scrollToVisible(elevation, in: reopenedDashboardScroll, app: app))
        assertInsideScreen(elevation, app: app)
        let metrics = app.descendants(matching: .any)["recording-metrics"].firstMatch
        XCTAssertTrue(scrollToVisible(metrics, in: reopenedDashboardScroll, app: app))
        assertInsideScreen(metrics, app: app)
        let estimates = app.descendants(matching: .any)["recording-estimates"].firstMatch
        XCTAssertFalse(
            estimates.exists,
            "Gap fixture must not invent an estimate without a stable pace"
        )

        let stop = app.buttons["recording-stop-button"].firstMatch
        XCTAssertTrue(scrollToReachable(stop, in: reopenedDashboardScroll, app: app))
        stop.tap()
        let save = app.buttons["Stop & Save"].firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 10), "Stop & Save action is missing")
        save.tap()

        let done = app.buttons["recording-summary-done"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 60), "Summary Done action is missing")
        XCTAssertEqual(app.buttons.matching(identifier: "recording-summary-done").count, 1)
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
        let distanceRow = app.descendants(matching: .any)[
            "recording-summary-stat-distance"
        ].firstMatch
        let durationRow = app.descendants(matching: .any)[
            "recording-summary-stat-duration"
        ].firstMatch
        XCTAssertTrue(distanceRow.waitForExistence(timeout: 10), "Distance metric row is missing")
        XCTAssertTrue(durationRow.waitForExistence(timeout: 10), "Duration metric row is missing")
        XCTAssertTrue(scrollToVisible(distanceRow, in: summaryScroll, app: app))
        assertInsideScreen(distanceRow, app: app)
        XCTAssertTrue(scrollToVisible(durationRow, in: summaryScroll, app: app))
        assertInsideScreen(durationRow, app: app)
        XCTAssertEqual(distanceRow.label, "Distance", "Distance metric label is unexpected")
        XCTAssertEqual(durationRow.label, "Duration", "Duration metric label is unexpected")
        XCTAssertFalse(
            (distanceRow.value as? String ?? "").isEmpty,
            "Distance metric value is missing"
        )
        XCTAssertFalse(
            (durationRow.value as? String ?? "").isEmpty,
            "Duration metric value is missing"
        )
        XCTAssertGreaterThan(
            distanceRow.frame.width,
            150,
            "Distance metric semantic frame is too narrow"
        )
        XCTAssertGreaterThan(
            durationRow.frame.width,
            150,
            "Duration metric semantic frame is too narrow"
        )
        let lowerContent = app.descendants(matching: .any)["recording-summary-area-progress"].firstMatch
        XCTAssertTrue(
            scrollToVisible(lowerContent, in: summaryScroll, app: app),
            "Complete Summary Area Progress card is not reachable"
        )
        assertInsideScreen(lowerContent, app: app)
        XCTAssertGreaterThan(lowerContent.frame.width, app.frame.width * 0.7)
        let progressTitle = app.staticTexts["Area Progress"].firstMatch
        let progressValue = app.descendants(matching: .any)[
            "recording-summary-area-progress-value"
        ].firstMatch
        let progressBar = app.descendants(matching: .any)[
            "recording-summary-area-progress-bar"
        ].firstMatch
        let cardBounds = lowerContent.frame.insetBy(dx: -1, dy: -1)
        XCTAssertTrue(progressTitle.exists, "Area Progress title is missing")
        XCTAssertTrue(progressValue.exists, "Area Progress value is missing")
        XCTAssertTrue(progressBar.exists, "Area Progress bar is missing")
        XCTAssertTrue(cardBounds.contains(progressTitle.frame))
        XCTAssertTrue(cardBounds.contains(progressValue.frame))
        XCTAssertTrue(cardBounds.contains(progressBar.frame))
        XCTAssertFalse(app.staticTexts["New Completions"].firstMatch.exists)
        XCTAssertFalse(app.staticTexts["Previously Completed"].firstMatch.exists)
        XCTAssertFalse(app.staticTexts["Made Progress"].firstMatch.exists)
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

    private func assertExploreLocationEmptyState(_ app: XCUIApplication) {
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
        XCTAssertTrue(state.waitForExistence(timeout: 10), "Location empty state is missing")
        guard let stablePair = stableExploreLocationPair(app) else { return }

        XCTAssertEqual(
            app.descendants(matching: .any).matching(
                identifier: "explore-location-empty-title"
            ).count,
            1,
            "Location empty state must expose one stable title"
        )
        XCTAssertEqual(
            app.buttons.matching(identifier: "explore-location-primary-action").count,
            1,
            "Location empty state must expose one stable primary action"
        )
        XCTAssertTrue(
            title.label == stablePair.title,
            "Location empty-state title does not match its settled state"
        )
        XCTAssertTrue(
            primaryAction.label == stablePair.action,
            "Location primary action does not match its settled state"
        )

        let elements = [title, detail, primaryAction, browseAction]
        let tabBar = app.tabBars.firstMatch
        for element in elements {
            XCTAssertTrue(
                scrollIntoExploreViewport(element, app: app),
                "Location empty-state content is not reachable"
            )
            assertInsideFrame(element, frame: exploreVisibleContentFrame(app))
            if tabBar.exists {
                XCTAssertTrue(
                    element.frame.intersection(tabBar.frame).isEmpty,
                    "Location empty-state content intersects the tab bar"
                )
            }
        }
        XCTAssertTrue(
            settleExplorePair(detail, primaryAction, app: app),
            "Location detail and primary action cannot be shown together"
        )
        let settledFrame = exploreVisibleContentFrame(app)
        assertInsideFrame(detail, frame: settledFrame)
        assertInsideFrame(primaryAction, frame: settledFrame)
    }

    private func stableExploreLocationPair(
        _ app: XCUIApplication
    ) -> (title: String, action: String)? {
        var previousPair: (title: String, action: String)?
        for attempt in 0...10 {
            let title = app.descendants(matching: .any)[
                "explore-location-empty-title"
            ].firstMatch
            let action = app.buttons["explore-location-primary-action"].firstMatch
            let pair = (title: title.label, action: action.label)
            let isAllowed = (pair.title == "Trails near you"
                && pair.action == "Enable Location")
                || (pair.title == "Location is off"
                    && pair.action == "Open Settings")
                || (pair.title == "Location unavailable"
                    && pair.action == "Retry")
            if title.exists, action.exists, isAllowed {
                if let previousPair,
                   previousPair.title == pair.title,
                   previousPair.action == pair.action {
                    return pair
                }
                previousPair = pair
            } else {
                previousPair = nil
            }
            if attempt < 10 { sleep(1) }
        }
        XCTFail("Location empty state did not settle to an allowed title/action pair")
        return nil
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
                sleep(1)
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
        XCTAssertTrue(
            open.frame.contains(title.frame),
            "Area title extends outside its Open card"
        )
        XCTAssertTrue(
            visibleFrame.contains(title.frame),
            "Area title extends outside the Explore viewport"
        )
        XCTAssertTrue(
            title.frame.intersection(save.frame).isEmpty,
            "Area title intersects the Save control"
        )
    }

    private func assertTrailActionFrames(
        _ app: XCUIApplication,
        select: XCUIElement,
        secondary: XCUIElement,
        selectLabelPrefix: String,
        secondaryLabelPrefix: String
    ) {
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        if trailScroll.exists {
            XCTAssertTrue(
                scrollToReachable(select, in: trailScroll, app: app),
                "Trail Select action is not reachable"
            )
            XCTAssertTrue(
                scrollToReachable(secondary, in: trailScroll, app: app),
                "Trail secondary action is not reachable"
            )
        }
        XCTAssertGreaterThanOrEqual(select.frame.width, 44, "Trail Select hit width is too small")
        XCTAssertGreaterThanOrEqual(select.frame.height, 44, "Trail Select hit height is too small")
        XCTAssertGreaterThanOrEqual(
            secondary.frame.width,
            44,
            "Trail secondary hit width is too small"
        )
        XCTAssertGreaterThanOrEqual(
            secondary.frame.height,
            44,
            "Trail secondary hit height is too small"
        )
        if isAccessibilityLayout(app) {
            XCTAssertGreaterThan(select.frame.width, app.frame.width * 0.7)
            XCTAssertGreaterThan(secondary.frame.width, app.frame.width * 0.7)
        } else {
            XCTAssertLessThanOrEqual(select.frame.height, 47)
            XCTAssertLessThanOrEqual(secondary.frame.width, 47)
            XCTAssertLessThanOrEqual(secondary.frame.height, 47)
        }
        assertInsideScreen(select, app: app)
        assertInsideScreen(secondary, app: app)
        XCTAssertTrue(
            select.frame.intersection(secondary.frame).isEmpty,
            "Trail actions overlap"
        )
        let identifiersAreDistinct = select.identifier != secondary.identifier
        let labelsAreDistinct = select.label != secondary.label
        XCTAssertTrue(identifiersAreDistinct, "Trail actions must have distinct identifiers")
        XCTAssertTrue(labelsAreDistinct, "Trail actions must have distinct labels")
        XCTAssertTrue(
            select.label.hasPrefix(selectLabelPrefix),
            "Trail Select action has unexpected semantics"
        )
        XCTAssertTrue(
            secondary.label.hasPrefix(secondaryLabelPrefix),
            "Trail secondary action has unexpected semantics"
        )
    }

    private func assertCollectionRepresentative(
        _ row: XCUIElement,
        in collectionScroll: XCUIElement,
        app: XCUIApplication,
        expectedWholeWord: String
    ) {
        XCTAssertTrue(
            scrollToReachable(row, in: collectionScroll, app: app),
            "Collection representative row is not reachable"
        )
        assertInsideScreen(row, app: app)
        XCTAssertTrue(
            label(row.label, containsWholeWord: expectedWholeWord),
            "Collection representative title is incomplete"
        )
        XCTAssertGreaterThan(
            row.frame.width,
            app.frame.width * 0.7,
            "Accessibility Collection representative row is not full width"
        )
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
        XCTAssertTrue(headerScroll.waitForExistence(timeout: 10), "Recording header scroll is missing")
        XCTAssertTrue(title.waitForExistence(timeout: 10), "Recording area title is missing")

        if header.frame.intersection(app.frame).isEmpty {
            expandAreaSheet(app)
        }
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
                sleep(1)
            }
        }

        let titleIsComplete = title.label == "South Mountain Park and Preserve"
        XCTAssertTrue(titleIsComplete, "Recording area title is incomplete")
        assertInsideScreen(title, app: app)
        XCTAssertTrue(title.isHittable, "Recording area title is not reachable")
        XCTAssertGreaterThanOrEqual(
            title.frame.minY,
            header.frame.minY + 19,
            "Recording area title intersects the drag-indicator zone"
        )

        let dashboardScroll = app.scrollViews["recording-dashboard-scroll"].firstMatch
        XCTAssertTrue(
            dashboardScroll.waitForExistence(timeout: 10),
            "Accessibility recording dashboard is missing"
        )
        XCTAssertTrue(
            header.frame.intersection(dashboardScroll.frame).isEmpty,
            "Recording header intersects the dashboard"
        )
        let dashboardElements = [
            app.descendants(matching: .any)["recording-gps-status"].firstMatch,
            app.descendants(matching: .any)["recording-elevation-profile"].firstMatch,
            app.descendants(matching: .any)["recording-metrics"].firstMatch,
            app.buttons["recording-stop-button"].firstMatch,
        ]
        for element in dashboardElements {
            XCTAssertTrue(
                element.waitForExistence(timeout: 10),
                "Accessibility recording dashboard component is missing"
            )
            XCTAssertTrue(
                title.frame.intersection(element.frame).isEmpty,
                "Recording area title intersects dashboard content"
            )
        }

        let controls = app.descendants(matching: .any)["area-map-controls"].firstMatch
        XCTAssertTrue(controls.waitForExistence(timeout: 10), "Map controls are missing")
        XCTAssertGreaterThan(
            header.frame.minY - controls.frame.maxY,
            0,
            "Recording state hides the map region"
        )
        XCTAssertEqual(stopControlCount(app), 1, "Recording state must expose exactly one Stop control")
        XCTAssertEqual(app.buttons.matching(identifier: "recording-stop-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "active-recording-stop-button").count, 0)
    }

    private func assertSelectedMapFraming(_ app: XCUIApplication) {
        let controls = app.descendants(matching: .any)["area-map-controls"].firstMatch
        let sheetHeader = app.descendants(matching: .any)["area-header"].firstMatch
        XCTAssertTrue(controls.waitForExistence(timeout: 10), "Map controls are missing")
        XCTAssertTrue(sheetHeader.waitForExistence(timeout: 10), "Area sheet header is missing")
        guard controls.exists, sheetHeader.exists else { return }

        XCTAssertEqual(app.buttons.matching(identifier: "area-close-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-map-options-button").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "area-map-favorite-button").count, 1)

        let mapControls = [
            app.buttons["area-close-button"].firstMatch,
            app.buttons["area-map-options-button"].firstMatch,
            app.buttons["area-map-favorite-button"].firstMatch,
        ]
        XCTAssertEqual(
            Set(mapControls.map { $0.identifier }).count,
            mapControls.count,
            "Accessibility map controls must have distinct identifiers"
        )
        XCTAssertEqual(
            Set(mapControls.map { $0.label }).count,
            mapControls.count,
            "Accessibility map controls must have distinct labels"
        )
        for control in mapControls {
            assertInsideScreen(control, app: app)
            XCTAssertGreaterThanOrEqual(control.frame.width, 44, "Map control is below the minimum hit width")
            XCTAssertGreaterThanOrEqual(control.frame.height, 44, "Map control is below the minimum hit height")
            XCTAssertLessThanOrEqual(control.frame.width, 48, "Map control exceeds the bounded hit width")
            XCTAssertLessThanOrEqual(control.frame.height, 48, "Map control exceeds the bounded hit height")
        }
        for firstIndex in mapControls.indices {
            for secondIndex in mapControls.indices where secondIndex > firstIndex {
                XCTAssertTrue(
                    mapControls[firstIndex].frame.intersection(mapControls[secondIndex].frame).isEmpty,
                    "Accessibility map controls overlap"
                )
            }
        }

        let farMarkers = app.descendants(matching: .any)
            .matching(identifier: "map-far-access-marker")
            .allElementsBoundByIndex.filter { $0.exists }
        let visibleNearMarkers = app.descendants(matching: .any)
            .matching(identifier: "map-near-access-marker")
            .allElementsBoundByIndex.filter { $0.exists }
        let nearMarkers: [XCUIElement]
        if farMarkers.isEmpty || !visibleNearMarkers.isEmpty {
            guard let stableMarkers = stableNearMarkers(app) else { return }
            nearMarkers = stableMarkers
        } else {
            nearMarkers = []
        }
        XCTAssertGreaterThan(
            nearMarkers.count + farMarkers.count,
            0,
            "Selected map has no access marker"
        )
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
            XCTAssertTrue(isContained, "Near access marker extends outside the screen")
            XCTAssertTrue(isHittable, "Near access marker is not reachable")
            XCTAssertTrue(clearsControls, "Near access marker intersects map controls")
            XCTAssertTrue(clearsSheet, "Near access marker intersects the area sheet")
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

        if nearMarkers.isEmpty, !farMarkers.isEmpty {
            assertFarOnlyParkingDisclosure(app)
        }
    }

    private func assertFarOnlyParkingDisclosure(_ app: XCUIApplication) {
        let parkingMatches = app.descendants(matching: .any).matching(
            identifier: "selected-trail-parking-detail"
        )
        XCTAssertEqual(
            parkingMatches.count,
            1,
            "Far-only selection must expose one selected parking detail"
        )
        let parking = parkingMatches.firstMatch
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10),
              parking.waitForExistence(timeout: 10),
              scrollToReachable(parking, in: trailScroll, app: app) else {
            XCTFail("Far-only selected parking detail is not reachable")
            return
        }
        XCTAssertTrue(
            hasNearestParkingDistanceLabel(parking.label),
            "Far-only selected parking detail violates the distance contract"
        )
    }

    private func hasNearestParkingDistanceLabel(_ label: String) -> Bool {
        let pattern = "^Nearest parking: (?:.+, )?[0-9]+(?:\\.[0-9]{1,2})? (?:mi|km) away$"
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return false }
        let range = NSRange(label.startIndex..<label.endIndex, in: label)
        return expression.firstMatch(in: label, range: range) != nil
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
            let frames = markers.map(\.frame)
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

            if attempt < 9 { sleep(1) }
        }

        XCTFail("Near selected markers did not produce a stable complete frame set")
        return nil
    }

    private func openBrowseSearch(_ app: XCUIApplication) -> XCUIElement {
        let search = app.buttons["area-search-button"].firstMatch
        XCTAssertTrue(search.waitForExistence(timeout: 10), "Fit Search action is missing")
        let searchField = app.textFields["Search trails"].firstMatch
        search.tap()
        for attempt in 0...10 {
            let hasBrowseAction = app.buttons.matching(
                identifier: "trail-filter-button"
            ).count == 1 || app.buttons.matching(
                identifier: "trail-search-keyboard-done"
            ).count == 1
            let reachedBrowse = searchField.exists
                && hasBrowseAction
                && app.buttons.matching(identifier: "area-search-button").count == 0
            if reachedBrowse { return searchField }
            if attempt < 10 { sleep(1) }
        }
        XCTFail("Browse search/filter chrome did not appear after Search")
        return searchField
    }

    private func dismissSearchKeyboard(_ app: XCUIApplication) {
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5), "Search action did not focus the keyboard")
        let done = app.buttons["Dismiss Search Keyboard"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 5), "Search keyboard Done action is missing")
        done.tap()
        for _ in 0..<5 {
            if !keyboard.exists { break }
            sleep(1)
        }
        XCTAssertFalse(keyboard.exists, "Search keyboard Done action did not dismiss the keyboard")
        XCTAssertTrue(app.textFields["Search trails"].firstMatch.exists)
        XCTAssertEqual(app.buttons.matching(identifier: "trail-filter-button").count, 1)
    }

    private func expandAreaSheet(_ app: XCUIApplication) {
        let header = app.scrollViews["area-header"].firstMatch
        let anchorY = header.exists ? header.frame.minY + 8 : app.frame.height * 0.62
        let start = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: anchorY))
        let end = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: app.frame.width / 2, dy: app.frame.height * 0.2))
        start.press(forDuration: 0.1, thenDragTo: end)
        sleep(2)
    }

    private func scrollToVisible(
        _ element: XCUIElement,
        in scrollView: XCUIElement,
        app: XCUIApplication
    ) -> Bool {
        for attempt in 0...20 {
            if isOnScreen(element, app: app) { return true }
            if attempt < 20 {
                let moveContentDown = element.exists && element.frame.midY < app.frame.midY
                let start = scrollView.coordinate(
                    withNormalizedOffset: CGVector(dx: 0.5, dy: moveContentDown ? 0.35 : 0.65)
                )
                let end = scrollView.coordinate(
                    withNormalizedOffset: CGVector(dx: 0.5, dy: moveContentDown ? 0.55 : 0.45)
                )
                start.press(forDuration: 0.05, thenDragTo: end)
            }
        }
        return false
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
            }
        }
        return false
    }

    private func launchSeededApp(arguments: [String] = []) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"] + arguments
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
        let scrollView = app.scrollViews["explore-scroll"].firstMatch
        for attempt in 0...20 {
            if element.exists,
               element.isHittable,
               visibleFrame.contains(element.frame) {
                return true
            }
            if attempt < 20 {
                nudgeExploreScroll(
                    scrollView.exists ? scrollView : app,
                    towardTop: element.exists && element.frame.minY < visibleFrame.minY
                )
                sleep(1)
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

    private func assertCompleteAreaTitle(_ title: XCUIElement, app: XCUIApplication) {
        XCTAssertTrue(title.waitForExistence(timeout: 10), "Area title is missing")
        let isComplete = title.label == "South Mountain Park and Preserve"
        XCTAssertTrue(isComplete, "Area title is incomplete")
        let isAccessibility = title.frame.height > 80
            || app.launchArguments.contains("UICTContentSizeCategoryAccessibilityXXXL")
        if !isAccessibility {
            XCTAssertLessThanOrEqual(title.frame.height, 52, "Area title exceeds two standard lines")
        }
    }

    private func assertInsideFrame(_ element: XCUIElement, frame: CGRect) {
        XCTAssertTrue(element.exists, "Explore control is missing")
        XCTAssertTrue(element.isHittable, "Explore control is not hittable")
        XCTAssertTrue(frame.contains(element.frame), "Explore control is outside the visible viewport")
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
