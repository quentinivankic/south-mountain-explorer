import CoreLocation
import Testing
@testable import SouthMountainExplorer

struct FieldTrustStateTests {
    @Test func exploreLocationOutcomesRemainActionable() {
        let coordinate = CLLocationCoordinate2D(latitude: 33.3, longitude: -112.0)
        let successfulState = ExploreLocationState.resolved(
            result: .success(coordinate),
            hasFallback: false
        )
        let deniedState = ExploreLocationState.resolved(
            result: .denied,
            hasFallback: true
        )
        let unavailableState = ExploreLocationState.resolved(
            result: .unavailable,
            hasFallback: false
        )
        let fallbackState = ExploreLocationState.resolved(
            result: .unavailable,
            hasFallback: true
        )

        #expect(successfulState == .available)
        #expect(deniedState == .denied)
        #expect(unavailableState == .unavailable)
        #expect(fallbackState == .fallback)
    }

    @Test func everyCompetingCameraOwnerInvalidatesPendingRecenter() {
        // Keep this count explicit: adding or removing an owner without updating
        // the gate is a camera-ownership regression, not a harmless enum edit.
        #expect(RecenterCameraOwner.allCases.count == 8)
        for owner in RecenterCameraOwner.allCases {
            let current = 4
            let invalidated = RecenterRequestGate.invalidatedGeneration(
                current: current,
                owner: owner
            )

            #expect(invalidated == 5, "A competing camera owner did not advance the generation")
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

    @MainActor
    @Test func recordingControlVisibilityTracksUniqueMountedPanels() {
        let visibility = RecordingControlVisibility()
        let areaPanel = RecordingControlVisibility.Token()
        let walkPanel = RecordingControlVisibility.Token()

        visibility.acquire(areaPanel)
        visibility.acquire(areaPanel)
        #expect(visibility.hasLocalControls)
        #expect(visibility.localControlCount == 1)

        visibility.acquire(walkPanel)
        visibility.release(areaPanel)
        #expect(visibility.hasLocalControls)
        #expect(visibility.localControlCount == 1)

        visibility.release(areaPanel)
        visibility.release(walkPanel)
        #expect(!visibility.hasLocalControls)
        #expect(visibility.localControlCount == 0)
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
