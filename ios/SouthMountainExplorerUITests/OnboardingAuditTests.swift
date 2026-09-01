import XCTest

/// Photographs the app's FIRST launch — the one state nothing else covers.
///
/// Reported 2026-09-01 from a clean install on a spare iPhone 13 mini: the
/// onboarding walkthrough and the location permission prompt never appeared.
/// Onboarding lost a fight between three `.fullScreenCover`s on one TabView,
/// and nothing had ever asked for location during onboarding at all.
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
            + "false and ContentView renders OnboardingView whenever it is false, "
            + "so the walkthrough should be up. See the dumped tree for where the "
            + "app landed instead."
        )

        guard onboardingVisible else { return }

        // ---- walk every page, ending on the location ask -------------------
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        var sawLocationPrompt = false
        var pagesWalked = 0

        for page in 1...6 {
            capture(app, String(format: "onboard-%02d-page-%d", page + 1, page))
            let enable = app.buttons["Enable Location"].firstMatch
            let finish = app.buttons["Get Started"].firstMatch
            let next = app.buttons["Continue"].firstMatch

            if enable.exists && enable.isHittable {
                pagesWalked = page
                print("AUDIT[onboarding] page \(page): Enable Location — tapping")
                enable.tap()
                // The system alert is owned by springboard, not the app.
                let alert = springboard.alerts.firstMatch
                if alert.waitForExistence(timeout: 15) {
                    sawLocationPrompt = true
                    print("AUDIT[permission] alert label=\(alert.label)")
                    capture(app, "onboard-08-location-prompt")
                    let allow = alert.buttons["Allow While Using App"]
                    if allow.exists { allow.tap() } else { alert.buttons.element(boundBy: 0).tap() }
                }
                break
            } else if finish.exists && finish.isHittable {
                pagesWalked = page
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

        settle(6)
        capture(app, "onboard-09-after-onboarding")
        dumpTree(app, "after-onboarding")

        print("AUDIT[permission] pagesWalked=\(pagesWalked) sawLocationPrompt=\(sawLocationPrompt)")
        XCTAssertTrue(sawLocationPrompt,
                      "Onboarding finished without ever asking for location. The "
                      + "last page's CTA should be 'Enable Location' and should "
                      + "raise the system alert.")

        // Onboarding must actually go away once it is done, and the tabs must
        // be usable — an overlay that never clears is the opposite failure.
        XCTAssertFalse(app.buttons["Continue"].firstMatch.exists,
                       "Onboarding was still on screen after finishing")
        let exploreAfter = app.tabBars.buttons["Explore"].firstMatch
        XCTAssertTrue(exploreAfter.waitForExistence(timeout: 15),
                      "Tab bar was not reachable after onboarding finished")
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
