import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release A, round 2 fixes: climb limit in `assemble`,
/// cooperative cancel, and the card-aware camera fit insets.
struct PlanRound2Tests {
    typealias K = PlannerKit

    private func scene() -> [Trail] {
        [K.trail("A", [(0, 0), (300, 0)], profile: K.ramp(150))]
    }

    // MARK: - F1 assemble enforces the climb limit

    @Test func assembleRejectsAWalkOverTheClimbLimit() throws {
        let free = try K.run(scene(), limit: 3600)
        let req = free.request
        let loose = try #require(RoutePlanEngine(req))
        loose.prepare()
        let state = try #require(loose.makeState(order: [0]))
        #expect(loose.assemble(state) != nil)             // control: no climb limit, plan assembles
        let capped = RoutePlanRequest(
            network: req.network, candidates: req.candidates, completion: req.completion,
            timeLimitSeconds: req.timeLimitSeconds, maxGainFt: 20, paceFactor: req.paceFactor, shape: req.shape)
        let strict = try #require(RoutePlanEngine(capped))
        strict.prepare()
        #expect(strict.assemble(state) == nil)            // same walk, over 20 ft of climb
    }

    @Test func assembleKeepsAWalkInsideTheClimbLimit() throws {
        let free = try K.run(scene(), limit: 3600)
        let gain = try #require(free.plan).metrics.gainFt
        let ok = try K.run(scene(), limit: 3600, maxGain: gain + 5)
        #expect(ok.plan != nil)
    }

    // MARK: - F2 cooperative cancel

    @Test func cancelledTokenStopsThePlanner() throws {
        let free = try K.run(scene(), limit: 3600)
        let token = PlanCancellation()
        token.cancel()
        let plan = PlanCancellation.$current.withValue(token) { RoutePlanner.plan(free.request) }
        #expect(plan == nil)
        #expect(RoutePlanner.plan(free.request) != nil)   // control: no token, same request plans
    }

    @Test func cancelledOutcomeSkipsTheExtraSearches() throws {
        let t = scene()
        let v = try K.view(t)
        let req = PlanOutcomeRequest(network: v, trails: K.inputs(t), timeLimitSeconds: 3600)
        let token = PlanCancellation()
        token.cancel()
        let out = PlanCancellation.$current.withValue(token) { RoutePlanningService.planOutcome(req) }
        if case .planned = out { Issue.record("a cancelled run must not return a plan") }
        if case .planned = RoutePlanningService.planOutcome(req) {} else { Issue.record("control should plan") }
    }

    @Test func runDetachedReturnsNilWhenTheCallerIsCancelled() async {
        let started = Started()
        let task = Task { () -> Int? in
            await PlanCancellation.runDetached {
                started.set()
                while !PlanCancellation.isCancelled { Thread.sleep(forTimeInterval: 0.005) }
                return 7
            }
        }
        while !started.value { try? await Task.sleep(for: .milliseconds(5)) }
        task.cancel()
        let result = await task.value
        #expect(result == nil)
    }

    @Test func runDetachedReturnsTheValueWhenNotCancelled() async {
        let v = await PlanCancellation.runDetached { 42 }
        #expect(v == 42)
    }

    @MainActor
    @Test func controllerCancelDropsToIdle() {
        let c = PlanHikeController()
        c.cancel()
        #expect(c.state.isIdle)
    }

    // MARK: - Camera fit insets

    private let window = CGSize(width: 400, height: 800)

    @Test func sheetCardReservesItsRealHeight() {
        // Card top at y = 330 (taller than half): bottom reserve covers it plus padding.
        let i = PlanFitInsets.insets(
            cardFrame: CGRect(x: 0, y: 330, width: 400, height: 470), window: window, controlsBottom: 72)
        #expect(i.bottom == 470 + PlanFitInsets.edgePadding)
        #expect(i.leading == 20 && i.trailing == 20)
        #expect(i.top == 112)
    }

    @Test func differentCardHeightsGiveDifferentInsets() {
        let a = PlanFitInsets.insets(cardFrame: CGRect(x: 0, y: 400, width: 400, height: 400), window: window, controlsBottom: 72)
        let b = PlanFitInsets.insets(cardFrame: CGRect(x: 0, y: 250, width: 400, height: 550), window: window, controlsBottom: 72)
        #expect(b.bottom > a.bottom)
    }

    @Test func unmeasuredCardFallsBackToHalfScreen() {
        let i = PlanFitInsets.insets(cardFrame: nil, window: window, controlsBottom: 72)
        #expect(i.bottom == 800 * 0.5 + 24)
    }

    @Test func narrowFloatingSheetIsStillABottomSheet() {
        // Measured on the wide Duo: 374 wide in a 466 wide window, top at 293 of 678.
        let w = CGSize(width: 466, height: 678)
        let i = PlanFitInsets.insets(cardFrame: CGRect(x: 8, y: 293, width: 374, height: 350), window: w, controlsBottom: 72)
        #expect(i.bottom == 678 - 293 + PlanFitInsets.edgePadding)
        #expect(i.leading == 20 && i.trailing == 20)
    }

    @Test func tallCardIsCapped() {
        let i = PlanFitInsets.insets(cardFrame: CGRect(x: 0, y: 20, width: 400, height: 780), window: window, controlsBottom: 72)
        #expect(i.bottom == 800 * PlanFitInsets.maxShare)
    }

    @Test func sidePanelOnTheRightReservesTheRightEdge() {
        let wide = CGSize(width: 1200, height: 800)
        let i = PlanFitInsets.insets(
            cardFrame: CGRect(x: 800, y: 0, width: 400, height: 800), window: wide, controlsBottom: 72)
        #expect(i.trailing == 400 + PlanFitInsets.edgePadding)
        #expect(i.bottom == 20)
        #expect(i.leading == 20)
    }

    @Test func sidePanelOnTheLeftReservesTheLeftEdge() {
        let wide = CGSize(width: 1200, height: 800)
        let i = PlanFitInsets.insets(
            cardFrame: CGRect(x: 0, y: 0, width: 400, height: 800), window: wide, controlsBottom: 72)
        #expect(i.leading == 400 + PlanFitInsets.edgePadding)
        #expect(i.trailing == 20)
    }

    @Test func fitKeepsRouteAndPinAboveTheCard() {
        // The card covers the bottom 470 of 800; the route's south edge must land above its top.
        let insets = PlanFitInsets.insets(
            cardFrame: CGRect(x: 0, y: 330, width: 400, height: 470), window: window, controlsBottom: 72)
        let pts: [(lat: Double, lon: Double)] = [(33.30, -112.0), (33.32, -112.0), (33.31, -111.99)]
        guard case .region(let lat, _, let latDelta, _)? = TrailMapView.selectedRouteRegion(
            points: pts, viewportInsets: insets, screenHeight: window.height, screenWidth: window.width)
        else { Issue.record("no region"); return }
        let southY = (lat + latDelta / 2 - 33.30) / latDelta * window.height
        #expect(southY <= 330 - 20, "route bottom \(southY) must sit above the card top 330")
    }
}

private final class Started: @unchecked Sendable {
    private let lock = NSLock()
    private var flag = false
    func set() { lock.lock(); flag = true; lock.unlock() }
    var value: Bool { lock.lock(); defer { lock.unlock() }; return flag }
}
