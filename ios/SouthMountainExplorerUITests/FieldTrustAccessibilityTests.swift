import XCTest

final class FieldTrustAccessibilityTests: XCTestCase {

    private let areaId = "south-mountain-park-and-preserve-az"

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    func testAreaCardOpenAndSaveAreIndependentButtons() {
        let app = launchSeededApp()

        let continueButton = app.buttons["continue-card"].firstMatch
        XCTAssertTrue(continueButton.waitForExistence(timeout: 30))
        XCTAssertTrue(continueButton.label.hasPrefix("Open Area,"))
        assertInsideScreen(continueButton, app: app)

        let open = findByScrolling(app.buttons["area-open-\(areaId)"].firstMatch, in: app)
        let save = app.buttons["area-save-\(areaId)"].firstMatch
        XCTAssertTrue(open.exists, "Area Open button is missing")
        XCTAssertTrue(save.exists, "Area Save button is missing")
        XCTAssertNotEqual(open.identifier, save.identifier)
        XCTAssertNotEqual(open.label, save.label)
        XCTAssertTrue(open.label.hasPrefix("Open Area,"))
        XCTAssertEqual(save.label, "Remove from Saved Areas")
        assertInsideScreen(open, app: app)
        assertInsideScreen(save, app: app)

        save.tap()
        XCTAssertFalse(
            app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 2),
            "Saving an area opened it"
        )

        app.terminate()
        app.launchArguments = ["--uitest-seed"]
        app.launch()
        _ = app.tabBars.buttons["Explore"].waitForExistence(timeout: 30)

        let resetOpen = findByScrolling(app.buttons["area-open-\(areaId)"].firstMatch, in: app)
        let resetSave = app.buttons["area-save-\(areaId)"].firstMatch
        XCTAssertEqual(resetSave.label, "Remove from Saved Areas")
        resetOpen.tap()
        XCTAssertTrue(
            app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60),
            "Opening an area did not show its map"
        )

        let close = app.buttons["area-close-button"].firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 10))
        close.tap()
        let saveAfterOpen = findByScrolling(app.buttons["area-save-\(areaId)"].firstMatch, in: app)
        XCTAssertEqual(saveAfterOpen.label, "Remove from Saved Areas", "Opening an area changed its saved state")
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
        XCTAssertNotEqual(select.identifier, secondary.identifier)
        let actionLabelsAreDistinct = select.label != secondary.label
        XCTAssertTrue(actionLabelsAreDistinct, "Trail action labels must be distinct")
        XCTAssertTrue(select.label.hasPrefix("Select Trail,"))
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
        XCTAssertTrue(select.label.hasPrefix("Select Trail,"), "Mark Complete selected the trail")

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

    private func launchSeededApp() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"]
        app.launch()
        _ = app.tabBars.buttons["Explore"].waitForExistence(timeout: 30)
        return app
    }

    @discardableResult
    private func findByScrolling(_ element: XCUIElement, in app: XCUIApplication) -> XCUIElement {
        var swipes = 0
        while !element.exists && swipes < 12 {
            app.swipeUp()
            swipes += 1
        }
        return element
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
        XCTAssertGreaterThanOrEqual(frame.minX, screen.minX - 1)
        XCTAssertLessThanOrEqual(frame.maxX, screen.maxX + 1)
        XCTAssertGreaterThanOrEqual(frame.minY, screen.minY - 1)
        XCTAssertLessThanOrEqual(frame.maxY, screen.maxY + 1)
    }
}
