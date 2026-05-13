import Foundation
import Observation

/// Runtime diagnostics for the area map. Populated by
/// `MapKitMapView.Coordinator` (overlay count, `updateUIView`
/// duration) and `FPSCounter` (frames-per-second sampling).
/// `DebugHUDView` observes this when the Developer-mode HUD is on.
///
/// Single shared instance because (a) there's only ever one
/// TrailMapView visible at a time, and (b) the HUD lives in a
/// different subtree from where the writes originate — passing it
/// down the view tree would mean piping it through several
/// unrelated parents. The singleton lets both ends hold a reference
/// without coupling.
@MainActor
@Observable
final class MapDiagnostics {
    static let shared = MapDiagnostics()
    private init() {}

    /// Most recent UI-thread frame rate (samples per second).
    /// Updated ~twice per second by `FPSCounter` while the HUD is
    /// active. 0 when the FPS counter is stopped.
    var fps: Double = 0

    /// Number of overlays currently attached to the MKMapView.
    /// Includes trail multi-polylines, halo polylines, and the
    /// recording polyline (when present). Snapshotted at the end
    /// of every `updateUIView` so the HUD reflects the live state.
    var overlayCount: Int = 0

    /// Duration of the most recent `MapKitMapView.updateUIView`
    /// call in milliseconds. Useful for spotting regressions where
    /// an unrelated state change starts triggering expensive
    /// reconciliation work mid-pan.
    var lastUpdateDurationMs: Double = 0
}
