import CoreGraphics
import Foundation
// "Plan a Hike", round 7: side-by-side passes. When a route walks the same
// edge more than once, each pass is drawn as its own line, offset sideways a
// few screen points, so an out-and-back reads as two lanes instead of one
// line drawn on top of itself. Pure math, no MapKit.
enum PlannedRouteLanes {
    /// Screen points between neighbouring lanes. Two passes sit at plus and
    /// minus half of this (4 pt) from the trail line.
    static let spacingPoints: CGFloat = 8
    /// One walk step's pass over its edge.
    struct Pass: Equatable, Sendable {
        /// 0-based, in walking order, among the walks of this edge.
        let index: Int
        /// How many times the route walks this edge.
        let count: Int
        /// Walked the same way as the edge's first pass.
        let sameDirectionAsFirst: Bool
        /// Sideways position in lanes, to the right of the walking direction
        /// (positive = walker's right). 0 for an edge walked once.
        var lane: Double {
            PlannedRouteLanes.lane(index: index, count: count, sameDirectionAsFirst: sameDirectionAsFirst)
        }
    }
    /// Pass of each walk step, in walking order. Steps are (edge id,
    /// direction); a negative edge id gets a single pass.
    static func passes(_ steps: [(edgeId: Int, direction: RouteDirection)]) -> [Pass] {
        var total: [Int: Int] = [:]
        for s in steps where s.edgeId >= 0 { total[s.edgeId, default: 0] += 1 }
        var seen: [Int: Int] = [:]
        var firstDirection: [Int: RouteDirection] = [:]
        return steps.map { s in
            guard s.edgeId >= 0 else { return Pass(index: 0, count: 1, sameDirectionAsFirst: true) }
            let k = seen[s.edgeId, default: 0]
            seen[s.edgeId] = k + 1
            let first = firstDirection[s.edgeId] ?? s.direction
            firstDirection[s.edgeId] = first
            return Pass(index: k, count: total[s.edgeId] ?? 1, sameDirectionAsFirst: first == s.direction)
        }
    }
    /// Lanes are spread evenly around the trail line, the first pass on the
    /// right of its own direction. The value is walker-relative: an
    /// out-and-back puts both passes on the walker's right (+0.5), which is
    /// opposite sides of the line because they face opposite ways.
    static func lane(index: Int, count: Int, sameDirectionAsFirst: Bool) -> Double {
        guard count > 1 else { return 0 }
        let fromFirst = Double(count - 1) / 2 - Double(index)
        return sameDirectionAsFirst ? fromFirst : -fromFirst
    }
    /// Sideways offset, in the same units as the points, for `lane` at a map
    /// scale of `pointsPerUnit` screen points per point unit. Constant on
    /// screen at every zoom.
    static func offset(lane: Double, pointsPerUnit: CGFloat) -> CGFloat {
        guard pointsPerUnit > 0, pointsPerUnit.isFinite else { return 0 }
        return CGFloat(lane) * spacingPoints / pointsPerUnit
    }
    /// `points` moved `distance` to the right of the direction of travel
    /// (y grows downward, as on screen and in MapKit map points). Each vertex
    /// moves along the mitre of its two neighbouring segments, so lines keep
    /// their spacing through bends; the mitre is capped at twice the
    /// distance so a sharp turn does not spike.
    static func offset(_ points: [CGPoint], by distance: CGFloat) -> [CGPoint] {
        guard points.count >= 2, distance != 0, distance.isFinite else { return points }
        // Unit right-hand normal of each segment; nil for a zero-length one.
        let normals: [CGPoint?] = (0..<(points.count - 1)).map { i in
            let dx = points[i + 1].x - points[i].x, dy = points[i + 1].y - points[i].y
            let len = (dx * dx + dy * dy).squareRoot()
            return len > 0 ? CGPoint(x: -dy / len, y: dx / len) : nil
        }
        func before(_ i: Int) -> CGPoint? {
            var j = i - 1
            while j >= 0 { if let n = normals[j] { return n }; j -= 1 }
            return nil
        }
        func after(_ i: Int) -> CGPoint? {
            var j = i
            while j < normals.count { if let n = normals[j] { return n }; j += 1 }
            return nil
        }
        return points.indices.map { i in
            let a = before(i), b = after(i)
            let n: CGPoint
            var scale = distance
            switch (a, b) {
            case let (a?, b?):
                let m = CGPoint(x: a.x + b.x, y: a.y + b.y)
                let len = (m.x * m.x + m.y * m.y).squareRoot()
                if len < 1e-6 {
                    // A full turn-around: keep the incoming side.
                    n = a
                } else {
                    n = CGPoint(x: m.x / len, y: m.y / len)
                    let cosHalf = n.x * b.x + n.y * b.y
                    scale = cosHalf > 0.5 ? distance / cosHalf : distance * 2
                }
            case let (a?, nil): n = a
            case let (nil, b?): n = b
            case (nil, nil): return points[i]
            }
            return CGPoint(x: points[i].x + n.x * scale, y: points[i].y + n.y * scale)
        }
    }
    /// Lays out consecutive route pieces, each moved by its own offset. Where
    /// one piece starts at the previous piece's last point but sits in a
    /// different lane, the previous piece's offset end is put in front of
    /// it, so the line steps across instead of breaking.
    static func layout(_ pieces: [(points: [CGPoint], offset: CGFloat)]) -> [[CGPoint]] {
        var out: [[CGPoint]] = []
        for (i, piece) in pieces.enumerated() {
            var line = offset(piece.points, by: piece.offset)
            if i > 0, let prevEnd = pieces[i - 1].points.last, let start = piece.points.first,
               hypot(prevEnd.x - start.x, prevEnd.y - start.y) < 1e-9,
               pieces[i - 1].offset != piece.offset, let joined = out.last?.last {
                line.insert(joined, at: 0)
            }
            out.append(line)
        }
        return out
    }
}
