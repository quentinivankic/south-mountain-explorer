import Foundation

/// Iterative Douglas-Peucker for trail polyline simplification.
/// Operates on the project's raw `[[lat, lon]]` segment format and
/// returns a subset preserving the line's overall shape within
/// `epsilonMeters` of perpendicular distance.
///
/// Iterative (stack-based) instead of recursive: trail polylines
/// can run hundreds of points and the call stack on a deeply
/// recursive DP is not worth the risk.
///
/// Distance math is flat-earth meters relative to the segment's
/// first latitude. At hiking-trail scales (~tens of km span) the
/// cos(lat) drift across the segment is <0.1% — well below the
/// epsilon we care about.
enum PolylineDecimator {
    static func decimate(_ coords: [[Double]], epsilonMeters: Double) -> [[Double]] {
        guard coords.count > 2 else { return coords }
        // Reject malformed points up front so the math below can't
        // index past the end of a row.
        guard coords.allSatisfy({ $0.count >= 2 }) else { return coords }

        let refLat = coords[0][0]
        let cosRef = cos(refLat * .pi / 180.0)
        let metersPerDegLat = 111_320.0
        let metersPerDegLon = 111_320.0 * cosRef

        let xy: [(x: Double, y: Double)] = coords.map { c in
            (c[1] * metersPerDegLon, c[0] * metersPerDegLat)
        }

        var keep = Array(repeating: false, count: coords.count)
        keep[0] = true
        keep[coords.count - 1] = true

        var stack: [(Int, Int)] = [(0, coords.count - 1)]
        while let (start, end) = stack.popLast() {
            guard end - start > 1 else { continue }
            let (x1, y1) = xy[start]
            let (x2, y2) = xy[end]
            let dx = x2 - x1
            let dy = y2 - y1
            let segLenSquared = dx * dx + dy * dy
            let segLen = segLenSquared.squareRoot()
            var maxDist = 0.0
            var maxIdx = start
            for i in (start + 1)..<end {
                let (px, py) = xy[i]
                let d: Double
                if segLen == 0 {
                    let ddx = px - x1
                    let ddy = py - y1
                    d = (ddx * ddx + ddy * ddy).squareRoot()
                } else {
                    d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / segLen
                }
                if d > maxDist {
                    maxDist = d
                    maxIdx = i
                }
            }
            if maxDist > epsilonMeters {
                keep[maxIdx] = true
                stack.append((start, maxIdx))
                stack.append((maxIdx, end))
            }
        }

        var result: [[Double]] = []
        result.reserveCapacity(keep.count)
        for i in 0..<coords.count where keep[i] {
            result.append(coords[i])
        }
        return result
    }
}
