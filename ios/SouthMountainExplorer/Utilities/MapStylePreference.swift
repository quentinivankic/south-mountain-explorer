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

    /// Map to the `MKMapType` MapKit expects on `MKMapView.mapType`.
    /// `.hybrid` is satellite imagery + road/place labels overlaid —
    /// closer to what users mean by "satellite" on Apple Maps.
    var mkMapType: MKMapType {
        switch self {
        case .standard:  return .standard
        case .satellite: return .satellite
        case .hybrid:    return .hybrid
        }
    }
}
