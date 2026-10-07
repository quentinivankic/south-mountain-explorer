import XCTest

final class FieldTrustAccessibilityTests: XCTestCase {

    private let areaId = "south-mountain-park-and-preserve-az"

    private struct ScrollVisibilityResult {
        let isVisible: Bool
        let didScroll: Bool
    }

    private enum KnownTargetPosition {
        case earlier
        case later
    }

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
        assertNavigationRegressionControls()
        let app = launchSeededApp(arguments: ["--uitest-completed", "0"])
        let continueButton = app.buttons["continue-card"].firstMatch
        XCTAssertTrue(continueButton.waitForExistence(timeout: 30))
        continueButton.tap()
        XCTAssertTrue(app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60))
        _ = openBrowseSearch(app)
        dismissSearchKeyboard(app)

        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.waitForExistence(timeout: 10), "Trail list scroll is missing")
        let secondaryCandidate = app.descendants(matching: .any).matching(NSPredicate(
            format: "identifier BEGINSWITH %@ AND label BEGINSWITH %@",
            "trail-secondary-",
            "Mark Trail Complete,"
        )).firstMatch
        XCTAssertTrue(
            scrollToReachable(secondaryCandidate, in: trailScroll, app: app),
            "No incomplete trail action appeared"
        )
        let secondaryIdentifier = secondaryCandidate.identifier
        guard let pair = revealPairedIncompleteTrailActions(
            app,
            secondaryIdentifier: secondaryIdentifier,
            in: trailScroll
        ) else {
            XCTFail("No paired semantic trail actions appeared")
            return
        }
        let selectIdentifier = pair.select.identifier
        assertTrailActionFrames(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            selectLabelPrefix: "Select Trail,",
            secondaryLabelPrefix: "Mark Trail Complete,"
        )
        let trailName = pair.subject
        let toggledPrefix = "Mark Trail Incomplete,"

        pair.secondary.tap()
        let toggledSecondary = trailAction(app, identifier: secondaryIdentifier)
        XCTAssertTrue(
            waitForLabelPrefix(toggledPrefix, element: toggledSecondary),
            "Completion action did not toggle completion"
        )
        XCTAssertTrue(
            toggledSecondary.label == "\(toggledPrefix) \(trailName)",
            "Completion action has unexpected toggled semantics"
        )
        let inactiveSelect = trailAction(app, identifier: selectIdentifier)
        XCTAssertTrue(
            inactiveSelect.label == "Select Trail, \(trailName)",
            "Mark Complete selected the trail"
        )

        pair.select.tap()
        guard let selectedPair = waitForSelectedTrailActions(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            subject: trailName
        ) else {
            XCTFail("Trail Select action did not settle exact selected semantics")
            return
        }
        let selectedControl = selectedPair.select
        let recordControl = selectedPair.secondary
        XCTAssertTrue(
            selectedControl.label == "Deselect Trail, \(trailName)",
            "Selected trail action has unexpected semantics"
        )
        XCTAssertTrue(
            recordControl.label == "Record Trail, \(trailName)",
            "Selected trail secondary action has unexpected semantics"
        )
        assertTrailActionFrames(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            selectLabelPrefix: "Deselect Trail,",
            secondaryLabelPrefix: "Record Trail,"
        )
        sleep(3)
        assertSelectedMapFraming(app)

        guard let freshDeselect = freshDeselectActionAfterLowerContent(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            subject: trailName,
            in: trailScroll
        ) else {
            XCTFail("Selected trail action was not restored after parking proof")
            return
        }
        XCTAssertTrue(
            freshDeselect.label == "Deselect Trail, \(trailName)",
            "Restored trail action has unexpected selected semantics"
        )
        freshDeselect.tap()
        guard waitForDeselectedTrailActions(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            subject: trailName,
            completionLabel: "\(toggledPrefix) \(trailName)"
        ) else {
            XCTFail("Trail deselection did not settle exact idle semantics")
            return
        }
        let finalSelectMatches = trailActionMatches(
            app,
            identifier: selectIdentifier
        )
        let finalSecondaryMatches = trailActionMatches(
            app,
            identifier: secondaryIdentifier
        )
        guard let finalSelect = uniqueExistingElement(finalSelectMatches),
              let finalSecondary = uniqueExistingElement(finalSecondaryMatches) else {
            XCTFail("Final retained trail actions are not uniquely materialized")
            return
        }
        XCTAssertTrue(
            finalSelect.label == "Select Trail, \(trailName)",
            "Final trail action has unexpected unselected semantics"
        )
        XCTAssertTrue(
            finalSecondary.label == "\(toggledPrefix) \(trailName)",
            "Final completion action did not preserve exact semantics"
        )
    }

    func testAreaSheetAndCollectionAdaptAtAccessibilitySize() {
        assertNavigationRegressionControls()
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

        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.waitForExistence(timeout: 10), "Trail list scroll is missing")
        let secondaryCandidate = app.descendants(matching: .any).matching(NSPredicate(
            format: "identifier BEGINSWITH %@ AND label BEGINSWITH %@",
            "trail-secondary-",
            "Mark Trail Complete,"
        )).firstMatch
        XCTAssertTrue(
            scrollToReachable(secondaryCandidate, in: trailScroll, app: app),
            "No incomplete trail action appeared"
        )
        let secondaryIdentifier = secondaryCandidate.identifier
        guard let pair = revealPairedIncompleteTrailActions(
            app,
            secondaryIdentifier: secondaryIdentifier,
            in: trailScroll
        ) else {
            XCTFail("No paired semantic trail actions appeared")
            return
        }
        let selectIdentifier = pair.select.identifier
        let suffix = String(selectIdentifier.dropFirst("trail-select-".count))
        assertTrailActionFrames(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            selectLabelPrefix: "Select Trail,",
            secondaryLabelPrefix: "Mark Trail Complete,"
        )
        let trailName = pair.subject
        let initialSecondaryLabel = pair.secondary.label

        pair.select.tap()
        guard let selectedPair = waitForSelectedTrailActions(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            subject: trailName
        ) else {
            XCTFail("Trail Select action did not settle exact selected semantics")
            return
        }
        let selectedControl = selectedPair.select
        let recordControl = selectedPair.secondary
        XCTAssertTrue(
            selectedControl.label == "Deselect Trail, \(trailName)",
            "Selected trail action has unexpected semantics"
        )
        XCTAssertTrue(
            recordControl.label == "Record Trail, \(trailName)",
            "Selected trail secondary action has unexpected semantics"
        )
        assertTrailActionFrames(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            selectLabelPrefix: "Deselect Trail,",
            secondaryLabelPrefix: "Record Trail,"
        )
        sleep(3)

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
            max(selectedControl.frame.maxY, recordControl.frame.maxY) - 1,
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

        // The selected actions and profile are verified before this single
        // downward pass through profile and informational parking content.
        XCTAssertTrue(scrollToReachable(flip, in: trailScroll, app: app))
        assertInsideScreen(flip, app: app)
        XCTAssertTrue(
            scrollToVisible(identifier: "trail-profile-range", in: trailScroll, app: app)
        )
        let visibleRangeMatches = app.descendants(matching: .any).matching(
            identifier: "trail-profile-range"
        )
        guard let visibleRange = uniqueExistingElement(visibleRangeMatches) else {
            XCTFail("Profile range is not uniquely materialized")
            return
        }
        assertInsideScreen(visibleRange, app: app)
        XCTAssertTrue(
            scrollToVisible(
                identifier: "selected-trail-parking-detail",
                in: trailScroll,
                app: app
            )
        )
        guard let parking = exactContainedParkingDetail(
            app,
            in: trailScroll
        ) else {
            return
        }
        let settledRangeMatches = app.descendants(matching: .any).matching(
            identifier: "trail-profile-range"
        )
        guard let settledRange = uniqueExistingElement(settledRangeMatches) else {
            XCTFail("Profile range disappeared before parking overlap proof")
            return
        }
        XCTAssertTrue(
            settledRange.frame.intersection(parking.frame).isEmpty,
            "Profile range overlaps selected parking content"
        )

        // The far-only map contract can now inspect the same visible parking
        // detail without moving a lazily absent profile target farther away.
        assertSelectedMapFraming(app)
        guard let freshDeselect = freshDeselectActionAfterLowerContent(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            subject: trailName,
            in: trailScroll
        ) else {
            XCTFail("Selected trail action was not restored after lower-content proof")
            return
        }
        XCTAssertTrue(
            freshDeselect.label == "Deselect Trail, \(trailName)",
            "Restored trail action has unexpected selected semantics"
        )
        freshDeselect.tap()
        guard waitForDeselectedTrailActions(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            subject: trailName,
            completionLabel: initialSecondaryLabel
        ) else {
            XCTFail("Trail deselection did not settle exact idle semantics")
            return
        }
        let deselectedMatches = trailActionMatches(
            app,
            identifier: selectIdentifier
        )
        let deselectedSecondaryMatches = trailActionMatches(
            app,
            identifier: secondaryIdentifier
        )
        guard let deselectedControl = uniqueExistingElement(deselectedMatches),
              let deselectedSecondary = uniqueExistingElement(
                deselectedSecondaryMatches
              ) else {
            XCTFail("Deselected retained trail actions are not uniquely materialized")
            return
        }
        XCTAssertTrue(
            deselectedControl.label == "Select Trail, \(trailName)",
            "Deselected trail action has unexpected semantics"
        )
        XCTAssertTrue(
            deselectedSecondary.label == initialSecondaryLabel,
            "Deselecting changed the exact completion action semantics"
        )

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
        XCTAssertTrue(
            scrollToVisible(identifier: "recording-gps-status", in: dashboardScroll, app: app)
        )
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
        XCTAssertTrue(
            scrollToVisible(
                identifier: "recording-elevation-summary",
                in: reopenedDashboardScroll,
                app: app
            )
        )
        assertInsideScreen(elevation, app: app)
        let metrics = app.descendants(matching: .any)["recording-metrics"].firstMatch
        XCTAssertTrue(
            scrollToVisible(
                identifier: "recording-metrics",
                in: reopenedDashboardScroll,
                app: app
            )
        )
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
        XCTAssertTrue(
            scrollToVisible(
                identifier: "recording-summary-stat-distance",
                in: summaryScroll,
                app: app
            )
        )
        assertInsideScreen(distanceRow, app: app)
        XCTAssertTrue(
            scrollToVisible(
                identifier: "recording-summary-stat-duration",
                in: summaryScroll,
                app: app
            )
        )
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
            scrollToVisible(
                identifier: "recording-summary-area-progress",
                in: summaryScroll,
                app: app
            ),
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
    ) -> (select: XCUIElement, secondary: XCUIElement, subject: String)? {
        guard secondaryIdentifier.hasPrefix("trail-secondary-") else { return nil }
        let suffix = String(secondaryIdentifier.dropFirst("trail-secondary-".count))
        let selectIdentifier = "trail-select-\(suffix)"

        for attempt in 0...20 {
            let selectMatches = app.descendants(matching: .any).matching(
                identifier: selectIdentifier
            )
            let secondaryMatches = app.descendants(matching: .any).matching(
                identifier: secondaryIdentifier
            )
            if let select = uniqueExistingElement(selectMatches),
               let secondary = uniqueExistingElement(secondaryMatches) {
                let subject = actionSubject(select.label, after: "Select Trail,")
                let pairIsReachable = isOnScreenAndHittable(select, app: app)
                    && isOnScreenAndHittable(secondary, app: app)
                    && select.frame.width >= 44
                    && select.frame.height >= 44
                    && secondary.frame.width >= 44
                    && secondary.frame.height >= 44
                    && select.identifier != secondary.identifier
                    && select.label != secondary.label
                    && select.frame.intersection(secondary.frame).isEmpty
                    && subject != nil
                    && secondary.label == "Mark Trail Complete, \(subject ?? "")"
                if pairIsReachable, let subject {
                    return (select, secondary, subject)
                }
            }
            if attempt < 20 {
                let start = trailScroll.coordinate(
                    withNormalizedOffset: CGVector(dx: 0.5, dy: 0.35)
                )
                let end = trailScroll.coordinate(
                    withNormalizedOffset: CGVector(dx: 0.5, dy: 0.55)
                )
                start.press(forDuration: 0.05, thenDragTo: end)
                sleep(1)
            }
        }
        return nil
    }

    private func waitForSelectedTrailActions(
        _ app: XCUIApplication,
        selectIdentifier: String,
        secondaryIdentifier: String,
        subject: String
    ) -> (select: XCUIElement, secondary: XCUIElement)? {
        for attempt in 0...5 {
            let selectMatches = app.descendants(matching: .any).matching(
                identifier: selectIdentifier
            )
            let secondaryMatches = app.descendants(matching: .any).matching(
                identifier: secondaryIdentifier
            )
            let select = selectMatches.firstMatch
            let secondary = secondaryMatches.firstMatch
            if selectMatches.count == 1,
               secondaryMatches.count == 1,
               select.exists,
               secondary.exists,
               select.label == "Deselect Trail, \(subject)",
               secondary.label == "Record Trail, \(subject)" {
                return (select, secondary)
            }
            if attempt < 5 { sleep(1) }
        }
        return nil
    }

    private func waitForDeselectedTrailActions(
        _ app: XCUIApplication,
        selectIdentifier: String,
        secondaryIdentifier: String,
        subject: String,
        completionLabel: String
    ) -> Bool {
        for attempt in 0...5 {
            let selectMatches = app.descendants(matching: .any).matching(
                identifier: selectIdentifier
            )
            let secondaryMatches = app.descendants(matching: .any).matching(
                identifier: secondaryIdentifier
            )
            let select = selectMatches.firstMatch
            let secondary = secondaryMatches.firstMatch
            let record = app.buttons["area-record-button"].firstMatch
            let settled = selectMatches.count == 1
                && secondaryMatches.count == 1
                && select.exists
                && secondary.exists
                && select.label == "Select Trail, \(subject)"
                && secondary.label == completionLabel
                && record.exists
                && record.label == "Start a hike"
            if settled { return true }
            if attempt < 5 { sleep(1) }
        }
        return false
    }

    private func assertTrailActionFrames(
        _ app: XCUIApplication,
        selectIdentifier: String,
        secondaryIdentifier: String,
        selectLabelPrefix: String,
        secondaryLabelPrefix: String
    ) {
        let selectMatches = trailActionMatches(app, identifier: selectIdentifier)
        let secondaryMatches = trailActionMatches(app, identifier: secondaryIdentifier)
        let select = selectMatches.firstMatch
        let secondary = secondaryMatches.firstMatch
        XCTAssertTrue(select.waitForExistence(timeout: 10), "Trail Select action is missing")
        XCTAssertTrue(secondary.waitForExistence(timeout: 10), "Trail secondary action is missing")
        XCTAssertEqual(selectMatches.count, 1, "Trail Select action must be unique")
        XCTAssertEqual(secondaryMatches.count, 1, "Trail secondary action must be unique")

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
        XCTAssertTrue(select.isHittable, "Trail Select action is not hittable")
        XCTAssertTrue(secondary.isHittable, "Trail secondary action is not hittable")
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

    private func restoreSelectedTrailActionAfterLowerContent(
        _ app: XCUIApplication,
        identifier: String,
        in trailScroll: XCUIElement
    ) -> XCUIElement? {
        guard pairedTrailIdentifiers(for: identifier) != nil else { return nil }

        // Lower profile/parking proof places this known selected row earlier
        // than the viewport. Restore only its primary Deselect action; the
        // vertically stacked Record action is proved independently below.
        for attempt in 0...20 {
            let selectedMatches = trailActionMatches(app, identifier: identifier)
            if let selected = uniqueExistingElement(selectedMatches) {
                let label = selected.label
                let frame = selected.frame
                let isHittable = selected.isHittable
                if primaryDeselectIsReady(
                    label: label,
                    frame: frame,
                    appFrame: app.frame,
                    isHittable: isHittable
                ) {
                    return selected
                }
            }
            if attempt < 20 {
                performKnownTargetRecovery(.earlier, in: trailScroll)
                sleep(1)
            }
        }
        return nil
    }

    private func proveSelectedTrailContextBeforeDeselect(
        _ app: XCUIApplication,
        secondaryIdentifier: String,
        profileIdentifier: String,
        subject: String,
        in trailScroll: XCUIElement
    ) -> Bool {
        var secondaryWasProved = false
        var profileWasProved = false

        for attempt in 0...20 {
            if !secondaryWasProved {
                let secondaryMatches = trailActionMatches(
                    app,
                    identifier: secondaryIdentifier
                )
                if let secondary = uniqueExistingElement(secondaryMatches) {
                    secondaryWasProved = secondary.label == "Record Trail, \(subject)"
                }
            }
            if !profileWasProved {
                let profileMatches = app.descendants(matching: .any).matching(
                    identifier: profileIdentifier
                )
                profileWasProved = uniqueExistingElement(profileMatches) != nil
            }
            if secondaryWasProved, profileWasProved { return true }

            if attempt < 20 {
                performKnownTargetRecovery(.later, in: trailScroll)
                sleep(1)
            }
        }
        return false
    }

    private func freshDeselectActionAfterLowerContent(
        _ app: XCUIApplication,
        selectIdentifier: String,
        secondaryIdentifier: String,
        subject: String,
        in trailScroll: XCUIElement
    ) -> XCUIElement? {
        guard let identifiers = pairedTrailIdentifiers(for: selectIdentifier),
              identifiers.secondary == secondaryIdentifier,
              restoreSelectedTrailActionAfterLowerContent(
                app,
                identifier: selectIdentifier,
                in: trailScroll
              ) != nil,
              proveSelectedTrailContextBeforeDeselect(
                app,
                secondaryIdentifier: secondaryIdentifier,
                profileIdentifier: identifiers.profile,
                subject: subject,
                in: trailScroll
              ),
              restoreSelectedTrailActionAfterLowerContent(
                app,
                identifier: selectIdentifier,
                in: trailScroll
              ) != nil else {
            return nil
        }

        // Every element used by the final state proof is freshly queried after
        // the last recovery gesture. Record need not be simultaneously hittable.
        let freshMatches = trailActionMatches(app, identifier: selectIdentifier)
        let freshSecondaryMatches = trailActionMatches(
            app,
            identifier: secondaryIdentifier
        )
        let freshProfileMatches = app.descendants(matching: .any).matching(
            identifier: identifiers.profile
        )
        guard let freshDeselect = uniqueExistingElement(freshMatches),
              let freshSecondary = uniqueExistingElement(freshSecondaryMatches),
              uniqueExistingElement(freshProfileMatches) != nil else {
            return nil
        }
        let label = freshDeselect.label
        let frame = freshDeselect.frame
        let isHittable = freshDeselect.isHittable
        let secondaryLabel = freshSecondary.label
        guard label == "Deselect Trail, \(subject)",
              secondaryLabel == "Record Trail, \(subject)",
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
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10),
              scrollToVisible(
                identifier: "selected-trail-parking-detail",
                in: trailScroll,
                app: app
              ),
              exactContainedParkingDetail(
                app,
                in: trailScroll
              ) != nil else {
            XCTFail("Far-only selected parking detail is not visible")
            return
        }
    }

    private func exactContainedParkingDetail(
        _ app: XCUIApplication,
        in trailScroll: XCUIElement
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
        let hasExactDistance = hasNearestParkingDistanceLabel(label)
        XCTAssertTrue(
            isInformationalText,
            "Selected parking detail must remain informational text"
        )
        XCTAssertTrue(
            isFullyContained,
            "Selected parking detail extends outside the visible trail list"
        )
        XCTAssertTrue(
            hasExactDistance,
            "Selected parking detail violates the distance contract"
        )
        guard isInformationalText, isFullyContained, hasExactDistance else { return nil }
        return parking
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
        var previousCorrection: CGFloat?
        var didScroll = false

        for attempt in 0...20 {
            let viewport = scrollView.frame.intersection(app.frame)
            guard !viewport.isNull, viewport.width > 0, viewport.height > 0 else {
                return ScrollVisibilityResult(isVisible: false, didScroll: didScroll)
            }

            let matches = app.descendants(matching: .any).matching(
                identifier: identifier
            )
            let element = uniqueExistingElement(matches)
            let elementFrame: CGRect?
            if let element {
                let frame = element.frame
                if viewport.contains(frame) {
                    return ScrollVisibilityResult(isVisible: true, didScroll: didScroll)
                }
                elementFrame = frame
            } else {
                // Selected profile and parking content is later in its known row.
                elementFrame = nil
            }

            guard attempt < 20 else { break }
            let correction = parkingContainmentCorrection(
                elementFrame: elementFrame,
                viewport: viewport,
                previousCorrection: previousCorrection
            )
            guard correction != 0 else { break }
            performLowMomentumContentCorrection(
                correction,
                in: scrollView,
                viewport: viewport
            )
            previousCorrection = correction
            didScroll = true
            sleep(1)
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

    private func performLowMomentumContentCorrection(
        _ correction: CGFloat,
        in scrollView: XCUIElement,
        viewport: CGRect
    ) {
        let gestureFrame = viewport.insetBy(dx: 8, dy: 8)
        let scrollFrame = scrollView.frame
        guard gestureFrame.width > 0,
              gestureFrame.height > 0,
              scrollFrame.width > 0,
              scrollFrame.height > 0 else {
            return
        }
        let distance = min(abs(correction), gestureFrame.height * 0.45)
        guard distance > 0 else { return }
        let direction: CGFloat = correction < 0 ? -1 : 1
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
            return (0.35, 0.55)
        case .later:
            return (0.80, 0.20)
        }
    }

    private func performKnownTargetRecovery(
        _ position: KnownTargetPosition,
        in trailScroll: XCUIElement
    ) {
        let offsets = knownTargetGestureOffsets(position)
        let start = trailScroll.coordinate(
            withNormalizedOffset: CGVector(dx: 0.5, dy: offsets.start)
        )
        let end = trailScroll.coordinate(
            withNormalizedOffset: CGVector(dx: 0.5, dy: offsets.end)
        )
        start.press(
            forDuration: 0.15,
            thenDragTo: end,
            withVelocity: .slow,
            thenHoldForDuration: 0.10
        )
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

    private func assertNavigationRegressionControls() {
        XCTAssertFalse(
            exactMatchAllowsPropertyRead(matchCount: 0, exists: false),
            "An unmatched query must not permit element property access"
        )
        XCTAssertFalse(
            exactMatchAllowsPropertyRead(matchCount: 2, exists: true),
            "A non-unique query must not permit element property access"
        )
        XCTAssertTrue(
            exactMatchAllowsPropertyRead(matchCount: 1, exists: true),
            "A unique existing query must permit guarded property access"
        )

        let earlier = knownTargetGestureOffsets(.earlier)
        let repeatedEarlier = knownTargetGestureOffsets(.earlier)
        let later = knownTargetGestureOffsets(.later)
        XCTAssertGreaterThan(
            earlier.end - earlier.start,
            0,
            "A known earlier target must move content down"
        )
        XCTAssertLessThan(
            later.end - later.start,
            0,
            "A known later target must move content up"
        )
        XCTAssertEqual(earlier.start, repeatedEarlier.start)
        XCTAssertEqual(earlier.end, repeatedEarlier.end)

        let viewport = CGRect(x: 0, y: 0, width: 100, height: 100)
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
        XCTAssertLessThan(below, 0, "Bottom overflow must move content up")
        XCTAssertGreaterThan(crossedAbove, 0, "Top overflow must reverse direction")
        XCTAssertLessThan(
            abs(crossedAbove),
            abs(below),
            "A correction must shrink after crossing the visible interval"
        )

        XCTAssertTrue(
            primaryDeselectIsReady(
                label: "Deselect Trail, Regression Trail",
                frame: CGRect(x: 10, y: 10, width: 80, height: 44),
                appFrame: viewport,
                isHittable: true
            ),
            "Primary restoration must not require secondary hittability"
        )

        let retained = pairedTrailIdentifiers(
            for: "trail-select-regression-trail"
        )
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
