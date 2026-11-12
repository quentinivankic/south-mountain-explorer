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
        openSouthMountain(app)

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

    /// Satellite (realistic elevation) map: a two-finger pinch zooms the Area
    /// map, and trail lines keep their on-screen width across the zoom (the
    /// `LineDisplayZoomScale` compensation). Width = median local thickness of
    /// the cyan completed-trail strokes (3 pt) in the map band above the sheet.
    @MainActor
    func testSatellitePinchZoomsAndKeepsTrailLineWidth() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-seed", "-summit:map-style", "satellite"]
        app.launch()
        openSouthMountain(app)

        let map = app.maps.firstMatch
        XCTAssertTrue(map.exists, "Area map missing")
        let probe = probePoint(app: app, map: map)
        let band = lineBand(app: app, map: map)

        let s0 = shot("sat-0-open")
        let w0 = lineWidth(s0, in: band)
        print("MAPGESTURE sat before width=\(w0.median) px=\(w0.pixels) scale=\(w0.scale) band=\(band) mapFrame=\(map.frame)")

        // XCUIElement.pinch always lands on the element's center. On CI phones
        // that is open map; on a short window (folded Duo) the fit-stop sheet
        // covers it, so local runs zoom with a double tap at the probe instead.
        // CI (TREKDEX_CI=1) must pinch.
        // `map.isHittable` reports true even when the sheet covers the center,
        // so compare against the sheet header's top edge instead.
        let onCI = ProcessInfo.processInfo.environment["TREKDEX_CI"] == "1"
        let centerHittable = map.frame.midY < sheetTop(app) - 24
        print("MAPGESTURE sat mapCenterOpen=\(centerHittable) sheetTop=\(sheetTop(app)) ci=\(onCI)")
        if centerHittable {
            map.pinch(withScale: 2.0, velocity: 1.0)
        } else {
            XCTAssertFalse(onCI, "Map center is covered, so the pinch cannot reach the map on CI")
            app.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: probe.x, dy: probe.y)).doubleTap()
        }
        sleep(6)
        let s1 = shot("sat-1-after-zoom")
        let zoom = diff(s0, s1, around: probe)
        let w1 = lineWidth(s1, in: band)
        print("MAPGESTURE sat zoomDiff=\(zoom) after width=\(w1.median) px=\(w1.pixels)")
        XCTAssertGreaterThan(zoom, 0.3,
                             centerHittable ? "Pinch did not zoom the satellite Area map"
                                            : "Double tap did not zoom the satellite Area map")

        // 3 pt strokes measure 2.67-3.0 pt here (8-9 px at 3x); with the
        // compensation turned off they measured 5.0-5.33 pt (iOS 27.3 Duo).
        for (label, w) in [("before", w0), ("after", w1)] {
            XCTAssertGreaterThanOrEqual(w.pixels, 2000, "Too few trail-line pixels to measure (\(label) zoom)")
            XCTAssertGreaterThan(w.median, 1.5, "Satellite trail lines too thin \(label) zoom: \(w.median) pt")
            XCTAssertLessThan(w.median, 4.0, "Satellite trail lines too thick \(label) zoom: \(w.median) pt")
        }
        let ratio = w1.median / w0.median
        XCTAssertTrue((0.7...1.43).contains(ratio),
                      "Satellite trail line width changed with zoom: \(w0.median) to \(w1.median) pt")
    }

    /// Stats tab, then the South Mountain Area Progress row, then wait for the
    /// Area map to open and settle.
    @MainActor
    private func openSouthMountain(_ app: XCUIApplication) {
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
    }

    /// A point well inside the visible map, clear of the sheet / side panel.
    private func probePoint(app: XCUIApplication, map: XCUIElement) -> CGPoint {
        let win = app.windows.firstMatch.frame
        let f = map.frame.intersection(win)
        return CGPoint(x: f.midX, y: f.minY + min(f.height, win.height) * 0.30)
    }

    /// Top of the trail-list sheet: its header's top edge less the grabber
    /// and padding above it. The window bottom when the header is not found.
    @MainActor
    private func sheetTop(_ app: XCUIApplication) -> CGFloat {
        let header = app.descendants(matching: .any)["area-header"].firstMatch
        guard header.exists else { return app.windows.firstMatch.frame.maxY }
        return header.frame.minY - 40
    }

    /// The visible map band used for line-width sampling: inset from the
    /// edges, from 15% of the window height down to the sheet top (at most
    /// 50% of the window).
    @MainActor
    private func lineBand(app: XCUIApplication, map: XCUIElement) -> CGRect {
        let win = app.windows.firstMatch.frame
        let f = map.frame.intersection(win)
        let h = min(f.height, win.height)
        let top = f.minY + h * 0.15
        let bottom = min(f.minY + h * 0.50, sheetTop(app))
        return CGRect(x: f.minX + 16, y: top, width: f.width - 32, height: max(0, bottom - top))
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

    /// Median local stroke thickness, in screen points, of cyan pixels inside
    /// `band` (points). Local thickness of a pixel = the shortest of its
    /// horizontal, vertical and two diagonal runs of cyan (diagonals times
    /// sqrt 2), which is within 8% of the stroke width at any line angle.
    private func lineWidth(_ image: UIImage, in band: CGRect)
        -> (median: Double, pixels: Int, scale: Double) {
        guard let cg = image.cgImage else { return (-1, 0, 0) }
        let scale = CGFloat(cg.width) / image.size.width
        let box = CGRect(x: band.minX * scale, y: band.minY * scale,
                         width: band.width * scale, height: band.height * scale)
            .intersection(CGRect(x: 0, y: 0, width: cg.width, height: cg.height)).integral
        guard let px = rgba(cg, box) else { return (-1, 0, Double(scale)) }
        let w = Int(box.width), h = Int(box.height)
        var mask = [Bool](repeating: false, count: w * h)
        for i in 0..<(w * h) {
            let r = Int(px[i * 4]), g = Int(px[i * 4 + 1]), b = Int(px[i * 4 + 2])
            mask[i] = b > 150 && g > 120 && r < 140 && b - r > 80 && g - r > 50
        }
        var best = [Double](repeating: .infinity, count: w * h)
        // Directions: (dx, dy, step length in pixels).
        let dirs: [(Int, Int, Double)] = [(1, 0, 1), (0, 1, 1), (1, 1, 2.0.squareRoot()), (1, -1, 2.0.squareRoot())]
        for (dx, dy, step) in dirs {
            var seen = [Bool](repeating: false, count: w * h)
            for start in 0..<(w * h) where mask[start] && !seen[start] {
                // Walk back to the run's first pixel, then forward over the run.
                var x = start % w, y = start / w
                while x - dx >= 0, x - dx < w, y - dy >= 0, y - dy < h, mask[(y - dy) * w + (x - dx)] {
                    x -= dx; y -= dy
                }
                var run: [Int] = []
                while x >= 0, x < w, y >= 0, y < h, mask[y * w + x] {
                    run.append(y * w + x)
                    x += dx; y += dy
                }
                let len = Double(run.count) * step
                for i in run {
                    seen[i] = true
                    best[i] = min(best[i], len)
                }
            }
        }
        let values = best.filter { $0.isFinite }.sorted()
        guard !values.isEmpty else { return (-1, 0, Double(scale)) }
        return (values[values.count / 2] / Double(scale), values.count, Double(scale))
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
