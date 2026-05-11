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
    /// OSM models named "loop trails" two ways:
    ///   1. A single closed way where the first node == last node, or
    ///   2. Several same-named ways stitched together at shared endpoints
    ///      (each individual way is linear; the loop emerges from the union).
    ///
    /// We detect both cases:
    ///   - Closed-way path: any segment whose first ≈ last point.
    ///   - Stitched-loop path: total trail length comfortably exceeds the
    ///     straight-line distance between the two farthest segment
    ///     endpoints. A linear trail walks ≈ 1× span end-to-end, an
    ///     out-and-back ≈ 2×, a real loop ≈ 3×+. 2.2× catches loops while
    ///     leaving out-and-backs labelled linear.
    var routeType: RouteType {
        for seg in segments where seg.count >= 3 {
            if let first = seg.first, let last = seg.last,
               first.count >= 2, last.count >= 2,
               endpointGapMi(first, last) < 0.05 {
                return .loop
            }
        }

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
        // Very-tight cluster of endpoints (everything within ~0.1 mi) is a
        // small loop regardless of total miles. Otherwise apply the
        // length/span ratio test.
        if maxSpan < 0.1 { return .loop }
        return distanceMi > 2.2 * maxSpan ? .loop : .linear
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
