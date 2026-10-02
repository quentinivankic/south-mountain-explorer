import CoreLocation
import Testing
@testable import SouthMountainExplorer

struct FieldTrustStateTests {
    @Test func exploreLocationOutcomesRemainActionable() {
        let coordinate = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)

        #expect(ExploreLocationState.resolved(
            result: .success(coordinate),
            hasFallback: false
        ) == .available)
        #expect(ExploreLocationState.resolved(
            result: .denied,
            hasFallback: true
        ) == .denied)
        #expect(ExploreLocationState.resolved(
            result: .unavailable,
            hasFallback: false
        ) == .unavailable)
        #expect(ExploreLocationState.resolved(
            result: .unavailable,
            hasFallback: true
        ) == .fallback)
    }

    @Test func everyCompetingCameraOwnerInvalidatesPendingRecenter() {
        for owner in RecenterCameraOwner.allCases {
            let current = 4
            let invalidated = RecenterRequestGate.invalidatedGeneration(
                current: current,
                owner: owner
            )

            #expect(invalidated == 5, "\(owner) did not advance the camera generation")
            #expect(!RecenterRequestGate.mayApply(request: current, current: invalidated))
        }
        #expect(RecenterRequestGate.mayApply(request: 4, current: 4))
    }

    @Test func newWalkPartialScopeIsExplicitAndStartable() {
        let state = WalkGeometryLoadState.resolved(
            requested: 12,
            loaded: 8,
            restoringActiveWalk: false
        )

        #expect(state == .partial(loaded: 8, total: 12))
        #expect(state.allowsNewWalkStart)
    }

    @Test func restoredWalkPartialScopeBlocksStopSurface() {
        let state = WalkGeometryLoadState.resolved(
            requested: 12,
            loaded: 8,
            restoringActiveWalk: true
        )

        #expect(state == .restoringPartial(loaded: 8, total: 12))
        #expect(!state.allowsNewWalkStart)
        #expect(WalkGeometryLoadState.resolved(
            requested: 12,
            loaded: 12,
            restoringActiveWalk: true
        ) == .ready)
    }

    @Test func rootStopGateBlocksPreHydrationDuplicates() {
        #expect(RootRecordingStopGate.canBegin(
            isRootStopInFlight: false,
            hasSummaryRoute: false,
            hasActiveRecording: true,
            serviceIsStopping: false
        ))
        #expect(!RootRecordingStopGate.canBegin(
            isRootStopInFlight: true,
            hasSummaryRoute: false,
            hasActiveRecording: true,
            serviceIsStopping: false
        ))
        #expect(!RootRecordingStopGate.canBegin(
            isRootStopInFlight: false,
            hasSummaryRoute: false,
            hasActiveRecording: true,
            serviceIsStopping: true
        ))
    }
}
