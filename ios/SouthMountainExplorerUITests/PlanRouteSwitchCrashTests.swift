import XCTest

/// TestFlight 319 crash: tapping Edit, Shorter or Less climb on a planned
/// route trapped in `PlanRouteCard.stepRow` (a stale row index into a route
/// with fewer steps). Plans a hike in South Mountain, selects a late step,
/// then switches routes and edits several times; the app must stay up.
final class PlanRouteSwitchCrashTests: XCTestCase {
    private let areaRowId = "area-progress-south-mountain-park-and-preserve-az"

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    func testSwitchingRoutesAndEditingDoesNotCrash() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"]
        app.launch()
        sleep(15)
        openArea(app)

        let planButton = app.buttons["area-plan-button"].firstMatch
        XCTAssertTrue(planButton.waitForExistence(timeout: 60), "Plan a Hike button missing")
        tap(planButton)

        for round in 1...3 {
            runPlan(app)
            let chips = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'plan-alt-'"))
            let chipIds = (0..<chips.count).map { chips.element(boundBy: $0).identifier }
            print("PLAN-CRASH round \(round): chips \(chipIds)")
            for id in chipIds.reversed() + chipIds {
                selectLateStep(app)
                let chip = app.buttons[id].firstMatch
                guard chip.waitForExistence(timeout: 5) else { continue }
                tap(chip)
                sleep(1)
                XCTAssertEqual(app.state, .runningForeground, "App died after tapping \(id)")
            }
            selectLateStep(app)
            let edit = app.buttons["plan-edit"].firstMatch
            XCTAssertTrue(edit.waitForExistence(timeout: 10), "Edit button missing")
            tap(edit)
            sleep(1)
            XCTAssertEqual(app.state, .runningForeground, "App died after Edit (round \(round))")
        }
    }

    // MARK: - Helpers

    private func openArea(_ app: XCUIApplication) {
        let stats = app.buttons["Stats"].firstMatch
        XCTAssertTrue(stats.waitForExistence(timeout: 30), "Stats tab missing")
        for _ in 1...4 where !stats.isSelected {
            stats.tap()
            sleep(3)
        }
        let row = app.descendants(matching: .any)[areaRowId].firstMatch
        var swipes = 0
        while !row.exists && swipes < 12 {
            app.swipeUp()
            swipes += 1
            sleep(1)
        }
        XCTAssertTrue(row.waitForExistence(timeout: 10), "Area row missing")
        tap(row)
        XCTAssertTrue(app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60), "Area never opened")
        sleep(5)
    }

    private func runPlan(_ app: XCUIApplication) {
        let run = app.buttons["plan-run-button"].firstMatch
        XCTAssertTrue(run.waitForExistence(timeout: 30), "Plan button missing")
        tap(run)
        let card = app.descendants(matching: .any)["plan-route-card"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 180), "No planned route")
        sleep(2)
    }

    /// Taps the last numbered step in the list. Walk through drops the sheet
    /// to the slim bar, so swipe it back up to reach the chips and Edit.
    private func selectLateStep(_ app: XCUIApplication) {
        let rows = app.buttons.matching(NSPredicate(
            format: "identifier BEGINSWITH 'plan-step-' AND identifier != 'plan-step-finish'"))
        guard rows.count > 0 else { return }
        tap(rows.element(boundBy: rows.count - 1))
        sleep(1)
        let bar = app.descendants(matching: .any)["plan-collapsed-bar"].firstMatch
        if bar.exists { bar.swipeUp(); sleep(1) }
    }

    private func tap(_ element: XCUIElement) {
        if element.isHittable {
            element.tap()
        } else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
    }
}
