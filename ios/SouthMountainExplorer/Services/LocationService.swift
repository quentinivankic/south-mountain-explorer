import CoreLocation
import Foundation
import Observation
import UIKit

/// UUID-backed ownership for one visible foreground-location consumer.
/// Value semantics make repeated acquisition by the same view idempotent,
/// while distinct visible views remain independently reference-counted.
struct ForegroundLocationDemandToken: Hashable, Sendable {
    let id: UUID

    init(id: UUID = UUID()) {
        self.id = id
    }
}

/// UUID-backed ownership for one visible compass consumer.
struct HeadingDemandToken: Hashable, Sendable {
    let id: UUID

    init(id: UUID = UUID()) {
        self.id = id
    }
}

/// Narrow Core Location driver seam. `LocationService` owns policy and is the
/// only place that starts/stops or changes background configuration; this seam
/// keeps those decisions observable in focused tests without booting hardware.
@MainActor
protocol LocationManagerDriving: AnyObject {
    var delegate: (any CLLocationManagerDelegate)? { get set }
    var authorizationStatus: CLAuthorizationStatus { get }
    var desiredAccuracy: CLLocationAccuracy { get set }
    var headingFilter: CLLocationDegrees { get set }
    var allowsBackgroundLocationUpdates: Bool { get set }
    var pausesLocationUpdatesAutomatically: Bool { get set }
    var activityType: CLActivityType { get set }
    var headingAvailable: Bool { get }

    func requestWhenInUseAuthorization()
    func requestLocation()
    func startUpdatingLocation()
    func stopUpdatingLocation()
    func startUpdatingHeading()
    func stopUpdatingHeading()
}

@MainActor
private final class SystemLocationManagerDriver: LocationManagerDriving {
    private let manager = CLLocationManager()

    var delegate: (any CLLocationManagerDelegate)? {
        get { manager.delegate }
        set { manager.delegate = newValue }
    }
    var authorizationStatus: CLAuthorizationStatus { manager.authorizationStatus }
    var desiredAccuracy: CLLocationAccuracy {
        get { manager.desiredAccuracy }
        set { manager.desiredAccuracy = newValue }
    }
    var headingFilter: CLLocationDegrees {
        get { manager.headingFilter }
        set { manager.headingFilter = newValue }
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
    var headingAvailable: Bool { CLLocationManager.headingAvailable() }

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
        manager: SystemLocationManagerDriver(),
        userDefaults: .standard
    )

    private(set) var authorizationStatus: CLAuthorizationStatus = .notDetermined
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
    /// visible heading owner has demand in an active scene.
    private(set) var liveHeading: CLLocationDirection? = nil
    /// Timestamp of the most recent GPS fix received THIS session (from
    /// the live delegate — not the last-known location restored from
    /// UserDefaults). Lets callers tell a genuinely fresh fix from the
    /// stale restored one. `nil` until the first live fix arrives.
    private(set) var lastFixDate: Date? = nil
    /// Scene activity gates foreground GPS and compass demand. Recording
    /// demand deliberately ignores this gate so locking/backgrounding the app
    /// cannot interrupt an explicitly started hike.
    private(set) var isApplicationActive = false

    private enum ContinuousLocationMode: Equatable {
        case off
        case foreground
        case recording
    }

    private let manager: any LocationManagerDriving
    private let userDefaults: UserDefaults
    private var foregroundLocationDemands: Set<ForegroundLocationDemandToken> = []
    private var headingDemands: Set<HeadingDemandToken> = []
    private var recordingDemand = false
    private var appliedLocationMode: ContinuousLocationMode? = nil
    private var headingUpdatesRunning = false
    /// One-shot requests are reconciled with continuous demand because Core
    /// Location forbids requestLocation() while continuous updates are active.
    private var freshFixRequested = false
    private var freshFixInFlight = false

    /// Injectable initializer for lifecycle tests. Production uses `shared`.
    init(manager: any LocationManagerDriving, userDefaults: UserDefaults) {
        self.manager = manager
        self.userDefaults = userDefaults
        super.init()

        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        // 2° keeps compass movement smooth without firing on every tiny sensor
        // fluctuation. This is static sensor tuning, not lifecycle policy.
        manager.headingFilter = 2
        authorizationStatus = manager.authorizationStatus

        let lat = userDefaults.double(forKey: StorageKeys.userLocationLat)
        let lon = userDefaults.double(forKey: StorageKeys.userLocationLon)
        if lat != 0 || lon != 0 {
            userLocation = CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }

        // Establish known foreground-safe configuration without issuing an
        // unnecessary physical stop on a newly-created manager.
        reconcileDemand()
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
        // When-In-Use is sufficient for an explicit recording session with
        // the location background mode and allowsBackgroundLocationUpdates.
        manager.requestWhenInUseAuthorization()
    }

    func setApplicationActive(_ active: Bool) {
        guard isApplicationActive != active else { return }
        isApplicationActive = active
        reconcileDemand()
    }

    func acquireForegroundLocationDemand(_ token: ForegroundLocationDemandToken) {
        guard foregroundLocationDemands.insert(token).inserted else { return }
        reconcileDemand()
    }

    func releaseForegroundLocationDemand(_ token: ForegroundLocationDemandToken) {
        guard foregroundLocationDemands.remove(token) != nil else { return }
        reconcileDemand()
    }

    func acquireHeadingDemand(_ token: HeadingDemandToken) {
        guard headingDemands.insert(token).inserted else { return }
        reconcileDemand()
    }

    func releaseHeadingDemand(_ token: HeadingDemandToken) {
        guard headingDemands.remove(token) != nil else { return }
        reconcileDemand()
    }

    /// Ask for one fresh fix without changing continuous ownership. Repeated
    /// requests coalesce; an already-running foreground/recording stream is
    /// itself the fresh-fix source, because Core Location rejects concurrent
    /// requestLocation() and startUpdatingLocation() use.
    func requestFreshFix() {
        guard isAuthorized, isApplicationActive else { return }
        freshFixRequested = true
        reconcileDemand()
    }

    /// Complete either the one-shot success or failure edge. Continuous
    /// callbacks also pass through here harmlessly when no one-shot is active.
    func freshFixDidComplete() {
        freshFixRequested = false
        freshFixInFlight = false
    }

    // RecordingService remains the single logical recording owner, so its
    // existing API maps to one idempotent Boolean rather than a token set.
    func startBackgroundTracking() {
        guard !recordingDemand else { return }
        recordingDemand = true
        reconcileDemand()
    }

    func stopBackgroundTracking() {
        guard recordingDemand else { return }
        recordingDemand = false
        reconcileDemand()
    }

    /// Shared by the concrete delegate and focused tests so pending demand is
    /// reconciled immediately after grant, denial, or a later Settings change.
    func authorizationDidChange(to status: CLAuthorizationStatus) {
        authorizationStatus = status
        reconcileDemand()
    }

    /// Ignore a heading delivered after its final owner released demand.
    func receiveHeading(_ heading: CLLocationDirection) {
        guard headingUpdatesRunning else { return }
        liveHeading = heading
    }

    /// The sole owner of physical continuous-location/heading transitions and
    /// background configuration. Logical demand is retained while unauthorized
    /// or scene-inactive so a later grant/foreground transition can resume it.
    private func reconcileDemand() {
        let desiredLocationMode: ContinuousLocationMode
        if isAuthorized, recordingDemand {
            desiredLocationMode = .recording
        } else if isAuthorized, isApplicationActive, !foregroundLocationDemands.isEmpty {
            desiredLocationMode = .foreground
        } else {
            desiredLocationMode = .off
        }

        // A one-shot is a physical location operation too. Cancel it before a
        // continuous owner starts, or when scene/authorization no longer permits
        // foreground work. Home will request again on the next active edge.
        if desiredLocationMode != .off || !isAuthorized || !isApplicationActive {
            freshFixRequested = false
            if freshFixInFlight {
                manager.stopUpdatingLocation()
                freshFixInFlight = false
            }
        }

        if appliedLocationMode != desiredLocationMode {
            let previousMode = appliedLocationMode ?? .off

            // Reset every recording-only setting whenever recording is not the
            // effective owner, including a direct recording -> foreground edge.
            switch desiredLocationMode {
            case .recording:
                manager.allowsBackgroundLocationUpdates = true
                manager.pausesLocationUpdatesAutomatically = false
                manager.activityType = .fitness
            case .foreground, .off:
                manager.allowsBackgroundLocationUpdates = false
                manager.pausesLocationUpdatesAutomatically = true
                manager.activityType = .other
            }

            if previousMode == .off, desiredLocationMode != .off {
                manager.startUpdatingLocation()
            } else if previousMode != .off, desiredLocationMode == .off {
                manager.stopUpdatingLocation()
            }
            // Foreground <-> recording changes configuration in place. Stopping
            // and restarting here would create the exact cross-owner gap this
            // reconciler exists to prevent.
            appliedLocationMode = desiredLocationMode
        }

        if desiredLocationMode == .off,
           isAuthorized,
           isApplicationActive,
           freshFixRequested {
            freshFixRequested = false
            if !freshFixInFlight {
                manager.requestLocation()
                freshFixInFlight = true
            }
        }

        let shouldRunHeading = isAuthorized
            && isApplicationActive
            && !headingDemands.isEmpty
            && manager.headingAvailable
        if shouldRunHeading != headingUpdatesRunning {
            if shouldRunHeading {
                manager.startUpdatingHeading()
            } else {
                manager.stopUpdatingHeading()
            }
            headingUpdatesRunning = shouldRunHeading
        }
        if !shouldRunHeading {
            liveHeading = nil
        }
    }
}

extension LocationService: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        let coord = loc.coordinate
        // Negative verticalAccuracy is Core Location's sentinel for no valid
        // altitude (typically early in a session before vertical lock).
        let altitude: Double? = loc.verticalAccuracy >= 0 ? loc.altitude : nil
        let fixDate = loc.timestamp
        Task { @MainActor in
            self.freshFixDidComplete()
            self.liveLocation = coord
            self.liveAltitude = altitude
            self.userLocation = coord
            self.lastFixDate = fixDate
            self.userDefaults.set(coord.latitude, forKey: StorageKeys.userLocationLat)
            self.userDefaults.set(coord.longitude, forKey: StorageKeys.userLocationLon)
        }
    }

    /// `requestLocation()` requires a failure handler; failure is non-fatal and
    /// callers fall back to the last-known location.
    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in
            self.freshFixDidComplete()
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            self.authorizationDidChange(to: status)
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        // Prefer true heading; fall back to magnetic while calibration settles.
        let heading: CLLocationDirection = newHeading.trueHeading >= 0
            ? newHeading.trueHeading
            : newHeading.magneticHeading
        Task { @MainActor in
            self.receiveHeading(heading)
        }
    }
}

// CLLocationCoordinate2D doesn't conform to Equatable out of the box,
// which trips up `.onChange(of: optionalCoord)` in SwiftUI.
extension CLLocationCoordinate2D: @retroactive Equatable {
    public static func == (lhs: CLLocationCoordinate2D, rhs: CLLocationCoordinate2D) -> Bool {
        lhs.latitude == rhs.latitude && lhs.longitude == rhs.longitude
    }
}
