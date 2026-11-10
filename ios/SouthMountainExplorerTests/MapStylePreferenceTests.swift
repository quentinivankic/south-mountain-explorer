import Testing
import MapKit
@testable import SouthMountainExplorer

/// `MapStylePreference` → `MKMapConfiguration`. The Area map must use
/// the configuration API with realistic elevation for imagery: legacy
/// `mapType = .satellite` and flat imagery freeze panning on iOS 27.3.
@MainActor
struct MapStylePreferenceTests {

    @Test func standardIsFlatStandardWithoutPOIs() throws {
        let config = try #require(
            MapStylePreference.standard.makeMapConfiguration() as? MKStandardMapConfiguration
        )
        #expect(config.elevationStyle == .flat)
        #expect(config.pointOfInterestFilter == .excludingAll)
    }

    @Test func satelliteIsRealisticImagery() throws {
        let config = try #require(
            MapStylePreference.satellite.makeMapConfiguration() as? MKImageryMapConfiguration
        )
        #expect(config.elevationStyle == .realistic)
    }

    @Test func hybridIsRealisticHybridWithoutPOIs() throws {
        let config = try #require(
            MapStylePreference.hybrid.makeMapConfiguration() as? MKHybridMapConfiguration
        )
        #expect(config.elevationStyle == .realistic)
        #expect(config.pointOfInterestFilter == .excludingAll)
    }
}
