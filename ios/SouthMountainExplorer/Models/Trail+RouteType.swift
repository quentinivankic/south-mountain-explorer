import Foundation

enum RouteType: String, CaseIterable, Identifiable, Sendable {
    case loop, linear

    var id: String { rawValue }

    var label: String {
        switch self {
        case .loop:   return "Loop"
        case .linear: return "Linear"
        }
    }

    var systemImage: String {
        switch self {
        case .loop:   return "arrow.triangle.2.circlepath"
        case .linear: return "arrow.left.and.right"
        }
    }
}

extension Trail {
    /// Classify a trail as loop vs linear.
    ///
    /// OSM models named loop trails two ways:
    ///   1. A single closed way where the first node == last node.
    ///   2. Several same-named ways stitched at shared endpoints (each
    ///      individual way is linear; the loop emerges from the union).
    ///
    /// Heuristic, in priority order:
    ///   - **Cluster check.** If every segment endpoint sits inside a
    ///     ~0.1 mi cluster (maxSpan ≈ 0), the trail loops back on
    ///     itself. Catches single closed ways automatically (their
    ///     first/last point coincide) without needing a per-segment
    ///     shortcut — earlier versions did that and would flip a long
    ///     linear trail to Loop the moment it contained one tiny
    ///     closed spur or switchback artifact.
    ///   - **Length/span ratio.** A true loop's total mileage exceeds
    ///     its span by ≈ π (circle: circumference / diameter ≈ 3.14).
    ///     Switchback-heavy linear trails — common in real OSM data
    ///     for mountain ascents — top out around 2.5×. So the
    ///     threshold sits at 3.0×, comfortably above switchbacks and
    ///     right at the geometric floor for real loops.
    var routeType: RouteType {
        var endpoints: [[Double]] = []
        for seg in segments where seg.count >= 2 {
            if let f = seg.first, f.count >= 2 { endpoints.append(f) }
            if let l = seg.last,  l.count >= 2 { endpoints.append(l) }
        }
        guard endpoints.count >= 2 else { return .linear }

        var maxSpan = 0.0
        for i in 0..<endpoints.count {
            for j in (i + 1)..<endpoints.count {
                let g = endpointGapMi(endpoints[i], endpoints[j])
                if g > maxSpan { maxSpan = g }
            }
        }
        if maxSpan < 0.1 { return .loop }
        return distanceMi > 3.0 * maxSpan ? .loop : .linear
    }
}

private func endpointGapMi(_ a: [Double], _ b: [Double]) -> Double {
    let R = 3958.8
    let dLat = (b[0] - a[0]) * .pi / 180
    let dLon = (b[1] - a[1]) * .pi / 180
    let h = sin(dLat / 2) * sin(dLat / 2)
        + cos(a[0] * .pi / 180) * cos(b[0] * .pi / 180)
        * sin(dLon / 2) * sin(dLon / 2)
    return R * 2 * atan2(sqrt(h), sqrt(1 - h))
}
