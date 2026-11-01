import Foundation
import Testing
@testable import SouthMountainExplorer

/// Live Activity extras: next-turn row and arrival clock times.
struct LiveActivityExtrasTests {
    typealias CS = HikeActivityAttributes.ContentState
    typealias P = LiveActivityThrottlePolicy
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func turn(_ kind: TurnCueEvent.Kind = .approach,
                      _ symbol: TurnCueSymbol = .turnLeft,
                      trail: String = "Crosscut Trail",
                      meters: Double? = 91.44) -> LiveActivityNextTurn {
        LiveActivityNextTurn(kind: kind, symbol: symbol, trailName: trail, distanceMeters: meters)
    }

    private func snap(status: GpsIngest.ActiveStatus = .good,
                      next: LiveActivityNextTurn? = nil,
                      estimates: LiveActivityEstimates = .none,
                      metric: Bool = false) -> LiveActivityRecordingSnapshot {
        LiveActivityRecordingSnapshot(
            recordingId: "r1", startedAt: now.addingTimeInterval(-600), isWalk: false,
            distanceMeters: 1000, gpsStatus: status, title: "Hidden Valley", subtitle: nil,
            trailLengthMeters: nil, estimates: estimates, usesMetric: metric, nextTurn: next)
    }

    private func map(_ s: LiveActivityRecordingSnapshot, finished: Bool = false) -> CS {
        HikeActivityStateMapper.contentState(from: s, now: now, finishedAt: finished ? now : nil)
    }

    // MARK: - Cue -> row

    @Test func approachRowImperial() {
        let s = map(snap(next: turn()))   // 91.44 m = 300 ft
        #expect(s.nextTurnArrow == .turnLeft)
        #expect(s.nextTurnText == "Turn left in 300 ft")
        #expect(s.nextTurnDistanceText == "300 ft")
        #expect(s.nextTurnTrail == "Crosscut Trail")
        #expect(s.nextTurnSpoken == "turn left in 300 feet onto Crosscut Trail")
    }

    @Test func approachRowMetric() {
        let s = map(snap(next: turn(meters: 80), metric: true))
        #expect(s.nextTurnText == "Turn left in 75 m")
        #expect(s.nextTurnSpoken == "turn left in 75 meters onto Crosscut Trail")
        let far = map(snap(next: turn(meters: 240), metric: true))
        #expect(far.nextTurnDistanceText == "250 m")
    }

    @Test func arrowsForEverySymbol() {
        let cases: [(TurnCueSymbol, HikeActivityAttributes.NextTurnArrow, String)] = [
            (.keepLeft, .keepLeft, "Keep left in 300 ft"),
            (.keepRight, .keepRight, "Keep right in 300 ft"),
            (.turnLeft, .turnLeft, "Turn left in 300 ft"),
            (.turnRight, .turnRight, "Turn right in 300 ft"),
            (.sharpLeft, .sharpLeft, "Sharp left in 300 ft"),
            (.sharpRight, .sharpRight, "Sharp right in 300 ft"),
        ]
        for (symbol, arrow, text) in cases {
            let s = map(snap(next: turn(.approach, symbol)))
            #expect(s.nextTurnArrow == arrow && s.nextTurnText == text)
        }
    }

    @Test func nearAndAtJunctionSayNow() {
        let near = map(snap(next: turn(.approach, .keepRight, meters: 10)))
        #expect(near.nextTurnText == "Keep right now" && near.nextTurnDistanceText == "Now")
        let at = map(snap(next: turn(.atJunction, .turnRight, meters: nil)))
        #expect(at.nextTurnText == "Turn right now" && at.nextTurnArrow == .turnRight)
    }

    @Test func offAndBackOnTrail() {
        let off = map(snap(next: turn(.offTrail, .offTrail, meters: nil)))
        #expect(off.nextTurnArrow == .offTrail)
        #expect(off.nextTurnText == "Off Crosscut Trail" && off.nextTurnTrail == nil)
        let back = map(snap(next: turn(.backOnTrail, .backOnTrail, meters: nil)))
        #expect(back.nextTurnArrow == .backOnTrail && back.nextTurnText == "Back on Crosscut Trail")
        let unnamed = map(snap(next: turn(.offTrail, .offTrail, trail: "  ", meters: nil)))
        #expect(unnamed.nextTurnText == "Off the trail")
    }

    @Test func distanceLadderIsCoarse() {
        func d(_ m: Double) -> String? {
            LiveActivityNextTurnFormatter.distance(meters: m, units: .imperial)?.short
        }
        #expect(d(15) == nil)                 // < 50 ft reads "Now"
        #expect(d(30) == "100 ft")            // 98 ft -> 100
        #expect(d(31) == "100 ft")
        #expect(d(60) == "200 ft")            // 197 ft
        #expect(d(100) == "300 ft")           // 328 ft -> 300 (100 ft step)
        #expect(d(152.4) == "500 ft")
        // A walker covers ~1 m between fixes: the text must not change per fix.
        let texts = Set(stride(from: 120.0, through: 125.0, by: 0.5).compactMap { d($0) })
        #expect(texts.count <= 2)
    }

    // MARK: - Hidden cases

    @Test func hiddenWhenNoCue() {
        let s = map(snap(next: nil))
        #expect(s.nextTurnArrow == nil && s.nextTurnText == nil && s.nextTurnSpoken == nil)
    }

    @Test func hiddenWhenGpsDegraded() {
        for status in [GpsIngest.ActiveStatus.paused, .waiting] {
            let s = map(snap(status: status, next: turn()))
            #expect(s.nextTurnArrow == nil && s.nextTurnText == nil)
        }
        #expect(map(snap(status: .recovered, next: turn())).nextTurnArrow == .turnLeft)
    }

    @Test func hiddenWhenFinishedOrGpsWeakOrNoDistance() {
        #expect(map(snap(next: turn()), finished: true).nextTurnText == nil)
        #expect(map(snap(next: turn(.gpsWeak, .gpsWeak, meters: nil))).nextTurnText == nil)
        #expect(map(snap(next: turn(.approach, .turnLeft, meters: nil))).nextTurnText == nil)
    }

    // MARK: - Arrival clock times

    @Test func approximateFlagsFollowEstimates() {
        let est = LiveActivityEstimates(trailEndSeconds: 600, backSeconds: 1200,
                                        trailEndApproximate: true, backApproximate: false)
        let s = map(snap(estimates: est))
        #expect(s.estimatedTrailEndApprox == true && s.estimatedBackApprox == false)
        #expect(s.estimatedTrailEndAt == now.addingTimeInterval(600))
        #expect(s.estimatedBackAt == now.addingTimeInterval(1200))
    }

    @Test func approximateFlagNilWhenTimeHidden() {
        let est = LiveActivityEstimates(trailEndSeconds: nil, backSeconds: 600, backApproximate: true)
        let s = map(snap(estimates: est))
        #expect(s.estimatedTrailEndAt == nil && s.estimatedTrailEndApprox == nil)
        #expect(s.estimatedBackApprox == true)
        let paused = map(snap(status: .paused, estimates: est))
        #expect(paused.estimatedBackAt == nil && paused.estimatedBackApprox == nil)
    }

    @Test func clockFormat12And24Hour() {
        // 2027-01-15 16:12 UTC
        let d = Date(timeIntervalSince1970: 1_800_029_520)
        let utc = TimeZone(identifier: "UTC")!
        let us = HikeActivityClock.time(d, locale: Locale(identifier: "en_US"), timeZone: utc)
            .replacingOccurrences(of: "\u{202F}", with: " ")
        let gb = HikeActivityClock.time(d, locale: Locale(identifier: "en_GB"), timeZone: utc)
        #expect(us == "4:12 PM")
        #expect(gb == "16:12")
    }

    @Test func clockLabelMarksApproximate() {
        let d = Date(timeIntervalSince1970: 1_800_029_520)
        let utc = TimeZone(identifier: "UTC")!
        let gb = Locale(identifier: "en_GB")
        #expect(HikeActivityClock.label("Finish", d, approximate: true, locale: gb, timeZone: utc) == "Finish ~16:12")
        #expect(HikeActivityClock.label("Back by", d, approximate: false, locale: gb, timeZone: utc) == "Back by 16:12")
        #expect(HikeActivityClock.label("Back by", d, approximate: nil, locale: gb, timeZone: utc) == "Back by 16:12")
    }

    // MARK: - Update triggers

    private func state(next: LiveActivityNextTurn? = nil, trailEnd: Date? = nil,
                       status: GpsIngest.ActiveStatus = .good) -> CS {
        map(snap(status: status, next: next,
                 estimates: .init(trailEndSeconds: trailEnd.map { $0.timeIntervalSince(now) }, backSeconds: nil)))
    }

    private func decide(_ candidate: CS, from last: CS, after sec: TimeInterval) -> P.Decision {
        P.decide(lastSent: .init(state: last, sentAt: now, distanceMetersAtSend: 0),
                 candidate: candidate, candidateDistanceMeters: 0, now: now.addingTimeInterval(sec))
    }

    @Test func cueAppearingChangingAndClearingTriggers() {
        let none = state()
        let left = state(next: turn())
        #expect(decide(left, from: none, after: 6) == .send(.cueChange))
        #expect(decide(none, from: left, after: 6) == .send(.cueChange))
        let right = state(next: turn(.approach, .turnRight))
        #expect(decide(right, from: left, after: 6) == .send(.cueChange))
        let other = state(next: turn(trail: "Ridge Trail"))
        #expect(decide(other, from: left, after: 6) == .send(.cueChange))
        let off = state(next: turn(.offTrail, .offTrail, meters: nil))
        #expect(decide(off, from: left, after: 6) == .send(.cueChange))
        // Crossing into "now" (near threshold / at junction) sends promptly.
        let atJ = state(next: turn(.atJunction, .turnLeft, meters: nil))
        #expect(decide(atJ, from: left, after: 6) == .send(.cueChange))
        // Spacing still applies.
        #expect(decide(left, from: none, after: 2) == .skip)
    }

    @Test func distanceStepsAreRateLimitedAndPerFixIsSkipped() {
        let a = state(next: turn(meters: 91.44))   // 300 ft
        let b = state(next: turn(meters: 60.96))   // 200 ft
        #expect(decide(b, from: a, after: 6) == .skip)
        #expect(decide(b, from: a, after: 15) == .send(.cueDistance))
        // Same rounded text (per-fix jitter) never triggers before the heartbeat.
        let jitter = state(next: turn(meters: 90.2))
        for sec in stride(from: 2.0, through: 58.0, by: 4.0) {
            #expect(decide(jitter, from: a, after: sec) == .skip)
        }
        #expect(decide(jitter, from: a, after: 60) == .send(.heartbeat))
    }

    @Test func clockTimeThreshold() {
        let a = state(trailEnd: now.addingTimeInterval(900))
        let small = state(trailEnd: now.addingTimeInterval(900 + 60))
        let big = state(trailEnd: now.addingTimeInterval(900 + 120))
        #expect(decide(small, from: a, after: 20) == .skip)
        #expect(decide(big, from: a, after: 20) == .send(.estimateShift))
    }

    // MARK: - Codable backward compatibility

    @Test func oldPayloadDecodesWithoutNewKeys() throws {
        let json = """
        {"status":"recording","title":"Old","distanceText":"1.00 mi",
         "estimatedBackAt":800000000}
        """.data(using: .utf8)!
        let s = try JSONDecoder().decode(CS.self, from: json)
        #expect(s.title == "Old" && s.estimatedBackAt != nil)
        #expect(s.nextTurnArrow == nil && s.nextTurnText == nil && s.nextTurnTrail == nil)
        #expect(s.nextTurnSpoken == nil && s.nextTurnDistanceText == nil)
        #expect(s.estimatedTrailEndApprox == nil && s.estimatedBackApprox == nil)
    }

    @Test func newStateRoundTripsAndStaysSmall() throws {
        let s = map(snap(next: turn(trail: String(repeating: "Long Trail Name ", count: 6)),
                         estimates: .init(trailEndSeconds: 600, backSeconds: 900,
                                          trailEndApproximate: true, backApproximate: true)))
        let data = try JSONEncoder().encode(s)
        #expect(try JSONDecoder().decode(CS.self, from: data) == s)
        #expect(data.count < 1024)
    }

    @Test func accessibilityLabelIncludesNextTurnAndClockTimes() {
        var s = map(snap(next: turn(),
                         estimates: .init(trailEndSeconds: 600, backSeconds: 1200)))
        s.estimatedTrailEndApprox = nil
        let label = HikeActivityAccessibility.label(for: s)
        #expect(label.contains("Next: turn left in 300 feet onto Crosscut Trail"))
        #expect(label.contains("Finish around "))
        #expect(label.contains("Back by around "))
    }
}
