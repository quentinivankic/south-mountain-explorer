import CoreLocation
import Foundation
import Testing
@testable import SouthMountainExplorer

@Suite(.serialized)
@MainActor
struct LocationServiceLifecycleTests {
    @MainActor
    private final class FakeLocationManager: LocationManagerDriving {
        var delegate: (any CLLocationManagerDelegate)?
        var authorizationStatus: CLAuthorizationStatus
        var desiredAccuracy: CLLocationAccuracy = kCLLocationAccuracyThreeKilometers
        var headingFilter: CLLocationDegrees = kCLHeadingFilterNone
        var allowsBackgroundLocationUpdates = false
        var pausesLocationUpdatesAutomatically = true
        var activityType: CLActivityType = .other
        var headingAvailable: Bool

        private(set) var permissionRequestCount = 0
        private(set) var freshFixRequestCount = 0
        private(set) var locationStartCount = 0
        private(set) var locationStopCount = 0
        private(set) var headingStartCount = 0
        private(set) var headingStopCount = 0

        init(
            authorizationStatus: CLAuthorizationStatus,
            headingAvailable: Bool = true
        ) {
            self.authorizationStatus = authorizationStatus
            self.headingAvailable = headingAvailable
        }

        func requestWhenInUseAuthorization() { permissionRequestCount += 1 }
        func requestLocation() { freshFixRequestCount += 1 }
        func startUpdatingLocation() { locationStartCount += 1 }
        func stopUpdatingLocation() { locationStopCount += 1 }
        func startUpdatingHeading() { headingStartCount += 1 }
        func stopUpdatingHeading() { headingStopCount += 1 }
    }

    private func makeDefaults() throws -> (UserDefaults, String) {
        let name = "LocationServiceLifecycleTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        defaults.removePersistentDomain(forName: name)
        return (defaults, name)
    }

    private func makeSubject(
        authorizationStatus: CLAuthorizationStatus = .authorizedWhenInUse,
        headingAvailable: Bool = true
    ) throws -> (LocationService, FakeLocationManager, UserDefaults, String) {
        let (defaults, suiteName) = try makeDefaults()
        let manager = FakeLocationManager(
            authorizationStatus: authorizationStatus,
            headingAvailable: headingAvailable
        )
        let service = LocationService(manager: manager, userDefaults: defaults)
        return (service, manager, defaults, suiteName)
    }

    @Test func foregroundSurvivesRecordingStopWithoutRestartingHardware() throws {
        let (service, manager, defaults, suiteName) = try makeSubject()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let foreground = ForegroundLocationDemandToken()

        service.setApplicationActive(true)
        service.acquireForegroundLocationDemand(foreground)
        #expect(manager.locationStartCount == 1)
        #expect(manager.locationStopCount == 0)

        service.startBackgroundTracking()
        #expect(manager.locationStartCount == 1)
        #expect(manager.allowsBackgroundLocationUpdates)
        #expect(!manager.pausesLocationUpdatesAutomatically)
        #expect(manager.activityType == .fitness)

        service.stopBackgroundTracking()
        #expect(manager.locationStartCount == 1)
        #expect(manager.locationStopCount == 0)
        #expect(!manager.allowsBackgroundLocationUpdates)
        #expect(manager.pausesLocationUpdatesAutomatically)
        #expect(manager.activityType == .other)

        service.releaseForegroundLocationDemand(foreground)
        #expect(manager.locationStopCount == 1)
    }

    @Test func recordingSurvivesForegroundReleaseAndBackgroundScene() throws {
        let (service, manager, defaults, suiteName) = try makeSubject()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let foreground = ForegroundLocationDemandToken()

        service.setApplicationActive(true)
        service.acquireForegroundLocationDemand(foreground)
        service.startBackgroundTracking()
        service.releaseForegroundLocationDemand(foreground)
        service.setApplicationActive(false)

        #expect(manager.locationStartCount == 1)
        #expect(manager.locationStopCount == 0)
        #expect(manager.allowsBackgroundLocationUpdates)
        #expect(!manager.pausesLocationUpdatesAutomatically)
        #expect(manager.activityType == .fitness)

        service.stopBackgroundTracking()
        #expect(manager.locationStopCount == 1)
    }

    @Test func foregroundTokensAreReferenceCountedAndDuplicateSafe() throws {
        let (service, manager, defaults, suiteName) = try makeSubject()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let first = ForegroundLocationDemandToken()
        let second = ForegroundLocationDemandToken()

        service.setApplicationActive(true)
        service.acquireForegroundLocationDemand(first)
        service.acquireForegroundLocationDemand(first)
        service.acquireForegroundLocationDemand(second)
        #expect(manager.locationStartCount == 1)

        service.releaseForegroundLocationDemand(first)
        service.releaseForegroundLocationDemand(first)
        #expect(manager.locationStopCount == 0)

        service.releaseForegroundLocationDemand(second)
        service.releaseForegroundLocationDemand(second)
        #expect(manager.locationStopCount == 1)
    }

    @Test func headingTokensAreReferenceCountedEdgeTriggeredAndReleased() throws {
        let (service, manager, defaults, suiteName) = try makeSubject()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let first = HeadingDemandToken()
        let second = HeadingDemandToken()

        service.setApplicationActive(true)
        service.acquireHeadingDemand(first)
        service.acquireHeadingDemand(first)
        service.acquireHeadingDemand(second)
        #expect(manager.headingStartCount == 1)

        service.receiveHeading(123)
        #expect(service.liveHeading == 123)

        service.releaseHeadingDemand(first)
        #expect(manager.headingStopCount == 0)
        service.releaseHeadingDemand(second)
        service.releaseHeadingDemand(second)
        #expect(manager.headingStopCount == 1)
        #expect(service.liveHeading == nil)
    }

    @Test func sceneActivityGatesForegroundAndHeadingButRetainsPendingDemand() throws {
        let (service, manager, defaults, suiteName) = try makeSubject()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let foreground = ForegroundLocationDemandToken()
        let heading = HeadingDemandToken()

        service.acquireForegroundLocationDemand(foreground)
        service.acquireHeadingDemand(heading)
        #expect(manager.locationStartCount == 0)
        #expect(manager.headingStartCount == 0)

        service.setApplicationActive(true)
        #expect(manager.locationStartCount == 1)
        #expect(manager.headingStartCount == 1)

        service.setApplicationActive(false)
        service.setApplicationActive(false)
        #expect(manager.locationStopCount == 1)
        #expect(manager.headingStopCount == 1)

        service.setApplicationActive(true)
        #expect(manager.locationStartCount == 2)
        #expect(manager.headingStartCount == 2)
    }

    @Test func authorizationGrantDenyAndRegrantReconcilePendingDemand() throws {
        let (service, manager, defaults, suiteName) = try makeSubject(
            authorizationStatus: .notDetermined
        )
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let foreground = ForegroundLocationDemandToken()
        let heading = HeadingDemandToken()

        service.setApplicationActive(true)
        service.acquireForegroundLocationDemand(foreground)
        service.acquireHeadingDemand(heading)
        #expect(manager.locationStartCount == 0)
        #expect(manager.headingStartCount == 0)

        service.authorizationDidChange(to: .authorizedWhenInUse)
        #expect(manager.locationStartCount == 1)
        #expect(manager.headingStartCount == 1)

        service.authorizationDidChange(to: .denied)
        #expect(manager.locationStopCount == 1)
        #expect(manager.headingStopCount == 1)
        #expect(!manager.allowsBackgroundLocationUpdates)
        #expect(manager.pausesLocationUpdatesAutomatically)
        #expect(manager.activityType == .other)

        service.authorizationDidChange(to: .authorizedAlways)
        #expect(manager.locationStartCount == 2)
        #expect(manager.headingStartCount == 2)
    }

    @Test func recordingStopResetsEveryBackgroundOnlyConfiguration() throws {
        let (service, manager, defaults, suiteName) = try makeSubject()
        defer { defaults.removePersistentDomain(forName: suiteName) }

        service.startBackgroundTracking()
        #expect(manager.locationStartCount == 1)
        #expect(manager.allowsBackgroundLocationUpdates)
        #expect(!manager.pausesLocationUpdatesAutomatically)
        #expect(manager.activityType == .fitness)

        service.stopBackgroundTracking()
        #expect(manager.locationStopCount == 1)
        #expect(!manager.allowsBackgroundLocationUpdates)
        #expect(manager.pausesLocationUpdatesAutomatically)
        #expect(manager.activityType == .other)
    }

    @Test func freshFixCoalescesCancelsAndRemainsContinuousDemandNeutral() throws {
        let (service, manager, defaults, suiteName) = try makeSubject(
            authorizationStatus: .notDetermined
        )
        defer { defaults.removePersistentDomain(forName: suiteName) }

        service.setApplicationActive(true)
        service.requestFreshFix()
        #expect(manager.freshFixRequestCount == 0)
        #expect(manager.locationStartCount == 0)
        #expect(manager.locationStopCount == 0)

        service.authorizationDidChange(to: .authorizedWhenInUse)
        service.requestFreshFix()
        service.requestFreshFix()
        #expect(manager.freshFixRequestCount == 1, "only one one-shot may be in flight")
        #expect(manager.locationStartCount == 0)
        #expect(manager.locationStopCount == 0)

        service.freshFixDidComplete()
        service.requestFreshFix()
        #expect(manager.freshFixRequestCount == 2)

        service.setApplicationActive(false)
        #expect(manager.locationStopCount == 1, "scene loss cancels the pending one-shot")
        service.setApplicationActive(true)
        service.requestFreshFix()
        #expect(manager.freshFixRequestCount == 3)

        let foreground = ForegroundLocationDemandToken()
        service.acquireForegroundLocationDemand(foreground)
        #expect(manager.locationStopCount == 2, "continuous demand cancels the pending one-shot first")
        #expect(manager.locationStartCount == 1)

        service.requestFreshFix()
        #expect(manager.freshFixRequestCount == 3, "the live stream supplies freshness without a conflicting one-shot")
        #expect(manager.locationStartCount == 1)
        #expect(!manager.allowsBackgroundLocationUpdates)
        #expect(manager.pausesLocationUpdatesAutomatically)

        service.releaseForegroundLocationDemand(foreground)
        #expect(manager.locationStopCount == 3)
    }

    @Test func repeatedRecordingStartsAndStopsArePhysicallyIdempotent() throws {
        let (service, manager, defaults, suiteName) = try makeSubject()
        defer { defaults.removePersistentDomain(forName: suiteName) }

        service.startBackgroundTracking()
        service.startBackgroundTracking()
        #expect(manager.locationStartCount == 1)
        #expect(manager.locationStopCount == 0)

        service.stopBackgroundTracking()
        service.stopBackgroundTracking()
        #expect(manager.locationStartCount == 1)
        #expect(manager.locationStopCount == 1)

        service.startBackgroundTracking()
        service.startBackgroundTracking()
        service.stopBackgroundTracking()
        #expect(manager.locationStartCount == 2)
        #expect(manager.locationStopCount == 2)
    }
}
