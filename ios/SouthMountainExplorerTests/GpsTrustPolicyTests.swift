import Foundation
import Testing
@testable import SouthMountainExplorer

/// `GpsTrustPolicy` replaced numbers that were copied into three guides.
/// These pin the values the guides used before, so the move changed nothing,
/// and prove every guide reads the shared policy.
struct GpsTrustPolicyTests {
    @Test func policyKeepsTheNumbersTheGuidesUsedBefore() {
        #expect(GpsTrustPolicy.maxAcceptableAccuracy == 50.0)
        #expect(GpsTrustPolicy.staleFixSeconds == 10.0)
        #expect(GpsTrustPolicy.minSpokenSpacing == 6.0)
        #expect(GpsTrustPolicy.weakAfterDegradedSeconds == 15.0)
        #expect(GpsTrustPolicy.weakNeedsGoodSeconds == 30.0)
        #expect(GpsTrustPolicy.weakMinIntervalSeconds == 300.0)
        #expect(GpsTrustPolicy.offLineMeters == 40.0)
        #expect(GpsTrustPolicy.offLineSeconds == 20.0)
        #expect(GpsTrustPolicy.offLineFixes == 3)
        #expect(GpsTrustPolicy.offLineMoveMeters == 10.0)
        #expect(GpsTrustPolicy.armSeconds == 10.0)
        #expect(GpsTrustPolicy.backOnFraction == 0.6)
        #expect(GpsTrustPolicy.backOnSeconds == 6.0)
        #expect(GpsTrustPolicy.repeatSeconds == 120.0)
        #expect(GpsTrustPolicy.repeatFartherMeters == 50.0)
    }

    @Test func trailEngineConfigReadsThePolicy() {
        let c = TurnCueConfig(units: .imperial)
        #expect(c.maxAcceptableAccuracy == GpsTrustPolicy.maxAcceptableAccuracy)
        #expect(c.staleFixSeconds == GpsTrustPolicy.staleFixSeconds)
        #expect(c.minSpokenSpacing == GpsTrustPolicy.minSpokenSpacing)
        #expect(c.weakAfterDegradedSeconds == GpsTrustPolicy.weakAfterDegradedSeconds)
        #expect(c.weakNeedsGoodSeconds == GpsTrustPolicy.weakNeedsGoodSeconds)
        #expect(c.weakMinIntervalSeconds == GpsTrustPolicy.weakMinIntervalSeconds)
    }

    @Test func planGuideConfigReadsThePolicy() {
        let c = PlanGuideConfig(units: .imperial)
        #expect(c.maxAcceptableAccuracy == GpsTrustPolicy.maxAcceptableAccuracy)
        #expect(c.staleFixSeconds == GpsTrustPolicy.staleFixSeconds)
        #expect(c.minSpokenSpacing == GpsTrustPolicy.minSpokenSpacing)
        #expect(c.weakAfterDegradedSeconds == GpsTrustPolicy.weakAfterDegradedSeconds)
        #expect(c.weakNeedsGoodSeconds == GpsTrustPolicy.weakNeedsGoodSeconds)
        #expect(c.weakMinIntervalSeconds == GpsTrustPolicy.weakMinIntervalSeconds)
        #expect(c.offRouteMeters == GpsTrustPolicy.offLineMeters)
        #expect(c.offRouteSeconds == GpsTrustPolicy.offLineSeconds)
        #expect(c.offRouteFixes == GpsTrustPolicy.offLineFixes)
        #expect(c.offRouteMoveMeters == GpsTrustPolicy.offLineMoveMeters)
        #expect(c.armSeconds == GpsTrustPolicy.armSeconds)
        #expect(c.backOnFraction == GpsTrustPolicy.backOnFraction)
        #expect(c.backOnSeconds == GpsTrustPolicy.backOnSeconds)
        #expect(c.repeatSeconds == GpsTrustPolicy.repeatSeconds)
        #expect(c.repeatFartherMeters == GpsTrustPolicy.repeatFartherMeters)
    }

    @Test func progressMatcherConfigReadsThePolicy() {
        let c = PlanProgressMatcher.Config()
        #expect(c.maxAcceptableAccuracy == GpsTrustPolicy.maxAcceptableAccuracy)
        #expect(c.matchMeters == GpsTrustPolicy.offLineMeters)
    }
}
