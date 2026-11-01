import Foundation
import Testing
@testable import SouthMountainExplorer

struct LiveActivityDeepLinkTests {
    @Test func parsesActiveHike() {
        #expect(LiveActivityDeepLink.parse(URL(string: "trekdex://hike/active")!) == .activeHike)
        #expect(LiveActivityDeepLink.parse(LiveActivityDeepLink.activeHikeURL) == .activeHike)
    }
    @Test func rejectsOthers() {
        #expect(LiveActivityDeepLink.parse(URL(string: "trekdex://area/active")!) == nil)
        #expect(LiveActivityDeepLink.parse(URL(string: "https://hike/active")!) == nil)
        #expect(LiveActivityDeepLink.parse(URL(string: "trekdex://hike/other")!) == nil)
        #expect(LiveActivityDeepLink.parse(URL(string: "garbage")!) == nil)
    }
    @Test func routing() {
        func rec(_ mode: RecordingMode) -> ActiveRecording {
            ActiveRecording(areaId: "a1", mode: mode, trailId: nil, startedAt: Date(), path: [],
                            distanceMi: 0, priorCompleteTrailIds: nil)
        }
        #expect(LiveActivityRouting.target(for: rec(.walk)) == .walk)
        #expect(LiveActivityRouting.target(for: rec(.trail)) == .area("a1"))
        #expect(LiveActivityRouting.target(for: rec(.roam)) == .area("a1"))
        #expect(LiveActivityRouting.target(for: nil) == .none)
    }
    @Test func accessibilityLabel() {
        var s = HikeActivityAttributes.ContentState(
            status: .gpsPaused, title: "Hidden Valley", subtitle: nil, distanceText: "3.40 mi",
            trailLengthText: nil, progress: nil, estimatedTrailEndAt: nil, estimatedBackAt: nil, endedAt: nil)
        let label = HikeActivityAccessibility.label(for: s)
        #expect(label.contains("Hidden Valley") && label.contains("3.40 mi") && label.contains("GPS paused"))
        #expect(!label.contains("Finish") && !label.contains("Back by"))
        s.status = .recording
        s.estimatedBackAt = Date().addingTimeInterval(600)
        let withEst = HikeActivityAccessibility.label(for: s)
        #expect(withEst.contains("Back by around") && withEst.hasPrefix("Recording"))
    }
}
