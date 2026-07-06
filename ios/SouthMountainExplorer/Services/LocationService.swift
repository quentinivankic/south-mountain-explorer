import CoreLocation
import Observation

@MainActor
@Observable
final class LocationService: NSObject {
    static let shared = LocationService()

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
    /// north if true north isn't available). Populated when
    /// `startHeadingUpdates()` is active. Used by the map's
    /// follow-with-heading camera mode so the user's facing direction
    /// stays "up" on screen.
    private(set) var liveHeading: CLLocationDirection? = nil
    /// Timestamp of the most recent GPS fix received THIS session (from
    /// the live delegate — not the last-known location restored from
    /// UserDefaults). Lets callers tell a genuinely fresh fix from the
    /// stale restored one. `nil` until the first live fix arrives.
    private(set) var lastFixDate: Date? = nil

    private let manager = CLLocationManager()
    private var liveWatching = false

    private override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        // Throttle heading updates to 2° changes. Default
        // kCLHeadingFilterNone fires on every micro-movement; 5° was
        // an initial smoothing attempt but felt steppy on slow
        // rotations. 2° keeps update frequency reasonable while
        // letting the camera animation interpolate the gaps cleanly.
        manager.headingFilter = 2
        authorizationStatus = manager.authorizationStatus

        // Restore last known location from UserDefaults
        let ud = UserDefaults.standard
        let lat = ud.double(forKey: StorageKeys.userLocationLat)
        let lon = ud.double(forKey: StorageKeys.userLocationLon)
        if lat != 0 || lon != 0 {
            userLocation = CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }
    }

    func requestPermission() {
        // When-In-Use only. Background hike recording works under this
        // authorization because it happens during an explicit,
        // user-started session with `UIBackgroundModes: [location]` +
        // `allowsBackgroundLocationUpdates` (see startBackgroundTracking).
        // The app has no geofencing or significant-location-change
        // relaunch, so "Always" would add nothing but a scarier prompt
        // and an App Review scrutiny vector — deliberately not requested.
        manager.requestWhenInUseAuthorization()
    }

    func startLiveTracking() {
        guard !liveWatching else { return }
        liveWatching = true
        manager.startUpdatingLocation()
    }

    /// Force one fresh fix now, independent of whether continuous
    /// tracking is running. Used by the walk screen so it centers on
    /// where you ARE, not the last-known location restored from
    /// UserDefaults (which, after moving far between opens, points at
    /// where you last used the app). `requestLocation` delivers a
    /// single fix through the same delegate, updating `lastFixDate`.
    func requestFreshFix() {
        guard isAuthorized else { return }
        manager.requestLocation()
    }

    func stopLiveTracking() {
        guard liveWatching else { return }
        liveWatching = false
        manager.stopUpdatingLocation()
    }

    // Background location for recording
    func startBackgroundTracking() {
        manager.allowsBackgroundLocationUpdates = true
        manager.pausesLocationUpdatesAutomatically = false
        manager.activityType = .fitness
        manager.startUpdatingLocation()
    }

    func stopBackgroundTracking() {
        manager.allowsBackgroundLocationUpdates = false
        manager.stopUpdatingLocation()
    }

    func startHeadingUpdates() {
        guard CLLocationManager.headingAvailable() else { return }
        manager.startUpdatingHeading()
    }

    func stopHeadingUpdates() {
        manager.stopUpdatingHeading()
        liveHeading = nil
    }

    var isAuthorized: Bool {
        authorizationStatus == .authorizedWhenInUse || authorizationStatus == .authorizedAlways
    }
}

extension LocationService: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        let coord = loc.coordinate
        // Negative `verticalAccuracy` is CoreLocation's sentinel for
        // "we don't have a valid altitude" — typically early in a
        // session before the GPS gets a vertical lock. Skip those.
        let altitude: Double? = loc.verticalAccuracy >= 0 ? loc.altitude : nil
        let fixDate = loc.timestamp
        Task { @MainActor in
            self.liveLocation = coord
            self.liveAltitude = altitude
            self.userLocation = coord
            self.lastFixDate = fixDate
            UserDefaults.standard.set(coord.latitude, forKey: StorageKeys.userLocationLat)
            UserDefaults.standard.set(coord.longitude, forKey: StorageKeys.userLocationLon)
        }
    }

    /// `requestLocation()` requires a failure handler or it logs a
    /// warning; a fix failure here is non-fatal (the walk screen falls
    /// back to the last-known location), so just absorb it.
    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) { }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            self.authorizationStatus = status
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        // Prefer true heading; fall back to magnetic if true isn't
        // available yet (calibration). Either is fine for "make the
        // user's facing direction up on screen" — small magnetic vs
        // true offset isn't perceptible at city/trail zoom.
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
