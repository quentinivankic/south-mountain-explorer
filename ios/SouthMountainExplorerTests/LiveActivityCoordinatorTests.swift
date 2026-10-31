import Foundation
import Testing
@testable import SouthMountainExplorer

@MainActor
private final class FakeClient: LiveActivityClient {
    var areActivitiesEnabled = true
    var activities: [LiveActivityExisting] = []
    var requests = 0
    var updates = 0
    var ends: [(id: String, dismissAfter: TimeInterval?, hasFinal: Bool)] = []
    var requestError: Error?
    func currentActivities() -> [LiveActivityExisting] { activities }
    func request(attributes: HikeActivityAttributes, state: HikeActivityAttributes.ContentState,
                 staleDate: Date) throws -> String {
        if let requestError { throw requestError }
        requests += 1
        let id = "act-\(requests)"
        activities.append(.init(id: id, recordingId: attributes.recordingId, isEnded: false))
        return id
    }
    func update(id: String, state: HikeActivityAttributes.ContentState, staleDate: Date?) async { updates += 1 }
    func end(id: String, finalState: HikeActivityAttributes.ContentState?, dismissAfter: TimeInterval?) async {
        ends.append((id, dismissAfter, finalState != nil))
        activities.removeAll { $0.id == id }
    }
    func enablementUpdates() -> AsyncStream<Bool> { AsyncStream { $0.finish() } }
}

private struct Boom: Error {}

@MainActor
struct LiveActivityCoordinatorTests {
    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    private func snapshot(distance: Double = 0) -> LiveActivityRecordingSnapshot {
        .init(recordingId: "r1", startedAt: t0, isWalk: false, distanceMeters: distance, gpsStatus: .good,
              title: "Trail", subtitle: nil, trailLengthMeters: nil, estimates: .none, usesMetric: false)
    }

    private final class Box { var observed = LiveActivityObservedState(snapshot: nil, isStopping: false, isRecoveryBlocked: false) }

    private func make(_ client: FakeClient, _ box: Box, finishedLookup: @escaping @MainActor (String) async -> Bool = { _ in false })
        -> LiveActivityCoordinator {
        LiveActivityCoordinator(client: client, observedProvider: { box.observed },
                                finishedLookup: finishedLookup, log: { _, _ in })
    }
    private func eval(_ c: LiveActivityCoordinator, _ sec: TimeInterval) async {
        await c.evaluate(now: t0.addingTimeInterval(sec))
    }

    @Test func startsExactlyOneActivity() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        box.observed.snapshot = snapshot()
        await eval(c, 0); await eval(c, 1); await eval(c, 2)
        #expect(client.requests == 1)
    }

    @Test func rapidEventsCauseFewUpdates() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        for i in 1...100 {
            box.observed.snapshot = snapshot(distance: Double(i) * 0.1)
            await eval(c, Double(i) * 0.05)
        }
        #expect(client.updates <= 3)
        #expect(client.requests == 1)
    }

    @Test func stopEndsWithShortDismiss() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        box.observed.snapshot = snapshot(distance: 100)
        await eval(c, 0)
        box.observed.isStopping = true
        await eval(c, 10)
        box.observed = .init(snapshot: nil, isStopping: true, isRecoveryBlocked: false)
        await eval(c, 11)
        #expect(client.ends.count == 1)
        #expect(client.ends[0].dismissAfter == 8)
        #expect(client.ends[0].hasFinal)
    }

    @Test func fastSaveUsesHistoryLookup() async {
        let client = FakeClient(); let box = Box()
        let c = make(client, box, finishedLookup: { $0 == "r1" })
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        box.observed = .init(snapshot: nil, isStopping: false, isRecoveryBlocked: false)
        await eval(c, 1)
        #expect(client.ends.first?.dismissAfter == 8)
    }

    @Test func discardEndsImmediately() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        box.observed = .init(snapshot: nil, isStopping: false, isRecoveryBlocked: false)
        await eval(c, 5)
        #expect(client.ends.count == 1)
        #expect(client.ends[0].dismissAfter == nil)
        #expect(!client.ends[0].hasFinal)
    }

    @Test func failedStopKeepsActivity() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        box.observed.isStopping = true
        await eval(c, 1)
        box.observed.isStopping = false
        await eval(c, 2)
        #expect(client.ends.isEmpty)
        #expect(client.requests == 1)
    }

    @Test func requestFailureRetriesNoSoonerThan60sAndMaxThree() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        client.requestError = Boom()
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        client.requestError = nil
        await eval(c, 30)
        #expect(client.requests == 0)
        await eval(c, 61)
        #expect(client.requests == 1)

        let client2 = FakeClient(); let c2 = make(client2, box)
        client2.requestError = Boom()
        for i in 0..<6 { await eval(c2, Double(i) * 61) }
        client2.requestError = nil
        await eval(c2, 6 * 61)
        #expect(client2.requests == 0)
    }

    @Test func relaunchAdoptsExistingActivity() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        client.activities = [.init(id: "old", recordingId: "r1", isEnded: false)]
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        #expect(client.requests == 0)
        #expect(client.updates == 1)
    }

    @Test func relaunchWithNoRecordingEndsLeftovers() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        client.activities = [.init(id: "old", recordingId: "r1", isEnded: false)]
        await eval(c, 0)
        #expect(client.ends.count == 1)
        #expect(client.ends[0].dismissAfter == nil)
    }

    @Test func enablingMidHikeStarts() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        client.areActivitiesEnabled = false
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        #expect(client.requests == 0)
        client.areActivitiesEnabled = true
        await eval(c, 1)
        #expect(client.requests == 1)
    }

    @Test func swipedAwayActivityIsNotRestarted() async {
        let client = FakeClient(); let box = Box(); let c = make(client, box)
        box.observed.snapshot = snapshot()
        await eval(c, 0)
        client.activities.removeAll()
        await eval(c, 100)
        await eval(c, 200)
        #expect(client.requests == 1)
    }
}
