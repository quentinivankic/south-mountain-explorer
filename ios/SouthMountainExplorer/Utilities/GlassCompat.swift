import SwiftUI

/// iOS 26 Liquid Glass effect with an iOS 18 Material fallback.
///
/// The `.glassEffect(...)` modifier and the `Glass` value type are
/// iOS 26+ only. To keep this app installable on iOS 18 (the
/// minimum we ship) without losing the layered visual depth on
/// iOS 26, every call site routes through one of the helpers
/// below. The `#available` guard wraps the iOS 26-only types so
/// they never trip iOS 18 compilation; the fallback uses
/// `.regularMaterial` which gives the same "frosted layer above the
/// map" reading without true refraction.
extension View {

    /// Plain glass surface for cards, banners, and pill buttons.
    /// On iOS 26 renders real Liquid Glass; on iOS 18 falls back to
    /// `.regularMaterial`.
    @ViewBuilder
    func compatibleGlass<S: Shape>(in shape: S) -> some View {
        if #available(iOS 26.0, *) {
            self.glassEffect(in: shape)
        } else {
            self.background(.regularMaterial, in: shape)
        }
    }

    /// Interactive variant — used on the AreaView "Record this trail"
    /// pill where the glass picks up touch feedback. iOS 18 fallback
    /// drops the interactive shimmer (no equivalent on Material) and
    /// just renders the frosted surface.
    @ViewBuilder
    func compatibleGlassInteractive<S: Shape>(in shape: S) -> some View {
        if #available(iOS 26.0, *) {
            self.glassEffect(.regular.interactive(), in: shape)
        } else {
            self.background(.regularMaterial, in: shape)
        }
    }

    /// Tinted-interactive variant used by HomeView's length-filter
    /// pills. The selected pill picks up the accent color; the
    /// unselected pills stay neutral. On iOS 18 the selected state
    /// renders as a solid accent fill (no Glass tint exists), and
    /// the unselected state uses `.regularMaterial`.
    @ViewBuilder
    func compatibleGlassTinted<S: Shape>(isSelected: Bool, in shape: S) -> some View {
        if #available(iOS 26.0, *) {
            self.glassEffect(
                isSelected ? .regular.tint(.accentColor).interactive() : .regular.interactive(),
                in: shape
            )
        } else if isSelected {
            self.background(Color.accentColor, in: shape)
        } else {
            self.background(.regularMaterial, in: shape)
        }
    }
}
