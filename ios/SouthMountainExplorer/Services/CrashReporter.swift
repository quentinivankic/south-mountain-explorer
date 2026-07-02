import Foundation
import MetricKit

/// Subscribes to MetricKit diagnostic payloads and forwards crash / hang
/// COUNTS (never the payloads themselves) to analytics. This complements
/// PostHog, whose crash story is weak: MetricKit is Apple-first-party,
/// on-device, and PII-free.
///
/// Not live crash reporting — MetricKit aggregates diagnostics and
/// delivers them on a later launch (typically once per day), so this is
/// a "field crashes are happening" signal, not a real-time symbolicated
/// stack trace. Good enough to notice a regression via a spike in
/// `crash_detected` / `hang_detected`.
///
/// Plain (non-actor) NSObject: `MXMetricManagerSubscriber` callbacks are
/// delivered on a background queue, so the subscriber must be
/// nonisolated. We hop to the main actor only to hand the count to
/// `AnalyticsService`.
final class CrashReporter: NSObject, MXMetricManagerSubscriber {
    static let shared = CrashReporter()

    /// Register with MetricKit. Call once at launch.
    func start() {
        MXMetricManager.shared.add(self)
    }

    // Required by the protocol. Routine performance metrics aren't
    // forwarded — only the crash/hang diagnostics below.
    func didReceive(_ payloads: [MXMetricPayload]) {}

    func didReceive(_ payloads: [MXDiagnosticPayload]) {
        let crashes = payloads.reduce(0) { $0 + ($1.crashDiagnostics?.count ?? 0) }
        let hangs = payloads.reduce(0) { $0 + ($1.hangDiagnostics?.count ?? 0) }
        guard crashes > 0 || hangs > 0 else { return }
        // Counts are value types captured by the closure — safe to send
        // across the actor hop.
        Task { @MainActor in
            if crashes > 0 {
                AnalyticsService.shared.capture(.crashDetected(count: crashes))
            }
            if hangs > 0 {
                AnalyticsService.shared.capture(.hangDetected(count: hangs))
            }
        }
    }
}
