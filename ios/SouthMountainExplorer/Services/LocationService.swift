import CoreLocation
import Observation

@MainActor
@Observable
final class LocationService: NSObject {
    static let shared = LocationService()

    private(set) var authorizationStatus: CLAuthorizationStatus = .notDetermined
    private(set) var userLocation: CLLocationCoordinate2D? = nil
    private(set) var liveLocation: CLLocationCoordinate2D? = nil
    /// Compass heading in degrees clockwise from true north (or magnetic
    /// north if true north isn't available). Populated when
    /// `startHeadingUpdates()` is active. Used by the map's
    /// follow-with-heading camera mode so the user's facing direction
    /// stays "up" on screen.
    private(set) var liveHeading: CLLocationDirection? = nil

    private let manager = CLLocationManager()
    private var liveWatching = false

    private override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
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
        manager.requestWhenInUseAuthorization()
    }

    func requestAlwaysPermission() {
        manager.requestAlwaysAuthorization()
    }

    func startLiveTracking() {
        guard !liveWatching else { return }
        liveWatching = true
        manager.startUpdatingLocation()
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

    var hasAlwaysAuthorization: Bool {
        authorizationStatus == .authorizedAlways
    }
}

extension LocationService: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        let coord = loc.coordinate
        Task { @MainActor in
            self.liveLocation = coord
            self.userLocation = coord
            UserDefaults.standard.set(coord.latitude, forKey: StorageKeys.userLocationLat)
            UserDefaults.standard.set(coord.longitude, forKey: StorageKeys.userLocationLon)
        }
    }

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
