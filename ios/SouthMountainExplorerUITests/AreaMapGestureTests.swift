import XCTest
import UIKit

/// Regression gate for the Area map ignoring finger pan on iOS 27.3: opens
/// South Mountain, proves a one-finger drag on the visible map moves it
/// (idle noise near zero first), then proves selecting a trail from the list
/// still re-frames the map. Measures the fraction of changed pixels in a
/// 240x160 pt box at a point above the sheet / beside the side panel — NOT
/// the map element's center, which the fit-stop sheet covers.
final class AreaMapGestureTests: XCTestCase {
    private let areaRowId = "area-progress-south-mountain-park-and-preserve-az"

    @MainActor
    func testDragMovesAreaMapAndTrailSelectionStillFrames() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed"]
        // Optional map style (TEST_RUNNER_MAPSTYLE=satellite|hybrid|standard),
        // injected via the UserDefaults argument domain so it survives the seed wipe.
        if let style = ProcessInfo.processInfo.environment["MAPSTYLE"], !style.isEmpty {
            app.launchArguments += ["-summit:map-style", style]
            print("MAPGESTURE style=\(style)")
        }
        app.launch()
        // Let the launch burst settle before the first accessibility query.
        sleep(20)
        let stats = app.buttons["Stats"].firstMatch
        XCTAssertTrue(stats.waitForExistence(timeout: 30), "Stats tab missing")
        for _ in 1...4 {
            stats.tap()
            sleep(4)
            if stats.isSelected { break }
        }
        let row = app.descendants(matching: .any)[areaRowId].firstMatch
        var swipes = 0
        while !row.exists && swipes < 12 {
            app.swipeUp()
            swipes += 1
            sleep(1)
        }
        XCTAssertTrue(row.waitForExistence(timeout: 10), "Area Progress row missing")
        tap(row)
        XCTAssertTrue(app.buttons["area-recenter-button"].firstMatch.waitForExistence(timeout: 60),
                      "Area never opened")
        // Trail geometry and the opening framing settle after the push.
        sleep(12)

        let map = app.maps.firstMatch
        XCTAssertTrue(map.exists, "Area map missing")
        let probe = probePoint(app: app, map: map)

        let s0 = shot("map-0-open")
        sleep(4)
        let s1 = shot("map-1-idle")
        let idle = diff(s0, s1, around: probe)
        print("MAPGESTURE idleDiff=\(idle) probe=\(probe) mapFrame=\(map.frame)")
        XCTAssertLessThan(idle, 0.05, "Map is not idle before the drag")

        let start = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: probe.x, dy: probe.y))
        start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: -120, dy: 90)))
        sleep(4)
        let s2 = shot("map-2-after-drag")
        let drag = diff(s1, s2, around: probe)
        print("MAPGESTURE dragDiff=\(drag)")
        XCTAssertGreaterThan(drag, 0.3, "One-finger drag did not move the Area map")

        // Programmatic camera move: selecting a trail from the list.
        let select = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Select Trail,"))
            .allElementsBoundByIndex
            .first { $0.exists && $0.isHittable }
        guard let select else {
            XCTFail("No hittable Select Trail button")
            return
        }
        tap(select)
        sleep(5)
        let s3 = shot("map-3-after-select")
        let selected = diff(s2, s3, around: probe)
        print("MAPGESTURE selectDiff=\(selected)")
        XCTAssertGreaterThan(selected, 0.1, "Selecting a trail did not re-frame the map")
    }

    /// A point well inside the visible map, clear of the sheet / side panel.
    private func probePoint(app: XCUIApplication, map: XCUIElement) -> CGPoint {
        let win = app.windows.firstMatch.frame
        let f = map.frame.intersection(win)
        return CGPoint(x: f.midX, y: f.minY + min(f.height, win.height) * 0.30)
    }

    private func tap(_ element: XCUIElement) {
        if element.isHittable {
            element.tap()
        } else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
    }

    private func shot(_ name: String) -> UIImage {
        let s = XCUIScreen.main.screenshot()
        let a = XCTAttachment(screenshot: s)
        a.name = name
        a.lifetime = .keepAlways
        add(a)
        return s.image
    }

    /// Fraction of pixels (in a 240x160 pt box around `p`) whose RGB differ by > 24.
    private func diff(_ a: UIImage, _ b: UIImage, around p: CGPoint) -> Double {
        guard let ca = a.cgImage, let cb = b.cgImage else { return -1 }
        let scale = CGFloat(ca.width) / a.size.width
        let box = CGRect(x: (p.x - 120) * scale, y: (p.y - 80) * scale,
                         width: 240 * scale, height: 160 * scale)
            .intersection(CGRect(x: 0, y: 0, width: ca.width, height: ca.height)).integral
        guard let pa = rgba(ca, box), let pb = rgba(cb, box), pa.count == pb.count, !pa.isEmpty
        else { return -1 }
        var changed = 0
        var i = 0
        while i < pa.count {
            let d = abs(Int(pa[i]) - Int(pb[i])) + abs(Int(pa[i + 1]) - Int(pb[i + 1]))
                + abs(Int(pa[i + 2]) - Int(pb[i + 2]))
            if d > 24 { changed += 1 }
            i += 4
        }
        return Double(changed) / Double(pa.count / 4)
    }

    private func rgba(_ img: CGImage, _ r: CGRect) -> [UInt8]? {
        guard let crop = img.cropping(to: r) else { return nil }
        let w = crop.width, h = crop.height
        var buf = [UInt8](repeating: 0, count: w * h * 4)
        guard let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8,
                                  bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return nil }
        ctx.draw(crop, in: CGRect(x: 0, y: 0, width: w, height: h))
        return buf
    }
}
