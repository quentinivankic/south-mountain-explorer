import UIKit

/// Size of the app's window, not the physical screen.
///
/// `UIScreen.main.bounds` is the whole display and never changes when the
/// window does. On iPhone Duo (and any resizable window) the window can be
/// narrower or shorter than the screen, and the system shifts content away
/// from its right-edge rail, so screen-based math lands in the wrong place.
/// Falls back to the screen when no window scene exists yet (early launch,
/// unit tests).
@MainActor
enum WindowMetrics {
    static var size: CGSize {
        let window = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first(where: { $0.activationState == .foregroundActive })?
            .keyWindow
            ?? UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first?.keyWindow
        return window?.bounds.size ?? UIScreen.main.bounds.size
    }
}
