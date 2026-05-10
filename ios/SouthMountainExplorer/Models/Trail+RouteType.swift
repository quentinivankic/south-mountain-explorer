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
    /// Classify a trail as loop vs linear by checking whether the longest
    /// segment's endpoints meet up. Picking the longest segment (instead of
    /// concatenating all of them) keeps the signal clean for OSM trails made
    /// of multiple same-named ways — short connector spurs would otherwise
    /// make a real loop look linear.
    var routeType: RouteType {
        guard let longest = segments.max(by: { $0.count < $1.count }),
              let first = longest.first, first.count >= 2,
              let last = longest.last, last.count >= 2
        else { return .linear }
        return endpointGapMi(first, last) < 0.15 ? .loop : .linear
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
