import SwiftUI

/// Translucent box rendered top-right over `TrailMapView` when the
/// Settings → Developer → Show Debug HUD toggle is on. Surfaces
/// the four numbers most useful for diagnosing field-perf reports:
///
/// - **FPS** — frame rate of the UI thread; drops here under pan
///   are the canonical "the map is lagging" signal.
/// - **Overlays** — count of overlays attached to the MKMapView,
///   to spot regressions where halo/trails inflate beyond what the
///   area's data should produce.
/// - **Update ms** — duration of the most recent
///   `MapKitMapView.updateUIView`. Spikes here mean a state change
///   is triggering expensive overlay reconciliation.
/// - **Mem MB** — resident memory footprint, sampled once per
///   second. Helps catch leaks during long hikes.
///
/// All four pull from `MapDiagnostics.shared`. The HUD itself adds
/// no measurement overhead beyond its own re-render — which it
/// throttles to 1 Hz for memory and rides on `@Observable` change
/// notifications for the rest.
struct DebugHUDView: View {
    /// `@Bindable` because `MapDiagnostics` is `@Observable` — this
    /// is how SwiftUI subscribes to property-level change
    /// notifications for `@Observable` types in iOS 17+.
    @Bindable var diagnostics: MapDiagnostics
    @State private var memoryMB: Double = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("FPS:      \(Int(diagnostics.fps.rounded()))")
            Text("Overlays: \(diagnostics.overlayCount)")
            Text("Update:   \(String(format: "%.1f", diagnostics.lastUpdateDurationMs)) ms")
            Text("Mem:      \(String(format: "%.0f", memoryMB)) MB")
        }
        .font(.caption.monospacedDigit())
        .foregroundStyle(.white)
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(.black.opacity(0.65))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .allowsHitTesting(false)
        .onAppear {
            memoryMB = MemoryProbe.footprintMB()
        }
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in
            memoryMB = MemoryProbe.footprintMB()
        }
    }
}
