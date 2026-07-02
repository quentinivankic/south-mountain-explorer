import Foundation

/// A single analytics event: a stable snake_case name plus string-only
/// properties. Mirrors `ActivityLogService`'s privacy discipline — an
/// event must NEVER carry a GPS coordinate or free-form PII. Continuous
/// values (distance, duration) are bucketed at the factory so analytics
/// stays coarse and non-identifying.
struct AnalyticsEvent: Equatable, Sendable {
    let name: String
    let properties: [String: String]

    init(_ name: String, _ properties: [String: String] = [:]) {
        self.name = name
        self.properties = properties
    }
}

/// Where analytics events go. The rest of the app talks ONLY to
/// `AnalyticsService`; this protocol is the single seam a concrete
/// vendor (PostHog) plugs into, so call sites never import an SDK and
/// swapping vendors touches one file.
@MainActor
protocol AnalyticsBackend: AnyObject {
    func capture(_ event: AnalyticsEvent)
    func flush()
}

/// Default backend: drops everything. Ships until a real backend + key
/// are configured, so the app collects nothing by default and event
/// call sites can be wired ahead of the vendor going live.
@MainActor
final class NoopAnalyticsBackend: AnalyticsBackend {
    func capture(_ event: AnalyticsEvent) {}
    func flush() {}
}

/// App-wide analytics facade. Used as `AnalyticsService.shared.capture(...)`
/// at meaningful call sites (same granularity as ActivityLogService —
/// meaningful actions, not every tap). Routes to whatever
/// `AnalyticsBackend` is configured; the no-op backend until PostHog is
/// wired in Phase 3 (see docs/backend-pipelines.md).
@MainActor
@Observable
final class AnalyticsService {
    static let shared = AnalyticsService()

    private var backend: AnalyticsBackend = NoopAnalyticsBackend()

    private init() {}

    /// Install a real backend (e.g. PostHog) at launch once its key is
    /// available. Until called, events go to the no-op backend.
    func configure(backend: AnalyticsBackend) {
        self.backend = backend
    }

    func capture(_ event: AnalyticsEvent) {
        backend.capture(event)
    }

    /// Force-send buffered events — call on app background so a later
    /// kill doesn't lose them.
    func flush() {
        backend.flush()
    }
}

// MARK: - Event taxonomy

/// Typed factories for every event the app emits. Names are the stable
/// wire contract (PostHog groups on them) — the tests pin them, so
/// renaming one is a deliberate, reviewed change.
extension AnalyticsEvent {
    static func appLaunched(build: String) -> AnalyticsEvent {
        AnalyticsEvent("app_launched", ["build": build])
    }

    static func areaOpened(areaId: String) -> AnalyticsEvent {
        AnalyticsEvent("area_opened", ["area_id": areaId])
    }

    static func hikeStarted(areaId: String, mode: String) -> AnalyticsEvent {
        AnalyticsEvent("hike_started", ["area_id": areaId, "mode": mode])
    }

    static func hikeSaved(areaId: String, distanceMi: Double,
                          durationSeconds: Int, mode: String) -> AnalyticsEvent {
        AnalyticsEvent("hike_saved", [
            "area_id": areaId,
            "distance_bucket": distanceBucket(miles: distanceMi),
            "duration_bucket": durationBucket(seconds: durationSeconds),
            "mode": mode,
        ])
    }

    static func hikeDiscarded(areaId: String) -> AnalyticsEvent {
        AnalyticsEvent("hike_discarded", ["area_id": areaId])
    }

    static func trailCompleted(areaId: String) -> AnalyticsEvent {
        AnalyticsEvent("trail_completed", ["area_id": areaId])
    }

    static func areaCompleted(areaId: String) -> AnalyticsEvent {
        AnalyticsEvent("area_completed", ["area_id": areaId])
    }

    static func dexOpened(areaId: String) -> AnalyticsEvent {
        AnalyticsEvent("dex_opened", ["area_id": areaId])
    }

    static func unitsChanged(value: String) -> AnalyticsEvent {
        AnalyticsEvent("units_changed", ["value": value])
    }

    static func themeChanged(value: String) -> AnalyticsEvent {
        AnalyticsEvent("theme_changed", ["value": value])
    }

    static func dataExported() -> AnalyticsEvent {
        AnalyticsEvent("data_exported")
    }

    static func dataImported() -> AnalyticsEvent {
        AnalyticsEvent("data_imported")
    }

    static func feedbackSubmitted(category: String, hasEmail: Bool) -> AnalyticsEvent {
        AnalyticsEvent("feedback_submitted", [
            "category": category,
            "has_email": hasEmail ? "true" : "false",
        ])
    }

    // MARK: Bucketing (keeps continuous values coarse / non-identifying)

    /// Coarse distance bucket. Negative/zero fold into the lowest band.
    static func distanceBucket(miles: Double) -> String {
        switch miles {
        case ..<1: return "0-1mi"
        case ..<3: return "1-3mi"
        case ..<6: return "3-6mi"
        case ..<10: return "6-10mi"
        default: return "10mi+"
        }
    }

    /// Coarse duration bucket.
    static func durationBucket(seconds: Int) -> String {
        switch seconds {
        case ..<1800: return "0-30min"
        case ..<3600: return "30-60min"
        case ..<10800: return "1-3h"
        default: return "3h+"
        }
    }
}
