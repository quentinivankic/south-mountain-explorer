import CoreGraphics
import MapKit
import UIKit

/// Where the walking-direction chevrons on a planned route go ("Plan a Hike"
/// round 4). Pure math in any flat 2D space (the renderer uses MapKit map
/// points), so it is unit tested without a map.
///
/// - Chevrons sit every `spacing` along the whole route, in walking order,
///   starting half a spacing in. The caller passes the spacing in the same
///   units as the points (screen points divided by the zoom scale), so they
///   stay the same distance apart on screen at every zoom and never crowd.
/// - Each one points along the line at that spot, so a loop reads round in
///   its walking direction and a one-way plan reads from start to end.
/// - Where the route walks the same line twice in opposite directions (an
///   out-and-back), two chevrons can land on top of each other. A chevron
///   with an opposite-facing one closer than `laneGap` moves `laneOffset` to
///   its own right, so the two passes split onto either side of the line and
///   both show.
enum PlannedRouteArrows {
    struct Chevron: Equatable {
        var point: CGPoint
        /// Direction of travel, radians (atan2 of dy, dx).
        var angle: CGFloat
    }

    static func place(
        along points: [CGPoint], spacing: CGFloat, laneGap: CGFloat, laneOffset: CGFloat
    ) -> [Chevron] {
        guard points.count >= 2, spacing > 0, spacing.isFinite else { return [] }
        var out: [Chevron] = []
        var next = spacing / 2
        var walked: CGFloat = 0
        for i in 0..<(points.count - 1) {
            let a = points[i], b = points[i + 1]
            let dx = b.x - a.x, dy = b.y - a.y
            let len = (dx * dx + dy * dy).squareRoot()
            guard len > 0 else { continue }
            let angle = atan2(dy, dx)
            while next <= walked + len {
                let t = (next - walked) / len
                out.append(Chevron(point: CGPoint(x: a.x + dx * t, y: a.y + dy * t), angle: angle))
                next += spacing
            }
            walked += len
        }
        return separatingOppositePasses(out, laneGap: laneGap, laneOffset: laneOffset)
    }

    /// Moves each chevron that has an opposite-facing chevron within `laneGap`
    /// `laneOffset` to its right (y down is right of travel in screen space).
    static func separatingOppositePasses(_ chevrons: [Chevron], laneGap: CGFloat, laneOffset: CGFloat) -> [Chevron] {
        guard laneGap > 0, chevrons.count > 1 else { return chevrons }
        struct Cell: Hashable { let x: Int; let y: Int }
        func cell(_ p: CGPoint) -> Cell { Cell(x: Int((p.x / laneGap).rounded(.down)), y: Int((p.y / laneGap).rounded(.down))) }
        var grid: [Cell: [Int]] = [:]
        for (i, c) in chevrons.enumerated() { grid[cell(c.point), default: []].append(i) }
        var out = chevrons
        for (i, c) in chevrons.enumerated() {
            let home = cell(c.point)
            var opposed = false
            search: for gx in (home.x - 1)...(home.x + 1) {
                for gy in (home.y - 1)...(home.y + 1) {
                    for j in grid[Cell(x: gx, y: gy)] ?? [] where j != i {
                        let o = chevrons[j]
                        let d = hypot(o.point.x - c.point.x, o.point.y - c.point.y)
                        if d < laneGap, cos(o.angle - c.angle) < 0 { opposed = true; break search }
                    }
                }
            }
            if opposed {
                // Right of travel: the direction rotated a quarter turn clockwise on screen.
                out[i].point.x += -sin(c.angle) * laneOffset
                out[i].point.y += cos(c.angle) * laneOffset
            }
        }
        return out
    }
}

/// The overlay carrying the whole planned route in walking order, drawn as
/// chevrons only (the route line itself is drawn by the other route overlays).
final class PlannedRouteArrowsPolyline: MKPolyline {
    /// Walk through (round 7): arrows of the legs other than the current one.
    var isFaded = false
}

/// Draws `PlannedRouteArrows` chevrons for a `PlannedRouteArrowsPolyline`.
/// A polyline renderer subclass so MapKit sizes its bounds like the route
/// line; it strokes no line of its own.
final class PlannedRouteArrowsRenderer: MKPolylineRenderer {
    /// Screen points between chevrons.
    static let spacingPoints: CGFloat = 70
    /// Chevron length and half-width, screen points.
    static let lengthPoints: CGFloat = 12
    static let halfWidthPoints: CGFloat = 7
    static let laneGapPoints: CGFloat = 14
    static let laneOffsetPoints: CGFloat = 6
    /// Walk through: faded legs keep this much of their opacity.
    static let fadeAlpha: CGFloat = 0.22
    /// White with a dark shade of the route magenta for its edge: it sits on
    /// the magenta line, so it reads on satellite imagery and the standard map.
    static let fill = UIColor.white
    static let edge = UIColor(red: 0.45, green: 0.0, blue: 0.27, alpha: 1)

    override func draw(_ mapRect: MKMapRect, zoomScale: MKZoomScale, in context: CGContext) {
        let poly = polyline
        guard poly.pointCount >= 2, zoomScale > 0 else { return }
        let arrows = poly as? PlannedRouteArrowsPolyline
        let fade: CGFloat = arrows?.isFaded == true ? Self.fadeAlpha : 1
        let unit = 1 / zoomScale
        let mapPoints = UnsafeBufferPointer(start: poly.points(), count: poly.pointCount)
        let pts = mapPoints.map { point(for: $0) }
        let chevrons = PlannedRouteArrows.place(
            along: pts, spacing: Self.spacingPoints * unit,
            laneGap: Self.laneGapPoints * unit, laneOffset: Self.laneOffsetPoints * unit)
        let visible = rect(for: mapRect).insetBy(dx: -20 * unit, dy: -20 * unit)
        let len = Self.lengthPoints * unit, half = Self.halfWidthPoints * unit
        context.setLineJoin(.round)
        context.setLineWidth(1.5 * unit)
        context.setStrokeColor(Self.edge.withAlphaComponent(fade).cgColor)
        context.setFillColor(Self.fill.withAlphaComponent(fade).cgColor)
        for c in chevrons where visible.contains(c.point) {
            let cosA = cos(c.angle), sinA = sin(c.angle)
            func at(_ along: CGFloat, _ side: CGFloat) -> CGPoint {
                CGPoint(x: c.point.x + along * cosA - side * sinA, y: c.point.y + along * sinA + side * cosA)
            }
            // A notched arrowhead: tip ahead, two wings behind, notch between.
            context.beginPath()
            context.move(to: at(len / 2, 0))
            context.addLine(to: at(-len / 2, half))
            context.addLine(to: at(-len / 6, 0))
            context.addLine(to: at(-len / 2, -half))
            context.closePath()
            context.drawPath(using: .fillStroke)
        }
    }
}
