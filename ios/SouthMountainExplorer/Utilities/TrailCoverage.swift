import Foundation

/// Per-trail output of `measureCoverage`. `fraction` is the share of
/// polyline nodes within `bufferMeters` of the recorded GPS path.
/// `endpointsVisited` is true only when BOTH the first and last
/// polyline nodes are within `bufferMeters` of the path — i.e. the
/// hiker actually reached both ends, not just covered most of the
/// length. Two-gate completion (fraction ≥ threshold AND
/// `endpointsVisited`) is what stops "I walked 90% of a linear trail
/// but turned around before the end" from firing the celebration.
struct CoverageScore: Equatable, Sendable {
    let fraction: Double
    let endpointsVisited: Bool
}

/// Score every trail in `trails` against the recorded GPS `path`. Pure
/// function — no global state, safe to call from tests. Trails below
/// `minVisibleFraction` are filtered out as GPS noise (the user
/// probably skirted the trail without actually walking it).
///
/// Used by `RecordingService` for live coverage (mid-hike) and final
/// coverage (on stop), and replayed against historical hikes by
/// `rebuildCoverageFromHistory`. Also used directly by
/// `RecordingServiceTests`.
///
/// `bufferMeters` is the radius used for the per-node "did the GPS
/// path come within range" check. `endpointBufferMeters` is the
/// tighter radius used specifically for the start/end endpoint
/// check — tightened in build 16 from 15 m → 10 m after a user
/// report that completion fired with the hiker still noticeably
/// short of the trail end. GPS scatter at hiking pace is ±5-10 m,
/// so 10 m still triggers when the user is physically at the
/// endpoint while ruling out the "I turned around 12 m short"
/// false positive.
func measureCoverage(
    path: [GpsPoint],
    trails: [Trail],
    bufferMeters: Double = 30.0,
    endpointBufferMeters: Double = 10.0,
    minVisibleFraction: Double = 0.02
) -> [String: CoverageScore] {
    guard path.count >= 3 else { return [:] }

    var grid = SpatialGrid()
    for p in path { grid.insert(p) }

    func nodeVisited(_ node: [Double], withinMeters: Double) -> Bool {
        guard node.count >= 2 else { return false }
        return grid.hasNeighbor(lat: node[0], lon: node[1], withinMeters: withinMeters)
    }

    var result: [String: CoverageScore] = [:]
    for trail in trails {
        var total = 0
        var covered = 0
        for seg in trail.segments {
            for node in seg {
                guard node.count >= 2 else { continue }
                total += 1
                if nodeVisited(node, withinMeters: bufferMeters) { covered += 1 }
            }
        }
        guard total > 0 else { continue }
        let frac = Double(covered) / Double(total)
        guard frac > minVisibleFraction else { continue }

        // Endpoints: start of first segment, end of last segment.
        // Uses a TIGHTER buffer than the general-coverage check —
        // we want endpoints to read true only when the user
        // actually reached them, not when they got "close enough"
        // for general coverage purposes.
        let endpointsHit: Bool
        if
            let firstSeg = trail.segments.first, let start = firstSeg.first,
            let lastSeg = trail.segments.last, let end = lastSeg.last
        {
            endpointsHit = nodeVisited(start, withinMeters: endpointBufferMeters)
                && nodeVisited(end, withinMeters: endpointBufferMeters)
        } else {
            endpointsHit = false
        }
        result[trail.id] = CoverageScore(fraction: frac, endpointsVisited: endpointsHit)
    }
    return result
}
