import MapKit
import ObjectiveC
import OSLog
import UIKit

/// DIAGNOSTICS ONLY (chat/map-touch-diag-ios273). Never merge into a fix.
/// Enabled with the launch argument `-TrekdexTouchDiag 1`. Logs window,
/// touch, gesture recognizer and MKMapView state with the `[TDIAG]`
/// prefix. Never logs coordinates, locations, touch points or regions.
/// `-TrekdexDiagSkipPostHog 1` skips PostHog setup for an A/B check.
@MainActor
enum TouchDiag {
    static let enabled = UserDefaults.standard.bool(forKey: "TrekdexTouchDiag")
    static let skipPostHog = UserDefaults.standard.bool(forKey: "TrekdexDiagSkipPostHog")
    private static let logger = Logger(subsystem: "com.southmountainexplorer.app", category: "TDIAG")
    private static var installed = false
    private static var movedCount = 0

    static func log(_ s: String) {
        logger.notice("[TDIAG] \(s, privacy: .public)")
        print("[TDIAG] \(s)")
    }

    static func installIfEnabled() {
        guard enabled, !installed else { return }
        installed = true
        log("enabled skipPostHog=\(skipPostHog) os=\(UIDevice.current.systemVersion)")
        if let orig = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.sendEvent(_:))),
           let repl = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.tdiag_sendEvent(_:))) {
            method_exchangeImplementations(orig, repl)
            log("UIWindow.sendEvent hook installed")
        }
        NotificationCenter.default.addObserver(forName: UIWindow.didBecomeKeyNotification,
                                               object: nil, queue: .main) { note in
            let name = (note.object as AnyObject?).map { String(describing: type(of: $0)) } ?? "nil"
            MainActor.assumeIsolated {
                log("didBecomeKey window=\(name)")
                dumpWindows(reason: "didBecomeKey")
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            dumpWindows(reason: "launch+3s")
            dumpMaps(reason: "launch+3s")
        }
    }

    static func mapCreated(_ map: MKMapView) {
        guard enabled else { return }
        log("MKMapView created")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            dumpWindows(reason: "map-appeared")
            dumpMaps(reason: "map-appeared")
        }
    }

    // MARK: Dumps

    static func allWindows() -> [UIWindow] {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
    }

    static func dumpWindows(reason: String) {
        guard enabled else { return }
        log("windows reason=\(reason)")
        for scene in UIApplication.shared.connectedScenes {
            log(" scene \(type(of: scene)) state=\(scene.activationState.rawValue)")
        }
        for (i, w) in allWindows().enumerated() {
            let gr = (w.gestureRecognizers ?? []).map { describe($0) }
            let inter = w.interactions.map { String(describing: type(of: $0)) }
            log(" window[\(i)] \(type(of: w)) level=\(w.windowLevel.rawValue) hidden=\(w.isHidden) uie=\(w.isUserInteractionEnabled) frame=\(w.frame) key=\(w.isKeyWindow) alpha=\(w.alpha) root=\(w.rootViewController.map { String(describing: type(of: $0)) } ?? "nil")")
            log("  window[\(i)] interactions=\(inter) recognizers=\(gr)")
        }
        let appIMP = class_getMethodImplementation(UIApplication.self, #selector(UIApplication.sendEvent(_:)))
        let phIMP = class_getMethodImplementation(UIApplication.self, NSSelectorFromString("sendEventOverride:"))
        log(" UIApplication.sendEvent imp=\(String(describing: appIMP)) posthogOverrideSlot=\(String(describing: phIMP)) appClass=\(type(of: UIApplication.shared))")
    }

    static func dumpMaps(reason: String) {
        guard enabled else { return }
        var maps: [MKMapView] = []
        for w in allWindows() { collectMaps(in: w, into: &maps) }
        log("maps reason=\(reason) count=\(maps.count)")
        for (i, m) in maps.enumerated() { dumpMap(m, index: i) }
    }

    static func dumpMap(_ m: MKMapView, index: Int) {
        log(" map[\(index)] size=\(m.bounds.size) scroll=\(m.isScrollEnabled) zoom=\(m.isZoomEnabled) rotate=\(m.isRotateEnabled) pitch=\(m.isPitchEnabled) uie=\(m.isUserInteractionEnabled) hidden=\(m.isHidden) alpha=\(m.alpha) window=\(m.window.map { String(describing: type(of: $0)) } ?? "nil")")
        var chain: [String] = []
        var v = m.superview
        while let s = v {
            chain.append("\(type(of: s))(uie=\(s.isUserInteractionEnabled),hidden=\(s.isHidden),alpha=\(s.alpha))")
            v = s.superview
        }
        log("  map[\(index)] ancestors=\(chain.joined(separator: " < "))")
        log("  map[\(index)] interactions=\(m.interactions.map { String(describing: type(of: $0)) })")
        logRecognizers(in: m, depth: 0, label: "map[\(index)]")
    }

    private static func logRecognizers(in view: UIView, depth: Int, label: String) {
        let grs = view.gestureRecognizers ?? []
        if !grs.isEmpty {
            log("  \(label) d\(depth) \(type(of: view)) recognizers=\(grs.map { describe($0) })")
        }
        guard depth < 3 else { return }
        for sub in view.subviews where !(sub is MKAnnotationView) {
            logRecognizers(in: sub, depth: depth + 1, label: label)
        }
    }

    private static func collectMaps(in view: UIView, into maps: inout [MKMapView]) {
        if let m = view as? MKMapView { maps.append(m); return }
        for sub in view.subviews { collectMaps(in: sub, into: &maps) }
    }

    static func describe(_ g: UIGestureRecognizer) -> String {
        let del = g.delegate.map { String(describing: type(of: $0)) } ?? "nil"
        let view = g.view.map { String(describing: type(of: $0)) } ?? "nil"
        return "\(type(of: g))(state=\(stateName(g.state)),enabled=\(g.isEnabled),view=\(view),delegate=\(del),cancels=\(g.cancelsTouchesInView),delaysBegan=\(g.delaysTouchesBegan))"
    }

    static func stateName(_ s: UIGestureRecognizer.State) -> String {
        switch s {
        case .possible: return "possible"
        case .began: return "began"
        case .changed: return "changed"
        case .ended: return "ended"
        case .cancelled: return "cancelled"
        case .failed: return "failed"
        @unknown default: return "raw\(s.rawValue)"
        }
    }

    static func phaseName(_ p: UITouch.Phase) -> String {
        switch p {
        case .began: return "began"
        case .moved: return "moved"
        case .stationary: return "stationary"
        case .ended: return "ended"
        case .cancelled: return "cancelled"
        default: return "raw\(p.rawValue)"
        }
    }

    // MARK: Touches

    static func logEvent(_ event: UIEvent, window: UIWindow) {
        guard event.type == .touches, let touches = event.allTouches, !touches.isEmpty else { return }
        let isMovedOnly = touches.allSatisfy { $0.phase == .moved || $0.phase == .stationary }
        if isMovedOnly {
            movedCount += 1
            guard movedCount % 8 == 1 else { return }
        }
        log("event window=\(type(of: window)) touches=\(touches.count)")
        for (i, t) in touches.enumerated() {
            let delivered = t.view.map { String(describing: type(of: $0)) } ?? "nil"
            log(" touch[\(i)] phase=\(phaseName(t.phase)) taps=\(t.tapCount) type=\(t.type.rawValue) touchWindow=\(t.window.map { String(describing: type(of: $0)) } ?? "nil") deliveredView=\(delivered)")
            if t.phase == .began {
                let hit = window.hitTest(t.location(in: window), with: event)
                log("  touch[\(i)] hitChain=\(chain(from: hit))")
                if let m = firstMap(above: hit) { dumpMap(m, index: -1) }
            }
            let grs = (t.gestureRecognizers ?? []).map { describe($0) }
            log("  touch[\(i)] recognizers(\(grs.count))=\(grs)")
        }
    }

    private static func chain(from view: UIView?) -> String {
        guard let view else { return "nil" }
        var items: [String] = []
        var v: UIView? = view
        while let s = v {
            items.append("\(type(of: s))(uie=\(s.isUserInteractionEnabled),alpha=\(s.alpha),hidden=\(s.isHidden))")
            v = s.superview
        }
        return items.reversed().joined(separator: " > ")
    }

    private static func firstMap(above view: UIView?) -> MKMapView? {
        var v = view
        while let s = v {
            if let m = s as? MKMapView { return m }
            v = s.superview
        }
        return nil
    }
}

extension UIWindow {
    /// Swapped with `sendEvent(_:)` by `TouchDiag`; calling this name runs the original.
    @objc func tdiag_sendEvent(_ event: UIEvent) {
        tdiag_sendEvent(event)
        TouchDiag.logEvent(event, window: self)
    }
}
