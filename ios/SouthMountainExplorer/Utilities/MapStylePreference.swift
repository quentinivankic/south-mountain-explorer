import Foundation
import MapKit

/// User-facing map-style preference. Persisted via
/// `@AppStorage(StorageKeys.mapStyle)`. The raw string is what
/// lands in UserDefaults, so renaming a case is a migration
/// hazard.
enum MapStylePreference: String, CaseIterable, Identifiable {
    case standard
    case satellite
    case hybrid

    var id: String { rawValue }

    var label: String {
        switch self {
        case .standard:  return "Standard"
        case .satellite: return "Satellite"
        case .hybrid:    return "Hybrid"
        }
    }

    /// Build the `MKMapConfiguration` applied via
    /// `MKMapView.preferredConfiguration`. `.hybrid` is satellite imagery
    /// + road/place labels overlaid — closer to what users mean by
    /// "satellite" on Apple Maps.
    ///
    /// Do not go back to legacy `MKMapView.mapType`, and keep imagery on
    /// `.realistic` elevation: on the iOS 27.3 simulator both
    /// `mapType = .satellite` and `MKImageryMapConfiguration(.flat)` leave
    /// the Area map frozen (drag/pinch do nothing); `.realistic` pans.
    /// Hybrid gets `.realistic` too so both imagery styles take the same
    /// path. The Area map disables pitch, so it still renders top-down.
    /// Standard stays `.flat`. Points of interest stay excluded on the
    /// styles that support a filter, matching `pointOfInterestFilter =
    /// .excludingAll`.
    func makeMapConfiguration() -> MKMapConfiguration {
        switch self {
        case .standard:
            let config = MKStandardMapConfiguration(elevationStyle: .flat)
            config.pointOfInterestFilter = .excludingAll
            return config
        case .satellite:
            return MKImageryMapConfiguration(elevationStyle: .realistic)
        case .hybrid:
            let config = MKHybridMapConfiguration(elevationStyle: .realistic)
            config.pointOfInterestFilter = .excludingAll
            return config
        }
    }
}
