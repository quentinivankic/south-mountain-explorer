import Foundation
import Testing
@testable import SouthMountainExplorer

struct HikeActivityStateMapperTests {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func snap(status: GpsIngest.ActiveStatus = .good,
                      distance: Double = 1000,
                      title: String = "Hidden Valley",
                      trailLength: Double? = nil,
                      estimates: LiveActivityEstimates = .none,
                      isWalk: Bool = false,
                      metric: Bool = false) -> LiveActivityRecordingSnapshot {
        LiveActivityRecordingSnapshot(
            recordingId: "r1", startedAt: now.addingTimeInterval(-600), isWalk: isWalk,
            distanceMeters: distance, gpsStatus: status, title: title, subtitle: nil,
            trailLengthMeters: trailLength, estimates: estimates, usesMetric: metric)
    }

    @Test func statusMapping() {
        #expect(HikeActivityStateMapper.contentState(from: snap(status: .good), now: now).status == .recording)
        #expect(HikeActivityStateMapper.contentState(from: snap(status: .waiting), now: now).status == .waitingForGPS)
        #expect(HikeActivityStateMapper.contentState(from: snap(status: .paused), now: now).status == .gpsPaused)
        #expect(HikeActivityStateMapper.contentState(from: snap(status: .recovered), now: now).status == .gpsRecovered)
    }

    @Test func finishedSetsStatusAndEndedAt() {
        let s = HikeActivityStateMapper.contentState(
            from: snap(estimates: .init(trailEndSeconds: 600, backSeconds: 600)), now: now, finishedAt: now)
        #expect(s.status == .finished)
        #expect(s.endedAt == now)
        #expect(s.estimatedTrailEndAt == nil && s.estimatedBackAt == nil)
    }

    @Test func progressRules() {
        let half = HikeActivityStateMapper.contentState(from: snap(distance: 1000, trailLength: 2000), now: now)
        #expect(half.progress == 0.5)
        #expect(half.trailLengthText != nil)
        #expect(HikeActivityStateMapper.contentState(from: snap(distance: 2500, trailLength: 2000), now: now).progress == nil)
        let noLen = HikeActivityStateMapper.contentState(from: snap(trailLength: nil), now: now)
        #expect(noLen.progress == nil && noLen.trailLengthText == nil)
        #expect(HikeActivityStateMapper.contentState(from: snap(trailLength: 0), now: now).progress == nil)
    }

    @Test func estimatesBecomeAbsoluteDates() {
        let s = HikeActivityStateMapper.contentState(
            from: snap(estimates: .init(trailEndSeconds: 600, backSeconds: 1200)), now: now)
        #expect(s.estimatedTrailEndAt == now.addingTimeInterval(600))
        #expect(s.estimatedBackAt == now.addingTimeInterval(1200))
    }

    @Test func estimatesDroppedWhenGpsDegradedOrPast() {
        let est = LiveActivityEstimates(trailEndSeconds: 600, backSeconds: 600)
        for status in [GpsIngest.ActiveStatus.paused, .waiting] {
            let s = HikeActivityStateMapper.contentState(from: snap(status: status, estimates: est), now: now)
            #expect(s.estimatedTrailEndAt == nil && s.estimatedBackAt == nil)
        }
        let past = HikeActivityStateMapper.contentState(
            from: snap(estimates: .init(trailEndSeconds: -5, backSeconds: 0)), now: now)
        #expect(past.estimatedTrailEndAt == nil && past.estimatedBackAt == nil)
        let nilEst = HikeActivityStateMapper.contentState(from: snap(), now: now)
        #expect(nilEst.estimatedTrailEndAt == nil && nilEst.estimatedBackAt == nil)
    }

    @Test func titleFallbacks() {
        #expect(HikeActivityStateMapper.contentState(from: snap(title: "  "), now: now).title == "Hiking")
        #expect(HikeActivityStateMapper.contentState(from: snap(title: "", isWalk: true), now: now).title == "Walk")
    }

    @Test func badDistanceClampsToZero() {
        let zero = UnitFormatter.distance(meters: 0, units: .imperial)
        #expect(HikeActivityStateMapper.contentState(from: snap(distance: -5), now: now).distanceText == zero)
        #expect(HikeActivityStateMapper.contentState(from: snap(distance: .nan), now: now).distanceText == zero)
    }

    @Test func unitsFollowPreference() {
        #expect(HikeActivityStateMapper.contentState(from: snap(distance: 1609.344), now: now).distanceText.hasSuffix("mi"))
        #expect(HikeActivityStateMapper.contentState(from: snap(distance: 1609.344, metric: true), now: now).distanceText.hasSuffix("km"))
    }
}
