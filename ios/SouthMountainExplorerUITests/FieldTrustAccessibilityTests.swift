import XCTest

final class FieldTrustAccessibilityTests: XCTestCase {

    private let areaId = "south-mountain-park-and-preserve-az"

    private struct ScrollVisibilityResult {
        let isVisible: Bool
        let didScroll: Bool
    }

    private struct TrailActionProof {
        let selectIdentifier: String
        let secondaryIdentifier: String
        let subject: String
        let idleSecondaryLabel: String
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

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
        addUIInterruptionMonitor(withDescription: "Unexpected system alert") { _ in
            XCTFail("AUDIT[unexpected-system-alert]")
            return true
        }
    }

    func testAreaCardOpenAndSaveAreIndependentButtons() {
        guard let app = launchSeededApp() else { return }
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
        guard assertAuditedAppEnvironment(app) else { return }
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
        guard let app = launchSeededApp(arguments: ["--uitest-completed", "0"]) else {
            return
        }
        let continueButton = app.buttons["continue-card"].firstMatch
        XCTAssertTrue(continueButton.waitForExistence(timeout: 30))
        continueButton.tap()
        XCTAssertTrue(app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60))
        _ = openBrowseSearch(app)
        dismissSearchKeyboard(app)

        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        XCTAssertTrue(trailScroll.waitForExistence(timeout: 10), "Trail list scroll is missing")
        guard let secondaryIdentifier = firstReachableIncompleteSecondaryIdentifier(
            app,
            in: trailScroll
        ) else {
            XCTFail("No incomplete trail action appeared")
            return
        }
        guard let pair = revealPairedIncompleteTrailActions(
            app,
            secondaryIdentifier: secondaryIdentifier,
            in: trailScroll
        ) else {
            XCTFail("No paired semantic trail actions appeared")
            return
        }
        let selectIdentifier = pair.selectIdentifier
        assertTrailActionFrames(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            selectLabelPrefix: "Select Trail,",
            secondaryLabelPrefix: "Mark Trail Complete,"
        )
        let trailName = pair.subject
        let toggledPrefix = "Mark Trail Incomplete,"

        guard let freshCompletion = reachTrailAction(
            app,
            identifier: secondaryIdentifier,
            toward: .later,
            in: trailScroll
        ) else {
            XCTFail("Completion action is not reachable immediately before tapping")
            return
        }
        freshCompletion.tap()
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

        guard let freshSelect = reachTrailAction(
            app,
            identifier: selectIdentifier,
            toward: .earlier,
            in: trailScroll
        ) else {
            XCTFail("Trail Select action is not reachable immediately before tapping")
            return
        }
        freshSelect.tap()
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
        guard assertAuditedAppEnvironment(app) else { return }

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
        guard let secondaryIdentifier = firstReachableIncompleteSecondaryIdentifier(
            app,
            in: trailScroll
        ) else {
            XCTFail("No incomplete trail action appeared")
            return
        }
        guard let pair = revealPairedIncompleteTrailActions(
            app,
            secondaryIdentifier: secondaryIdentifier,
            in: trailScroll
        ) else {
            XCTFail("No paired semantic trail actions appeared")
            return
        }
        let selectIdentifier = pair.selectIdentifier
        let suffix = String(selectIdentifier.dropFirst("trail-select-".count))
        assertTrailActionFrames(
            app,
            selectIdentifier: selectIdentifier,
            secondaryIdentifier: secondaryIdentifier,
            selectLabelPrefix: "Select Trail,",
            secondaryLabelPrefix: "Mark Trail Complete,"
        )
        let trailName = pair.subject
        let initialSecondaryLabel = pair.idleSecondaryLabel

        guard let freshSelect = reachTrailAction(
            app,
            identifier: selectIdentifier,
            toward: .earlier,
            in: trailScroll
        ) else {
            XCTFail("Trail Select action is not reachable immediately before tapping")
            return
        }
        freshSelect.tap()
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

        // Derive the exact parking copy from the stable map-marker proof
        // before moving through the selected row's lower content.
        guard let parkingExpectation = assertSelectedMapFraming(
            app,
            provesFarDisclosure: false
        ) else {
            return
        }

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
            in: trailScroll,
            expectation: parkingExpectation
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
        assertNavigationRegressionControls()
        let app = XCUIApplication()
        app.launchArguments = [
            "--uitest-seed",
            "--uitest-recording-gap",
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryAccessibilityXXXL",
        ]
        app.launch()
        guard assertAuditedAppEnvironment(app) else { return }

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
            scrollToReachable(status, in: dashboardScroll, app: app),
            "Recording GPS status is not reachable"
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
        XCTAssertLessThanOrEqual(
            lowerContent.frame.maxY,
            app.frame.maxY + 1,
            "Summary Area Progress card extends below the physical screen"
        )
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

    private func firstReachableIncompleteSecondaryIdentifier(
        _ app: XCUIApplication,
        in trailScroll: XCUIElement
    ) -> String? {
        for attempt in 0...20 {
            let candidates = app.descendants(matching: .any).matching(NSPredicate(
                format: "identifier BEGINSWITH %@ AND label BEGINSWITH %@",
                "trail-secondary-",
                "Mark Trail Complete,"
            )).allElementsBoundByIndex.filter { $0.exists }
            let viewport = trailScroll.frame.intersection(app.frame)
            if let candidate = candidates.first(where: {
                actionIsReachable(
                    frame: $0.frame,
                    viewport: viewport,
                    isHittable: $0.isHittable
                )
            }) {
                return candidate.identifier
            }
            guard attempt < 20,
                  performRowTraversal(.later, in: trailScroll, app: app) else {
                return nil
            }
        }
        return nil
    }

    private func revealPairedIncompleteTrailActions(
        _ app: XCUIApplication,
        secondaryIdentifier: String,
        in trailScroll: XCUIElement
    ) -> TrailActionProof? {
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
                    return TrailActionProof(
                        selectIdentifier: selectIdentifier,
                        secondaryIdentifier: secondaryIdentifier,
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
        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10) else { return false }

        for attempt in 0...20 {
            let selectMatches = trailActionMatches(app, identifier: selectIdentifier)
            let secondaryMatches = trailActionMatches(app, identifier: secondaryIdentifier)
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
                    labelsSettled = select.identifier == selectIdentifier
                        && secondary.identifier == secondaryIdentifier
                        && select.label == "Select Trail, \(subject)"
                        && secondary.label == completionLabel
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
                if attempt < 20 { sleep(1) }
            case .macroLater:
                guard attempt < 20,
                      performRowTraversal(.later, in: trailScroll, app: app) else {
                    return false
                }
            case .microLater:
                guard attempt < 20,
                      performMeasuredMicroCorrection(
                        identifier: selectIdentifier,
                        toward: .later,
                        in: trailScroll,
                        app: app
                      ) else {
                    return false
                }
            case .microEarlier:
                guard attempt < 20,
                      performMeasuredMicroCorrection(
                        identifier: secondaryIdentifier,
                        toward: .earlier,
                        in: trailScroll,
                        app: app
                      ) else {
                    return false
                }
            }
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
        guard selectIdentifier.hasPrefix("trail-select-"),
              secondaryIdentifier.hasPrefix("trail-secondary-"),
              String(selectIdentifier.dropFirst("trail-select-".count))
                == String(secondaryIdentifier.dropFirst("trail-secondary-".count)) else {
            XCTFail("Paired trail actions do not share an exact suffix")
            return
        }
        let selectMatches = trailActionMatches(app, identifier: selectIdentifier)
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
        XCTAssertGreaterThanOrEqual(selectFrame.width, 44, "Trail Select hit width is too small")
        XCTAssertGreaterThanOrEqual(selectFrame.height, 44, "Trail Select hit height is too small")
        XCTAssertGreaterThanOrEqual(secondaryFrame.width, 44, "Trail secondary hit width is too small")
        XCTAssertGreaterThanOrEqual(secondaryFrame.height, 44, "Trail secondary hit height is too small")
        if isAccessibilityLayout(app) {
            XCTAssertGreaterThan(selectFrame.width, app.frame.width * 0.7)
            XCTAssertGreaterThan(secondaryFrame.width, app.frame.width * 0.7)
        } else {
            XCTAssertLessThanOrEqual(selectFrame.height, 47)
            XCTAssertLessThanOrEqual(secondaryFrame.width, 47)
            XCTAssertLessThanOrEqual(secondaryFrame.height, 47)
        }
        XCTAssertTrue(selectFrame.intersection(secondaryFrame).isEmpty, "Trail actions overlap")
        XCTAssertTrue(select.identifier != secondary.identifier, "Trail actions must have distinct identifiers")
        XCTAssertTrue(select.label != secondary.label, "Trail actions must have distinct labels")

        let trailScroll = app.scrollViews["trail-list-scroll"].firstMatch
        guard trailScroll.waitForExistence(timeout: 10),
              let reachedSelect = reachTrailAction(
                app,
                identifier: selectIdentifier,
                toward: .earlier,
                in: trailScroll
              ) else {
            XCTFail("Trail Select action is not independently reachable")
            return
        }
        assertInsideScreen(reachedSelect, app: app)
        XCTAssertTrue(reachedSelect.isHittable, "Trail Select action is not hittable")

        guard let reachedSecondary = reachTrailAction(
            app,
            identifier: secondaryIdentifier,
            toward: .later,
            in: trailScroll
        ) else {
            XCTFail("Trail secondary action is not independently reachable")
            return
        }
        assertInsideScreen(reachedSecondary, app: app)
        XCTAssertTrue(reachedSecondary.isHittable, "Trail secondary action is not hittable")
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
        secondaryIdentifier: String,
        profileIdentifier: String,
        subject: String,
        in trailScroll: XCUIElement
    ) -> Bool {
        guard let secondary = reachTrailAction(
            app,
            identifier: secondaryIdentifier,
            toward: .later,
            in: trailScroll
        ),
              secondary.label == "Record Trail, \(subject)" else {
            return false
        }

        for attempt in 0...20 {
            let profileMatches = app.descendants(matching: .any).matching(
                identifier: profileIdentifier
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

        // Reacquire only the exact action that will be tapped after the final
        // recovery gesture; the paired Record/profile proofs are value latches.
        let freshMatches = trailActionMatches(app, identifier: selectIdentifier)
        guard let freshDeselect = uniqueExistingElement(freshMatches) else {
            return nil
        }
        let label = freshDeselect.label
        let frame = freshDeselect.frame
        let isHittable = freshDeselect.isHittable
        guard label == "Deselect Trail, \(subject)",
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
            guard let stableMarkers = stableNearMarkers(app) else { return nil }
            nearMarkers = stableMarkers
        } else {
            nearMarkers = []
        }
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
            sleep(1)

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
            sleep(1)
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
            sleep(1)
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

    private func actionIsReturnable(
        frame: CGRect,
        appFrame: CGRect,
        isHittable: Bool
    ) -> Bool {
        isHittable && appFrame.insetBy(dx: -1, dy: -1).contains(frame)
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
                let isHittable = action.isHittable
                let independentlyReachable = actionIsReachable(
                    frame: frame,
                    viewport: viewport,
                    isHittable: isHittable
                )
                if independentlyReachable,
                   actionIsReturnable(
                       frame: frame,
                       appFrame: app.frame,
                       isHittable: isHittable
                   ) {
                    return action
                }
                guard attempt < 20 else { break }
                let directionFrame = independentlyReachable ? app.frame : viewport
                let direction: KnownTargetPosition = frame.minY < directionFrame.minY
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
        let actionAppFrame = CGRect(x: 0, y: 0, width: 440, height: 956)
        XCTAssertFalse(
            actionIsReturnable(
                frame: tallSelect,
                appFrame: actionAppFrame,
                isHittable: true
            )
        )
        XCTAssertTrue(
            actionIsReturnable(
                frame: tallSelect.offsetBy(dx: 0, dy: 80),
                appFrame: actionAppFrame,
                isHittable: true
            )
        )

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
            }
        }
        return false
    }

    private func launchSeededApp(arguments: [String] = []) -> XCUIApplication? {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"] + arguments
        app.launch()
        guard assertAuditedAppEnvironment(app) else { return nil }
        _ = app.tabBars.buttons["Explore"].waitForExistence(timeout: 30)
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
