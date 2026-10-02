import CoreLocation
import Foundation
import Testing
@testable import SouthMountainExplorer

@Suite(.serialized)
@MainActor
struct LocationOwnershipTests {
    private final class FakeLocationManager: LocationManagerControlling {
        var authorizationStatus: CLAuthorizationStatus = .authorizedWhenInUse
        var desiredAccuracy: CLLocationAccuracy = kCLLocationAccuracyThreeKilometers
        var allowsBackgroundLocationUpdates = false
        var pausesLocationUpdatesAutomatically = true
        var activityType: CLActivityType = .other
        var headingFilter: CLLocationDegrees = kCLHeadingFilterNone
        var headingAvailable = true

        weak var delegate: (any CLLocationManagerDelegate)?
        private(set) var locationStartCount = 0
        private(set) var locationStopCount = 0
        private(set) var headingStartCount = 0
        private(set) var headingStopCount = 0
        private(set) var requestLocationCount = 0
        private(set) var permissionRequestCount = 0

        func setDelegate(_ delegate: (any CLLocationManagerDelegate)?) {
            self.delegate = delegate
        }

        func requestWhenInUseAuthorization() { permissionRequestCount += 1 }
        func requestLocation() { requestLocationCount += 1 }
        func startUpdatingLocation() { locationStartCount += 1 }
        func stopUpdatingLocation() { locationStopCount += 1 }
        func startUpdatingHeading() { headingStartCount += 1 }
        func stopUpdatingHeading() { headingStopCount += 1 }

        func deliverLocation(latitude: Double = 33.3, longitude: Double = -112.0) {
            let location = CLLocation(
                coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude),
                altitude: 400,
                horizontalAccuracy: 5,
                verticalAccuracy: 5,
                timestamp: Date()
            )
            delegate?.locationManager?(CLLocationManager(), didUpdateLocations: [location])
        }

        func failLocation() {
            delegate?.locationManager?(
                CLLocationManager(),
                didFailWithError: CLError(.locationUnknown)
            )
        }

        func notifyAuthorizationChanged() {
            delegate?.locationManagerDidChangeAuthorization?(CLLocationManager())
        }
    }

    private func makeDefaults() throws -> (UserDefaults, String) {
        let name = "LocationOwnershipTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        defaults.removePersistentDomain(forName: name)
        return (defaults, name)
    }

    private func makeService(
        manager: FakeLocationManager,
        defaults: UserDefaults
    ) -> LocationService {
        LocationService(manager: manager, userDefaults: defaults)
    }

    @Test func recordingDemandOutranksForegroundAndSurvivesViewRelease() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let manager = FakeLocationManager()
        let service = makeService(manager: manager, defaults: defaults)
        let home = LocationConsumerID("home")
        let area = LocationConsumerID("area")

        service.acquireLocation(for: home, accuracy: .coarse)
        #expect(manager.locationStartCount == 1)
        #expect(manager.desiredAccuracy == kCLLocationAccuracyHundredMeters)
        #expect(!manager.allowsBackgroundLocationUpdates)

        service.acquireRecordingLocation()
        service.acquireLocation(for: area, accuracy: .precise)
        #expect(manager.locationStartCount == 1)
        #expect(manager.desiredAccuracy == kCLLocationAccuracyBest)
        #expect(manager.allowsBackgroundLocationUpdates)
        #expect(!manager.pausesLocationUpdatesAutomatically)
        #expect(manager.activityType == .fitness)

        service.releaseLocation(for: home)
        service.releaseLocation(for: area)
        #expect(manager.locationStopCount == 0)
        #expect(manager.allowsBackgroundLocationUpdates)

        service.releaseRecordingLocation()
        #expect(manager.locationStopCount == 1)
        #expect(!manager.allowsBackgroundLocationUpdates)
    }

    @Test func foregroundAreaAndWalkOwnershipIsConsumerKeyedAndIdempotent() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let manager = FakeLocationManager()
        let service = makeService(manager: manager, defaults: defaults)
        let area = LocationConsumerID("area")
        let walk = LocationConsumerID("walk")

        service.acquireLocation(for: area, accuracy: .precise)
        service.acquireLocation(for: area, accuracy: .precise)
        service.acquireLocation(for: walk, accuracy: .precise)
        #expect(manager.locationStartCount == 1)

        service.releaseLocation(for: area)
        service.releaseLocation(for: area)
        #expect(manager.locationStopCount == 0)
        service.releaseLocation(for: walk)
        service.releaseLocation(for: walk)
        #expect(manager.locationStopCount == 1)
    }

    @Test func backgroundStopsForegroundDemandButNotRecordingAndForegroundResumes() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let manager = FakeLocationManager()
        let service = makeService(manager: manager, defaults: defaults)
        let area = LocationConsumerID("area")

        service.acquireLocation(for: area, accuracy: .precise)
        service.acquireHeading(for: area)
        #expect(manager.locationStartCount == 1)
        #expect(manager.headingStartCount == 1)

        service.setApplicationActive(false)
        #expect(manager.locationStopCount == 1)
        #expect(manager.headingStopCount == 1)

        service.acquireRecordingLocation()
        #expect(manager.locationStartCount == 2)
        #expect(manager.allowsBackgroundLocationUpdates)
        #expect(manager.headingStartCount == 1)

        service.setApplicationActive(true)
        #expect(manager.locationStartCount == 2)
        #expect(manager.headingStartCount == 2)

        service.releaseRecordingLocation()
        #expect(manager.locationStopCount == 1, "the restored foreground Area demand remains")
        #expect(!manager.allowsBackgroundLocationUpdates)
        service.releaseLocation(for: area)
        service.releaseHeading(for: area)
        #expect(manager.locationStopCount == 2)
        #expect(manager.headingStopCount == 2)
    }

    @Test func headingStopsOnlyAfterFinalConsumerAndRestartsAfterForeground() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let manager = FakeLocationManager()
        let service = makeService(manager: manager, defaults: defaults)
        let area = LocationConsumerID("area")
        let walk = LocationConsumerID("walk")

        service.acquireHeading(for: area)
        service.acquireHeading(for: walk)
        #expect(manager.headingStartCount == 1)
        service.releaseHeading(for: area)
        #expect(manager.headingStopCount == 0)
        service.setApplicationActive(false)
        #expect(manager.headingStopCount == 1)
        service.setApplicationActive(true)
        #expect(manager.headingStartCount == 2)
        service.releaseHeading(for: walk)
        #expect(manager.headingStopCount == 2)
    }

    @Test func oneShotFixNeverEnablesBackgroundAndCanRunAfterRecordingStops() async throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let manager = FakeLocationManager()
        let service = makeService(manager: manager, defaults: defaults)
        let home = LocationConsumerID("home")

        service.acquireRecordingLocation()
        let duringRecordingTask = Task { @MainActor in
            await service.requestOneShotFix(for: home, accuracy: .coarse)
        }
        await Task.yield()
        #expect(manager.requestLocationCount == 1)
        #expect(manager.desiredAccuracy == kCLLocationAccuracyBest)
        #expect(manager.allowsBackgroundLocationUpdates)
        manager.deliverLocation()
        guard case .success = await duringRecordingTask.value else {
            Issue.record("expected the Home one-shot to complete during recording")
            return
        }

        service.releaseRecordingLocation()
        #expect(manager.locationStopCount == 1)

        let resultTask = Task { @MainActor in
            await service.requestOneShotFix(for: home, accuracy: .coarse)
        }
        await Task.yield()
        #expect(manager.requestLocationCount == 2)
        #expect(!manager.allowsBackgroundLocationUpdates)
        manager.deliverLocation()
        let result = await resultTask.value

        guard case .success(let coordinate) = result else {
            Issue.record("expected a successful fresh Home fix")
            return
        }
        #expect(coordinate.latitude == 33.3)
        #expect(manager.locationStartCount == 1, "a one-shot must not start continuous updates")
        #expect(manager.locationStopCount == 1)
    }

    @Test func retryReplacesPendingOneShotWithoutLeakingLateOwnership() async throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let manager = FakeLocationManager()
        let service = makeService(manager: manager, defaults: defaults)
        let recenter = LocationConsumerID("area-recenter")

        let first = Task { @MainActor in
            await service.requestOneShotFix(for: recenter, accuracy: .precise)
        }
        await Task.yield()
        let retry = Task { @MainActor in
            await service.requestOneShotFix(for: recenter, accuracy: .precise)
        }
        await Task.yield()

        #expect(await first.value == .unavailable)
        #expect(manager.requestLocationCount == 2)
        manager.deliverLocation(latitude: 33.4, longitude: -112.1)
        guard case .success(let coordinate) = await retry.value else {
            Issue.record("the retry must own the delivered fresh fix")
            return
        }
        #expect(coordinate.latitude == 33.4)
        #expect(!manager.allowsBackgroundLocationUpdates)
        #expect(manager.locationStartCount == 0)
    }

    @Test func deniedUnavailableAndFailedOneShotsReturnExplicitResults() async throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let manager = FakeLocationManager()
        manager.authorizationStatus = .denied
        let service = makeService(manager: manager, defaults: defaults)

        #expect(await service.requestOneShotFix(
            for: LocationConsumerID("denied"),
            accuracy: .coarse
        ) == .denied)
        #expect(manager.requestLocationCount == 0)

        manager.authorizationStatus = .authorizedWhenInUse
        manager.notifyAuthorizationChanged()
        await Task.yield()
        service.setApplicationActive(false)
        #expect(await service.requestOneShotFix(
            for: LocationConsumerID("background"),
            accuracy: .precise
        ) == .unavailable)

        service.setApplicationActive(true)
        let failedTask = Task { @MainActor in
            await service.requestOneShotFix(
                for: LocationConsumerID("failed"),
                accuracy: .precise
            )
        }
        await Task.yield()
        manager.failLocation()
        #expect(await failedTask.value == .unavailable)
    }
}
