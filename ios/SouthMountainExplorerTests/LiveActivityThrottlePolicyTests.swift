import Foundation
import Testing
@testable import SouthMountainExplorer

struct LiveActivityThrottlePolicyTests {
    typealias P = LiveActivityThrottlePolicy
    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    private func state(_ status: HikeActivityAttributes.ContentState.Status = .recording,
                       trailEnd: Date? = nil) -> HikeActivityAttributes.ContentState {
        .init(status: status, title: "T", subtitle: nil, distanceText: "0 mi", trailLengthText: nil,
              progress: nil, estimatedTrailEndAt: trailEnd, estimatedBackAt: nil, endedAt: nil)
    }
    private func last(_ s: HikeActivityAttributes.ContentState, meters: Double = 0) -> P.SentRecord {
        .init(state: s, sentAt: t0, distanceMetersAtSend: meters)
    }
    private func decide(_ s: HikeActivityAttributes.ContentState, after sec: TimeInterval,
                        meters: Double = 0, from l: P.SentRecord? = nil) -> P.Decision {
        P.decide(lastSent: l ?? last(state()), candidate: s, candidateDistanceMeters: meters,
                 now: t0.addingTimeInterval(sec))
    }

    @Test func firstSendIsInitial() {
        #expect(P.decide(lastSent: nil, candidate: state(), candidateDistanceMeters: 0, now: t0) == .send(.initial))
    }

    @Test func gpsFixRateIsSkipped() {
        for sec in stride(from: 2.0, through: 14.0, by: 2.0) {
            #expect(decide(state(), after: sec, meters: sec * 0.5) == .skip)
        }
    }

    @Test func distanceGate() {
        #expect(decide(state(), after: 20, meters: 49) == .skip)
        #expect(decide(state(), after: 14, meters: 50) == .skip)
        #expect(decide(state(), after: 15, meters: 50) == .send(.distance))
    }

    @Test func heartbeat() {
        #expect(decide(state(), after: 59) == .skip)
        #expect(decide(state(), after: 60) == .send(.heartbeat))
    }

    @Test func statusChangeRespectsSpacing() {
        #expect(decide(state(.gpsPaused), after: 4) == .skip)
        #expect(decide(state(.gpsPaused), after: 5) == .send(.stateChange))
    }

    @Test func finishedAlwaysSends() {
        #expect(decide(state(.finished), after: 0) == .send(.finished))
    }

    @Test func estimateAppearingAndDisappearingSend() {
        #expect(decide(state(trailEnd: t0.addingTimeInterval(900)), after: 6) == .send(.stateChange))
        let withEst = last(state(trailEnd: t0.addingTimeInterval(900)))
        #expect(decide(state(), after: 6, from: withEst) == .send(.stateChange))
    }

    @Test func estimateValueShift() {
        let l = last(state(trailEnd: t0.addingTimeInterval(900)))
        #expect(decide(state(trailEnd: t0.addingTimeInterval(1000)), after: 20, from: l) == .skip)
        #expect(decide(state(trailEnd: t0.addingTimeInterval(1300)), after: 20, from: l) == .send(.estimateShift))
    }

    @Test func threeHourWalkStaysUnderBudget() {
        var lastSent: P.SentRecord? = nil
        var sends = 0
        var meters = 0.0
        for i in 0..<(3 * 3600 / 2) {
            let now = t0.addingTimeInterval(Double(i) * 2)
            meters += 2.8
            if P.decide(lastSent: lastSent, candidate: state(), candidateDistanceMeters: meters, now: now) != .skip {
                sends += 1
                lastSent = .init(state: state(), sentAt: now, distanceMetersAtSend: meters)
            }
        }
        #expect(sends < 400)
        #expect(sends > 100)
    }
}
