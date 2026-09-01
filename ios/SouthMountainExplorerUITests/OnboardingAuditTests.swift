import XCTest

/// Photographs the app's FIRST launch — the one state nothing else covers.
///
/// Reported 2026-09-01 from a clean install on a spare iPhone 13 mini: the
/// onboarding walkthrough and the location permission prompt never appeared.
/// Every existing UI test launches with `--uitest-seed`, and that seeding sets
/// `StorageKeys.onboarded = true` (`UITestSupport.swift`), so the whole suite
/// has been blind to first launch by construction.
///
/// This class launches with NO arguments, so `summit:onboarded` stays at its
/// `false` default and the app takes the same path a new install does.
///
/// Run via `ios-screenshots.yml` with `test_class: OnboardingAuditTests`.
final class OnboardingAuditTests: XCTestCase {

    override func setUp() {
        super.setUp()
        continueAfterFailure = true
    }

    // MARK: - First launch

    func testFirstLaunchShowsOnboarding() {
        let app = XCUIApplication()
        app.launchArguments = []          // deliberately empty — see class doc
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 60),
                      "App never reached the foreground")
        settle(6)                          // covers present a frame or two late

        capture(app, "onboard-01-first-launch")
        dumpTree(app, "first-launch")

        let cont = app.buttons["Continue"].firstMatch
        let start = app.buttons["Get Started"].firstMatch
        let onboardingVisible = cont.exists || start.exists

        // Name what IS on screen, so a failure says where the app landed
        // instead of only that onboarding was absent.
        let exploreTab = app.tabBars.buttons.element(boundBy: 0)
        print("AUDIT[first-launch] onboardingVisible=\(onboardingVisible) "
              + "continueExists=\(cont.exists) getStartedExists=\(start.exists) "
              + "tabBarCount=\(app.tabBars.count) "
              + "firstTab=\(exploreTab.exists ? exploreTab.label : "<none>")")

        XCTAssertTrue(
            onboardingVisible,
            "FIRST LAUNCH SHOWED NO ONBOARDING. `summit:onboarded` defaults to "
            + "false and ContentView presents OnboardingView in a fullScreenCover, "
            + "so the walkthrough should be up. See the dumped tree for what "
            + "presented instead."
        )

        guard onboardingVisible else { return }

        // ---- walk the four pages -------------------------------------------
        for page in 1...4 {
            capture(app, String(format: "onboard-%02d-page-%d", page + 1, page))
            let next = app.buttons["Continue"].firstMatch
            let finish = app.buttons["Get Started"].firstMatch
            if finish.exists && finish.isHittable {
                print("AUDIT[onboarding] page \(page): Get Started — finishing")
                finish.tap()
                break
            } else if next.exists && next.isHittable {
                next.tap()
                settle(2)
            } else {
                XCTFail("Onboarding page \(page) had neither CTA")
                break
            }
        }

        settle(5)
        capture(app, "onboard-06-after-get-started")
        dumpTree(app, "after-get-started")

        // ---- did anything ever ask for location? ---------------------------
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let systemAlerts = springboard.alerts
        let sawLocationPrompt = systemAlerts.count > 0
            && systemAlerts.element(boundBy: 0).label.lowercased().contains("location")
        print("AUDIT[permission] springboardAlerts=\(systemAlerts.count) "
              + "sawLocationPrompt=\(sawLocationPrompt) "
              + "firstAlertLabel=\(systemAlerts.count > 0 ? systemAlerts.element(boundBy: 0).label : "<none>")")
        if sawLocationPrompt {
            capture(app, "onboard-07-location-prompt")
        }

        // Recorded, not asserted: `OnboardingView.swift` contains no reference
        // to LocationService, so onboarding is not expected to ask. The print
        // above is the evidence either way, and the assertion belongs in
        // whatever fix decides where the ask should live.
    }

    // MARK: - Helpers

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
