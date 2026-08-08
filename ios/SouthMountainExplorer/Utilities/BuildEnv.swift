import Foundation

/// Runtime build-channel detection.
///
/// Exists so developer-only affordances (currently the diagnostics auto-sync,
/// see `DebugDiagSync`) can be active in TestFlight while staying inert in an
/// App Store production install — WITHOUT using `#if DEBUG`, which is compiled
/// out of the Release archive that TestFlight actually ships (`ios-testflight.yml`
/// archives `-configuration Release`). A `#if DEBUG` feature therefore simply
/// does not exist in a TestFlight build; this gate is the fix.
enum BuildEnv {
    /// True for TestFlight and local/dev builds; false for an App Store
    /// production install.
    ///
    /// A production App Store receipt is named `receipt`; TestFlight (and Xcode
    /// dev installs) carry a `sandboxReceipt`. DEBUG short-circuits to true so
    /// the simulator/dev experience matches TestFlight. Since an App Store
    /// submission promotes the same Release binary TestFlight ran, this runtime
    /// check — not a compile flag — is what keeps the feature out of production.
    static var isTestFlight: Bool {
        #if DEBUG
        return true
        #else
        guard let url = Bundle.main.appStoreReceiptURL else { return false }
        return url.lastPathComponent == "sandboxReceipt"
        #endif
    }
}
