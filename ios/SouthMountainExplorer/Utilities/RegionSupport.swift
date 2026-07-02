import Foundation

/// Which regions TrekDex has trail coverage for, and where the current
/// device sits relative to that.
///
/// Uses `Locale.current.region` (the user's Region setting) — no
/// location permission, and it reflects the user's home market, which
/// is what the waitlist cares about: a US user travelling abroad still
/// reads as `US` and shouldn't be prompted to join a waitlist.
enum RegionSupport {
    /// ISO 3166-1 alpha-2 codes TrekDex covers today.
    static let supportedRegionCodes: Set<String> = ["US", "CA"]

    /// The device's current region code (e.g. "US", "FR"), or "" if the
    /// system can't resolve one.
    static var currentRegionCode: String {
        Locale.current.region?.identifier ?? ""
    }

    /// True when the device's region is covered.
    static var isSupported: Bool {
        isSupported(regionCode: currentRegionCode)
    }

    /// Testable core — kept pure so tests don't depend on the host's
    /// Region setting.
    static func isSupported(regionCode: String) -> Bool {
        supportedRegionCodes.contains(regionCode)
    }

    /// Localized country name for the current region ("France"), falling
    /// back to the raw code, then a generic phrase.
    static var currentCountryName: String {
        let code = currentRegionCode
        guard !code.isEmpty else { return "your region" }
        return Locale.current.localizedString(forRegionCode: code) ?? code
    }
}
