import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `RegionSupport.isSupported(regionCode:)` — which regions get
/// the app vs the out-of-region waitlist. Pure code-based check so it
/// doesn't depend on the host machine's Region setting.
struct RegionSupportTests {

    @Test func coveredRegionsAreSupported() {
        #expect(RegionSupport.isSupported(regionCode: "US"))
        #expect(RegionSupport.isSupported(regionCode: "CA"))
    }

    @Test func otherRegionsAreNotSupported() {
        #expect(!RegionSupport.isSupported(regionCode: "FR"))
        #expect(!RegionSupport.isSupported(regionCode: "GB"))
        #expect(!RegionSupport.isSupported(regionCode: "MX"))
        #expect(!RegionSupport.isSupported(regionCode: "AU"))
    }

    @Test func emptyOrUnknownRegionIsNotSupported() {
        // A device the system can't resolve a region for falls to "" —
        // treat as out-of-region (show the waitlist) rather than
        // silently assuming coverage.
        #expect(!RegionSupport.isSupported(regionCode: ""))
        #expect(!RegionSupport.isSupported(regionCode: "ZZ"))
    }

    @Test func codesAreCaseSensitiveISOUppercase() {
        // Region identifiers from Locale are uppercase ISO codes; a
        // lowercase value should not match (guards against a future
        // change feeding un-normalized input).
        #expect(!RegionSupport.isSupported(regionCode: "us"))
    }
}
