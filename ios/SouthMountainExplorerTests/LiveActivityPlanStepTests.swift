import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release B task 6: the Lock Screen and Dynamic Island step row
/// while a hike started from a plan is guided. Synthetic out and back only.
struct LiveActivityPlanStepTests {
    typealias CS = HikeActivityAttributes.ContentState
    typealias P = LiveActivityThrottlePolicy
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func status(_ step: Int, _ meters: Double, off: Double? = nil,
                        complete: Bool = false) -> PlanGuideEngine.Status {
        PlanGuideBarTests.status(step: step, meters: meters, off: off, complete: complete)
    }
    private func cue(_ kind: TurnCueEvent.Kind, _ symbol: TurnCueSymbol,
                     meters: Double? = nil) -> LiveActivityNextTurn {
        // Plan cues are direction only: no trail name.
        LiveActivityNextTurn(kind: kind, symbol: symbol, trailName: "", distanceMeters: meters)
    }
    private func map(_ steps: [PlanStep], _ st: PlanGuideEngine.Status, cue: LiveActivityNextTurn? = nil,
                     gps: GpsIngest.ActiveStatus = .good, metric: Bool = false,
                     trailEnd: TimeInterval? = nil, finished: Bool = false) -> CS {
        let snap = LiveActivityRecordingSnapshot(
            recordingId: "r1", startedAt: now.addingTimeInterval(-600), isWalk: false,
            distanceMeters: 1000, gpsStatus: gps, title: "South Mountain", subtitle: nil,
            trailLengthMeters: nil, estimates: .init(trailEndSeconds: trailEnd, backSeconds: 900),
            usesMetric: metric, nextTurn: cue,
            planGuide: LiveActivityPlanGuide(steps: steps, status: st))
        return HikeActivityStateMapper.contentState(from: snap, now: now, finishedAt: finished ? now : nil)
    }

    @Test func stepRowMatchesTheBar() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let first = map(s, status(0, 1290))
        #expect(first.nextTurnArrow == .straight)
        #expect(first.nextTurnText == "1. \(s[0].instruction), 0.8 mi to next turn")
        #expect(first.nextTurnText == PlanGuideText.line(steps: s, status: status(0, 1290), units: .imperial))
        #expect(first.nextTurnDistanceText == "0.8 mi")
        #expect(first.nextTurnSubtitle == "Step 1 of 4")
        #expect(first.nextTurnSpoken == "Step 1 of 4. 1. \(s[0].instruction), 0.8 mi to next turn")
        let second = map(s, status(1, 120))
        #expect(second.nextTurnArrow == .turnLeft && second.nextTurnSubtitle == "Step 2 of 4")
        #expect(second.nextTurnText == "2. \(s[1].instruction), 400 ft to next turn")
        // The new turn-around arrow on the dead end, then the last step to the finish.
        let third = map(s, status(2, 300))
        #expect(third.nextTurnArrow == .turnAround)
        let last = map(s, status(3, 120))
        #expect(last.nextTurnArrow == .turnRight)
        #expect(last.nextTurnText == "4. \(s[3].instruction), 400 ft to finish")
        let metric = map(s, status(1, 456), metric: true)
        #expect(metric.nextTurnDistanceText == "460 m")
    }

    @Test func everyStepActionHasAnArrow() {
        let cases: [(PlanStep.Action, HikeActivityAttributes.NextTurnArrow)] = [
            (.start, .straight), (.straight, .straight), (.keepLeft, .keepLeft), (.keepRight, .keepRight),
            (.left, .turnLeft), (.right, .turnRight), (.sharpLeft, .sharpLeft), (.sharpRight, .sharpRight),
            (.turnAround, .turnAround),
        ]
        for (action, arrow) in cases {
            #expect(LiveActivityPlanStepFormatter.arrow(for: action) == arrow)
        }
    }

    @Test func turnCueTakesTheRowWithTheStepCounter() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let heads = map(s, status(0, 91.44), cue: cue(.approach, .turnLeft, meters: 91.44))
        #expect(heads.nextTurnArrow == .turnLeft)
        #expect(heads.nextTurnText == "Turn left in 300 ft")
        #expect(heads.nextTurnSubtitle == "Step 1 of 4")
        #expect(heads.nextTurnSpoken == "turn left in 300 feet")
        let at = map(s, status(1, 0), cue: cue(.atJunction, .turnAround))
        #expect(at.nextTurnArrow == .turnAround && at.nextTurnText == "Turn around now")
        #expect(at.nextTurnDistanceText == "Now")
        let straight = map(s, status(0, 91.44), cue: cue(.approach, .straight, meters: 91.44))
        #expect(straight.nextTurnArrow == .straight && straight.nextTurnText == "Continue straight in 300 ft")
        let back = map(s, status(1, 200), cue: cue(.backOnTrail, .backOnTrail))
        #expect(back.nextTurnArrow == .backOnTrail && back.nextTurnText == "Back on route")
        #expect(back.nextTurnSubtitle == "Step 2 of 4")
    }

    @Test func offRouteRowIsAWarningWithDistanceToRoute() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let off = map(s, status(1, 200, off: 46), cue: cue(.offTrail, .offTrail), trailEnd: 600)
        #expect(off.nextTurnArrow == .offTrail)
        #expect(off.nextTurnText == "Off route, 150 ft to route")
        #expect(off.nextTurnDistanceText == "150 ft")
        #expect(off.nextTurnSubtitle == nil)
        // Finish time means nothing off route; Back by stays.
        #expect(off.estimatedTrailEndAt == nil && off.estimatedBackAt != nil)
        // Off route wins over a stale turn cue.
        let stale = map(s, status(1, 200, off: 46), cue: cue(.approach, .turnLeft, meters: 50))
        #expect(stale.nextTurnArrow == .offTrail)
    }

    @Test func routeCompleteRowAndHiddenWhenDegradedOrSaved() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let done = map(s, status(3, 0, complete: true))
        #expect(done.nextTurnArrow == .routeComplete && done.nextTurnText == "Route complete")
        #expect(done.nextTurnSubtitle == nil && done.nextTurnDistanceText == nil)
        // GPS weak or paused: the row hides, as for trail cues.
        #expect(map(s, status(1, 200), gps: .paused).nextTurnArrow == nil)
        #expect(map(s, status(1, 200), gps: .waiting).nextTurnText == nil)
        // The hike is saved: no row on the final state.
        #expect(map(s, status(1, 200), finished: true).nextTurnArrow == nil)
    }

    @Test func stepChangeSendsPromptlyAndCountdownIsRateLimited() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        func decide(_ c: CS, from l: CS, after sec: TimeInterval) -> P.Decision {
            P.decide(lastSent: .init(state: l, sentAt: now, distanceMetersAtSend: 0),
                     candidate: c, candidateDistanceMeters: 0, now: now.addingTimeInterval(sec))
        }
        let a = map(s, status(1, 300))
        // New step: the counter line changes, so it goes out at once.
        #expect(decide(map(s, status(2, 300)), from: a, after: 6) == .send(.cueChange))
        // Off route and the finish also go out at once.
        #expect(decide(map(s, status(1, 300, off: 60)), from: a, after: 6) == .send(.cueChange))
        #expect(decide(map(s, status(3, 0, complete: true)), from: a, after: 6) == .send(.cueChange))
        // The countdown on the same step waits for the distance spacing.
        let closer = map(s, status(1, 200))
        #expect(decide(closer, from: a, after: 6) == .skip)
        #expect(decide(closer, from: a, after: 15) == .send(.cueDistance))
    }

    @Test func newArrowsRoundTripAndOldPayloadStillDecodes() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        for st in [status(0, 1290), status(2, 300), status(1, 200, off: 46), status(3, 0, complete: true)] {
            let state = map(s, st)
            let data = try JSONEncoder().encode(state)
            #expect(try JSONDecoder().decode(CS.self, from: data) == state)
            #expect(data.count < 1024)
        }
        let old = #"{"status":"recording","title":"Old","distanceText":"1.00 mi","nextTurnArrow":"turnLeft"}"#
        #expect(try JSONDecoder().decode(CS.self, from: Data(old.utf8)).nextTurnArrow == .turnLeft)
    }

    @Test func accessibilityReadsTheStep() throws {
        let (_, s) = try PlanGuideRouteTests.outAndBack()
        let label = HikeActivityAccessibility.label(for: map(s, status(1, 120)))
        #expect(label.contains("Next: Step 2 of 4. 2. \(s[1].instruction), 400 ft to next turn"))
    }
}
