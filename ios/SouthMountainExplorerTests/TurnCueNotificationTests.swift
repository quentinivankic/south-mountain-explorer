import Foundation
import Testing
@testable import SouthMountainExplorer

@MainActor
private final class FakeCenter: TurnCueNotificationCenter {
    var status: TurnCueNotificationAuthorization
    var posted: [TurnCueNotification] = []
    var removed: [String] = []
    var requests = 0
    init(_ status: TurnCueNotificationAuthorization = .authorized) { self.status = status }
    func authorizationStatus() async -> TurnCueNotificationAuthorization { status }
    func requestAuthorization() async -> Bool { requests += 1; return status == .authorized }
    func post(_ notification: TurnCueNotification) async { posted.append(notification) }
    func remove(identifier: String) { removed.append(identifier) }
}

private func event(_ kind: TurnCueEvent.Kind = .approach, symbol: TurnCueSymbol = .turnLeft,
                   meters: Double? = 91.44) -> TurnCueEvent {
    TurnCueEvent(kind: kind, junctionId: 1, symbol: symbol, spoken: "spoken", shortText: "Turn left",
                 distanceMeters: meters)
}

private func ctx(enabled: Bool = true, active: Bool = false, rec: String = "r1",
                 trail: String = "Crosscut Trail", speaks: Bool = false) -> TurnCueNotificationContext {
    .init(enabled: enabled, appActive: active, recordingId: rec, trailName: trail,
          units: .imperial, voiceWillSpeak: speaks)
}

@MainActor
struct TurnCueNotificationTests {
    @Test func foregroundPostsNothing() async {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        #expect(await n.handle(event(), ctx(active: true)) == nil)
        #expect(c.posted.isEmpty)
    }

    @Test func disabledPostsNothing() async {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        #expect(await n.handle(event(), ctx(enabled: false)) == nil)
        #expect(c.posted.isEmpty)
    }

    @Test func backgroundEnabledPostsOnePerCueWithSpokenPhrasing() async throws {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        await n.handle(event(.approach, meters: 91.44), ctx())
        await n.handle(event(.atJunction, meters: nil), ctx())
        await n.handle(event(.offTrail, symbol: .offTrail, meters: nil), ctx())
        await n.handle(event(.backOnTrail, symbol: .backOnTrail, meters: nil), ctx())
        #expect(c.posted.map(\.title) == ["Turn left in 300 ft", "Turn left now", "Off trail", "Back on trail"])
        #expect(c.posted.allSatisfy { $0.body == "Crosscut Trail" })
        #expect(c.posted.allSatisfy { $0.level == .active })
        #expect(c.posted.allSatisfy { $0.deepLink == LiveActivityDeepLink.activeHikeURL.absoluteString })
    }

    @Test func gpsWeakIsNeverPosted() async {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        #expect(await n.handle(event(.gpsWeak, symbol: .gpsWeak, meters: nil), ctx()) == nil)
        #expect(c.posted.isEmpty)
    }

    @Test func metricUnitsFollowTheSetting() async {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        var m = ctx()
        m.units = .metric
        await n.handle(event(.approach, meters: 100), m)
        #expect(c.posted.first?.title == "Turn left in 100 m")
    }

    @Test func replacesInsteadOfStacking() async {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        await n.handle(event(.approach, meters: 91.44), ctx())
        await n.handle(event(.atJunction, meters: nil), ctx())
        #expect(Set(c.posted.map(\.identifier)).count == 1)
        #expect(Set(c.posted.map(\.threadIdentifier)).count == 1)
        #expect(c.posted.first?.identifier == TurnCueNotificationPlanner.identifier(forRecording: "r1"))
        await n.handle(event(), ctx(rec: "r2"))
        #expect(c.posted.last?.threadIdentifier != c.posted.first?.threadIdentifier)
    }

    @Test func repeatedIdenticalCueIsDeduped() async {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        await n.handle(event(), ctx())
        await n.handle(event(), ctx())
        #expect(c.posted.count == 1)
    }

    @Test func endingTheRecordingClearsAndStopsPosting() async {
        let c = FakeCenter()
        let n = TurnCueNotifier(center: c)
        await n.handle(event(), ctx())
        n.recordingEnded(recordingId: "r1")
        #expect(c.removed == [TurnCueNotificationPlanner.identifier(forRecording: "r1")])
        #expect(await n.handle(event(.atJunction, meters: nil), ctx()) == nil)
        #expect(c.posted.count == 1)
    }

    @Test func deniedOrUndeterminedAuthorizationPostsNothingAndNeverPrompts() async {
        for status in [TurnCueNotificationAuthorization.denied, .notDetermined] {
            let c = FakeCenter(status)
            let n = TurnCueNotifier(center: c)
            #expect(await n.handle(event(), ctx()) == nil)
            #expect(c.posted.isEmpty && c.requests == 0)
        }
    }

    @Test func voiceSpeakingMakesItSilentOtherwiseDefaultSound() {
        #expect(TurnCueNotificationPlanner.notification(for: event(), ctx(speaks: true))?.silent == true)
        #expect(TurnCueNotificationPlanner.notification(for: event(), ctx(speaks: false))?.silent == false)
    }

    @Test func settingDefaultsOff() throws {
        let d = try #require(UserDefaults(suiteName: "TurnCueNotificationTests-\(UUID().uuidString)"))
        #expect(d.bool(forKey: StorageKeys.turnCueNotifyLocked) == false)
    }
}

/// Live Activity fixes B (name once) and C (Finish hidden off trail).
struct LiveActivityOffTrailRowTests {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func snap(next: LiveActivityNextTurn?, estimates: LiveActivityEstimates) -> LiveActivityRecordingSnapshot {
        LiveActivityRecordingSnapshot(
            recordingId: "r1", startedAt: now.addingTimeInterval(-600), isWalk: false,
            distanceMeters: 1000, gpsStatus: .good, title: "Hidden Valley Trail", subtitle: nil,
            trailLengthMeters: nil, estimates: estimates, usesMetric: false, nextTurn: next)
    }

    private func off(_ kind: TurnCueEvent.Kind) -> LiveActivityNextTurn {
        LiveActivityNextTurn(kind: kind, symbol: kind == .offTrail ? .offTrail : .backOnTrail,
                             trailName: "Hidden Valley Trail", distanceMeters: nil)
    }

    @Test func offAndBackShowTheTrailNameOnce() {
        for kind in [TurnCueEvent.Kind.offTrail, .backOnTrail] {
            var s = HikeActivityStateMapper.contentState(from: snap(next: off(kind), estimates: .none), now: now)
            #expect(s.nextTurnText?.contains("Hidden Valley Trail") == true)
            #expect(s.nextTurnSubtitle == nil)
            // A state carrying the name in both fields (older build / fixture) still shows it once.
            s.nextTurnTrail = "Hidden Valley Trail"
            #expect(s.nextTurnSubtitle == nil)
        }
    }

    @Test func turnCuesStillShowTheTrailAsSubtitle() {
        let turn = LiveActivityNextTurn(kind: .approach, symbol: .turnLeft,
                                        trailName: "Crosscut Trail", distanceMeters: 91.44)
        let s = HikeActivityStateMapper.contentState(from: snap(next: turn, estimates: .none), now: now)
        #expect(s.nextTurnText == "Turn left in 300 ft" && s.nextTurnSubtitle == "Crosscut Trail")
    }

    @Test func finishIsHiddenOffTrailEvenWhenTheEstimateStillHasAValue() {
        let est = LiveActivityEstimates(trailEndSeconds: 1500, backSeconds: 2400)
        let s = HikeActivityStateMapper.contentState(from: snap(next: off(.offTrail), estimates: est), now: now)
        #expect(s.estimatedTrailEndAt == nil && s.estimatedTrailEndApprox == nil)
        #expect(s.estimatedBackAt != nil)
        let back = HikeActivityStateMapper.contentState(from: snap(next: off(.backOnTrail), estimates: est), now: now)
        #expect(back.estimatedTrailEndAt != nil && back.estimatedBackAt != nil)
    }

    @MainActor
    @Test func offTrailEstimateYieldsNoSeconds() {
        let e = HikeEstimate(distanceMeters: nil, durationSeconds: nil, status: .offTrail(rejoinMeters: 40),
                             targetName: "Hidden Valley Trail", computedAt: now)
        #expect(LiveActivityEstimateSource.seconds(e, now: now) == nil)
        // Even a stale-valued off-trail estimate is rejected by status.
        var withValue = e
        withValue.durationSeconds = 900
        #expect(LiveActivityEstimateSource.seconds(withValue, now: now) == nil)
    }
}
