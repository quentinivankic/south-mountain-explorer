import CoreLocation
import Observation
import UIKit

struct LocationConsumerID: Hashable, Sendable {
    private let rawValue: String

    init(_ rawValue: String = UUID().uuidString) {
        self.rawValue = rawValue
    }
}

enum LocationAccuracyDemand: Equatable, Sendable {
    case coarse
    case precise
}

enum LocationFixResult: Equatable {
    case success(CLLocationCoordinate2D)
    case denied
    case unavailable
}

@MainActor
protocol LocationManagerControlling: AnyObject {
    var authorizationStatus: CLAuthorizationStatus { get }
    var desiredAccuracy: CLLocationAccuracy { get set }
    var allowsBackgroundLocationUpdates: Bool { get set }
    var pausesLocationUpdatesAutomatically: Bool { get set }
    var activityType: CLActivityType { get set }
    var headingFilter: CLLocationDegrees { get set }
    var headingAvailable: Bool { get }

    func setDelegate(_ delegate: (any CLLocationManagerDelegate)?)
    func requestWhenInUseAuthorization()
    func requestLocation()
    func startUpdatingLocation()
    func stopUpdatingLocation()
    func startUpdatingHeading()
    func stopUpdatingHeading()
}

@MainActor
private final class CoreLocationManagerAdapter: LocationManagerControlling {
    private let manager = CLLocationManager()

    var authorizationStatus: CLAuthorizationStatus { manager.authorizationStatus }
    var desiredAccuracy: CLLocationAccuracy {
        get { manager.desiredAccuracy }
        set { manager.desiredAccuracy = newValue }
    }
    var allowsBackgroundLocationUpdates: Bool {
        get { manager.allowsBackgroundLocationUpdates }
        set { manager.allowsBackgroundLocationUpdates = newValue }
    }
    var pausesLocationUpdatesAutomatically: Bool {
        get { manager.pausesLocationUpdatesAutomatically }
        set { manager.pausesLocationUpdatesAutomatically = newValue }
    }
    var activityType: CLActivityType {
        get { manager.activityType }
        set { manager.activityType = newValue }
    }
    var headingFilter: CLLocationDegrees {
        get { manager.headingFilter }
        set { manager.headingFilter = newValue }
    }
    var headingAvailable: Bool { CLLocationManager.headingAvailable() }

    func setDelegate(_ delegate: (any CLLocationManagerDelegate)?) {
        manager.delegate = delegate
    }

    func requestWhenInUseAuthorization() { manager.requestWhenInUseAuthorization() }
    func requestLocation() { manager.requestLocation() }
    func startUpdatingLocation() { manager.startUpdatingLocation() }
    func stopUpdatingLocation() { manager.stopUpdatingLocation() }
    func startUpdatingHeading() { manager.startUpdatingHeading() }
    func stopUpdatingHeading() { manager.stopUpdatingHeading() }
}

@MainActor
@Observable
final class LocationService: NSObject, RecordingLocationControlling {
    static let shared = LocationService(
        manager: CoreLocationManagerAdapter(),
        userDefaults: .standard
    )

    private(set) var authorizationStatus: CLAuthorizationStatus
    private(set) var userLocation: CLLocationCoordinate2D? = nil
    private(set) var liveLocation: CLLocationCoordinate2D? = nil
    /// Altitude in meters at the latest GPS fix, when the fix had a
    /// non-negative `verticalAccuracy`. `nil` when the device isn't
    /// confident enough in altitude (cold start, indoor, dense canopy).
    /// Sampled alongside `liveLocation` by `RecordingService.appendPoint`
    /// so each saved GPS point can carry elevation.
    private(set) var liveAltitude: Double? = nil
    /// Compass heading in degrees clockwise from true north (or magnetic
    /// north if true north isn't available). Populated while at least one
    /// visible map owns heading demand.
    private(set) var liveHeading: CLLocationDirection? = nil
    /// Timestamp of the most recent GPS fix received THIS session (from
    /// the live delegate — not the last-known location restored from
    /// UserDefaults). Lets callers tell a genuinely fresh fix from the
    /// stale restored one. `nil` until the first live fix arrives.
    private(set) var lastFixDate: Date? = nil

    private struct PendingFix {
        let accuracy: LocationAccuracyDemand
        let requestedAt: Date
        let continuation: CheckedContinuation<LocationFixResult, Never>
    }

    private let manager: any LocationManagerControlling
    private let userDefaults: UserDefaults
    private var foregroundDemands: [LocationConsumerID: LocationAccuracyDemand] = [:]
    private var headingConsumers: Set<LocationConsumerID> = []
    private var pendingFixes: [LocationConsumerID: PendingFix] = [:]
    private var pendingFixTimeouts: [LocationConsumerID: Task<Void, Never>] = [:]
    private var recordingOwnsLocation = false
    private var applicationIsActive = true
    private var locationUpdatesRunning = false
    private var headingUpdatesRunning = false

    init(manager: any LocationManagerControlling, userDefaults: UserDefaults) {
        self.manager = manager
        self.userDefaults = userDefaults
        self.authorizationStatus = manager.authorizationStatus
        super.init()

        manager.setDelegate(self)
        manager.headingFilter = 2

        let lat = userDefaults.double(forKey: StorageKeys.userLocationLat)
        let lon = userDefaults.double(forKey: StorageKeys.userLocationLon)
        if lat != 0 || lon != 0 {
            userLocation = CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }
    }

    /// True once the user has actively refused (or is restricted by policy).
    /// `requestWhenInUseAuthorization()` is a no-op in this state, so callers
    /// must send the user to Settings instead of silently doing nothing.
    var isDenied: Bool {
        authorizationStatus == .denied || authorizationStatus == .restricted
    }

    var isAuthorized: Bool {
        authorizationStatus == .authorizedWhenInUse || authorizationStatus == .authorizedAlways
    }

    func requestPermission() {
        if isDenied {
            if let url = URL(string: UIApplication.openSettingsURLString) {
                UIApplication.shared.open(url)
            }
            return
        }
        manager.requestWhenInUseAuthorization()
    }

    func setApplicationActive(_ active: Bool) {
        guard applicationIsActive != active else { return }
        applicationIsActive = active
        if !active {
            finishPendingFixes(with: .unavailable)
        }
        reconcileManagerState()
    }

    func acquireLocation(
        for consumer: LocationConsumerID,
        accuracy: LocationAccuracyDemand
    ) {
        foregroundDemands[consumer] = accuracy
        reconcileManagerState()
    }

    func releaseLocation(for consumer: LocationConsumerID) {
        foregroundDemands.removeValue(forKey: consumer)
        finishPendingFix(for: consumer, with: .unavailable)
        reconcileManagerState()
    }

    func acquireHeading(for consumer: LocationConsumerID) {
        headingConsumers.insert(consumer)
        reconcileManagerState()
    }

    func releaseHeading(for consumer: LocationConsumerID) {
        headingConsumers.remove(consumer)
        reconcileManagerState()
    }

    func requestOneShotFix(
        for consumer: LocationConsumerID,
        accuracy: LocationAccuracyDemand
    ) async -> LocationFixResult {
        guard applicationIsActive else { return .unavailable }
        guard isAuthorized else { return isDenied ? .denied : .unavailable }

        finishPendingFix(for: consumer, with: .unavailable)

        return await withCheckedContinuation { continuation in
            pendingFixes[consumer] = PendingFix(
                accuracy: accuracy,
                requestedAt: Date(),
                continuation: continuation
            )
            pendingFixTimeouts[consumer] = Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(7))
                guard !Task.isCancelled else { return }
                self?.finishPendingFix(for: consumer, with: .unavailable)
                self?.reconcileManagerState()
            }
            reconcileManagerState()
            manager.requestLocation()
        }
    }

    func acquireRecordingLocation() {
        guard !recordingOwnsLocation else { return }
        recordingOwnsLocation = true
        reconcileManagerState()
    }

    func releaseRecordingLocation() {
        guard recordingOwnsLocation else { return }
        recordingOwnsLocation = false
        reconcileManagerState()
    }

    private func reconcileManagerState() {
        let foregroundLocationActive = applicationIsActive && !foregroundDemands.isEmpty
        let shouldRunLocation = recordingOwnsLocation || foregroundLocationActive
        let pendingAccuracy = pendingFixes.values.map(\.accuracy)
        let needsPrecise = recordingOwnsLocation
            || foregroundDemands.values.contains(.precise)
            || pendingAccuracy.contains(.precise)

        manager.desiredAccuracy = needsPrecise
            ? kCLLocationAccuracyBest
            : kCLLocationAccuracyHundredMeters
        manager.allowsBackgroundLocationUpdates = recordingOwnsLocation
        manager.pausesLocationUpdatesAutomatically = !recordingOwnsLocation
        manager.activityType = recordingOwnsLocation ? .fitness : .other

        if shouldRunLocation, !locationUpdatesRunning {
            locationUpdatesRunning = true
            manager.startUpdatingLocation()
        } else if !shouldRunLocation, locationUpdatesRunning {
            locationUpdatesRunning = false
            manager.stopUpdatingLocation()
        }

        let shouldRunHeading = applicationIsActive
            && isAuthorized
            && !headingConsumers.isEmpty
            && manager.headingAvailable
        if shouldRunHeading, !headingUpdatesRunning {
            headingUpdatesRunning = true
            manager.startUpdatingHeading()
        } else if !shouldRunHeading, headingUpdatesRunning {
            headingUpdatesRunning = false
            manager.stopUpdatingHeading()
            liveHeading = nil
        }
    }

    private func finishPendingFix(
        for consumer: LocationConsumerID,
        with result: LocationFixResult
    ) {
        pendingFixTimeouts.removeValue(forKey: consumer)?.cancel()
        pendingFixes.removeValue(forKey: consumer)?.continuation.resume(returning: result)
    }

    private func finishPendingFixes(
        with result: LocationFixResult,
        receivedAt: Date? = nil
    ) {
        let consumers = pendingFixes.compactMap { consumer, pending in
            guard let receivedAt else { return consumer }
            return receivedAt >= pending.requestedAt.addingTimeInterval(-2)
                ? consumer
                : nil
        }
        for consumer in consumers {
            finishPendingFix(for: consumer, with: result)
        }
    }
}

extension LocationService: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        let coord = loc.coordinate
        let altitude: Double? = loc.verticalAccuracy >= 0 ? loc.altitude : nil
        let fixDate = loc.timestamp
        Task { @MainActor in
            self.liveLocation = coord
            self.liveAltitude = altitude
            self.userLocation = coord
            self.lastFixDate = fixDate
            self.userDefaults.set(coord.latitude, forKey: StorageKeys.userLocationLat)
            self.userDefaults.set(coord.longitude, forKey: StorageKeys.userLocationLon)
            self.finishPendingFixes(with: .success(coord), receivedAt: fixDate)
            self.reconcileManagerState()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in
            self.finishPendingFixes(with: .unavailable)
            self.reconcileManagerState()
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            self.authorizationStatus = self.manager.authorizationStatus
            if self.isDenied {
                self.finishPendingFixes(with: .denied)
            }
            self.reconcileManagerState()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        let heading: CLLocationDirection = newHeading.trueHeading >= 0
            ? newHeading.trueHeading
            : newHeading.magneticHeading
        Task { @MainActor in
            self.liveHeading = heading
        }
    }
}

// CLLocationCoordinate2D doesn't conform to Equatable out of the box,
// which trips up `.onChange(of: optionalCoord)` in SwiftUI. Add a
// component-wise comparison so TrailMapView can observe liveLocation
// updates. `@retroactive` acknowledges that Apple may add their own
// conformance later; we'd see a clear conflict error here and drop
// this extension.
extension CLLocationCoordinate2D: @retroactive Equatable {
    public static func == (lhs: CLLocationCoordinate2D, rhs: CLLocationCoordinate2D) -> Bool {
        lhs.latitude == rhs.latitude && lhs.longitude == rhs.longitude
    }
}
