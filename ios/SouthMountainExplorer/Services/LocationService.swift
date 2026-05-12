import CoreLocation
import Observation

@MainActor
@Observable
final class LocationService: NSObject {
    static let shared = LocationService()

    private(set) var authorizationStatus: CLAuthorizationStatus = .notDetermined
    private(set) var userLocation: CLLocationCoordinate2D? = nil
    private(set) var liveLocation: CLLocationCoordinate2D? = nil

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
}
