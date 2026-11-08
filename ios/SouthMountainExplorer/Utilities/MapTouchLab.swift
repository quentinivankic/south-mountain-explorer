import MapKit
import SwiftUI
import UIKit

/// DIAGNOSTICS ONLY (chat/map-touch-diag-ios273). Never merge into a fix.
/// On-device bisect for the iOS 27.3 map drag bug. Each switch turns off
/// one part of the area map wrapper, and the lab screens show a bare map
/// with and without a sheet. Switches are stored in UserDefaults, so a
/// launch argument such as `-TrekdexDiagNoCameraWrites 1` also sets one.
/// Changes apply the next time a map is created (reopen the area).
enum MapTouchLab {
    static let noCameraWritesKey = "TrekdexDiagNoCameraWrites"
    static let noOverlaysKey = "TrekdexDiagNoOverlays"
    static let noDelegateKey = "TrekdexDiagNoDelegate"
    static let noSelectionTapKey = "TrekdexDiagNoSelectionTap"
    static let noSheetPanKey = "TrekdexDiagNoSheetPan"
    static let bareMapLaunchKey = "TrekdexBareMap"
    static let showPOIKey = "TrekdexDiagShowPOI"
    static let noUserDotKey = "TrekdexDiagNoUserDot"
    static let noPinsKey = "TrekdexDiagNoPins"
    static let standardTypeKey = "TrekdexDiagStandardType"
    static var showPOI: Bool { UserDefaults.standard.bool(forKey: showPOIKey) }
    static var noUserDot: Bool { UserDefaults.standard.bool(forKey: noUserDotKey) }
    static var noPins: Bool { UserDefaults.standard.bool(forKey: noPinsKey) }
    static var standardType: Bool { UserDefaults.standard.bool(forKey: standardTypeKey) }

    static var noCameraWrites: Bool { UserDefaults.standard.bool(forKey: noCameraWritesKey) }
    static var noOverlays: Bool { UserDefaults.standard.bool(forKey: noOverlaysKey) }
    static var noDelegate: Bool { UserDefaults.standard.bool(forKey: noDelegateKey) }
    static var noSelectionTap: Bool { UserDefaults.standard.bool(forKey: noSelectionTapKey) }
    static var noSheetPan: Bool { UserDefaults.standard.bool(forKey: noSheetPanKey) }
    static var openOnLaunch: Bool { UserDefaults.standard.bool(forKey: bareMapLaunchKey) }

    /// Turns off the sheet presentation pan recognizers in `view`'s window:
    /// the window-level pan whose delegate is the form sheet presentation
    /// controller, and any pan whose delegate is the sheet interaction.
    /// The touch log showed these reaching `changed` during map drags.
    @MainActor
    static func disableSheetPans(near view: UIView) {
        guard noSheetPan, let window = view.window else { return }
        var views: [UIView] = [window]
        var stack: [UIView] = [window]
        while let v = stack.popLast() {
            for s in v.subviews {
                if String(describing: type(of: s)).contains("DropShadowView") { views.append(s) }
                stack.append(s)
            }
        }
        var count = 0
        for v in views {
            for gr in v.gestureRecognizers ?? [] where gr is UIPanGestureRecognizer {
                let d = gr.delegate.map { String(describing: type(of: $0)) } ?? ""
                if d.contains("FormSheetPresentationController") || d.contains("SheetInteraction") {
                    if gr.isEnabled { gr.isEnabled = false; count += 1 }
                }
            }
        }
        TouchDiag.log("lab disabled sheet pans count=\(count)")
    }
}
