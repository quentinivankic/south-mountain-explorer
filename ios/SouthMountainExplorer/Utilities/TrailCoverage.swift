import Foundation

/// How much of a trail a GPS path covered, and whether that adds up to
/// having walked the thing.
///
/// `fraction` is the share of polyline nodes within `bufferMeters` of the
/// path. `longestSkippedRunM` is the longest CONTIGUOUS stretch of trail
/// LENGTH whose nodes went uncovered. Completion is `fraction ≥ threshold`
/// AND `longestSkippedRunM ≤ maxSkippedRunMeters` — see `completesTrail`.
///
/// `endpointsVisited` is retained for DIAGNOSTICS ONLY and no longer gates
/// anything. It used to, and it was wrong: see `completesTrail`.
struct CoverageScore: Equatable, Sendable {
    let fraction: Double
    let endpointsVisited: Bool
    let longestSkippedRunM: Double
}

/// Share of a trail's nodes that must be covered. Bumped 0.90 → 0.95 in
/// build 13 after device testing.
let completionFractionThreshold = 0.95

/// The longest stretch of trail you may skip and still be credited.
///
/// This REPLACED an "did you reach both endpoints" gate, which asked the
/// right question the wrong way. That gate took the first node of the first
/// segment and the last node of the last segment — meaningful only when a
/// trail is one contiguous, correctly-ordered polyline. 11,191 of 92,297
/// shipped trails are stored as several DISCONNECTED pieces in arbitrary
/// OSM order, so for those it tested two arbitrary interior points. Pima
/// West Loop Trail is a closed 1.5 mi loop plus a 19 m orphan fragment 127 m
/// away; its "last node" sits on the orphan, so walking the entire loop could
/// never complete it. Guadalupe Perimeter is five pieces whose nominal ends
/// are a mile apart with no trail between them.
///
/// A skipped-run limit asks what the endpoint gate was really after — "you
/// didn't miss a chunk" — without needing to know where a trail's ends are,
/// so it behaves identically for loops, lines, branches and gapped trails.
///
/// 50 m, measured. Simulated over all 11,191 disconnected trails plus a 6,000
/// random control, against hikers who walked the whole trail, walked only the
/// reachable piece, and turned round 100 m / 250 m short:
///
///     rule          fixes (disconnected)   false completions (100 m short)
///     endpoints             —                 454 disconnected / 180 ordinary
///     no gate at all      +871                939 / 2,675   ← 46% of ordinary!
///     skip ≤ 50 m         +240                185 /   270
///     skip ≤ 100 m        +480                587 / 1,633
///
/// So 50 m fixes more than the endpoint gate did AND halves its false
/// completions. The threshold must sit well BELOW the shortfall it should
/// catch — at 100 m, stopping 100 m short leaves a ~100 m run that passes.
/// It must also sit above `bufferMeters` (30 m), or ordinary GPS scatter
/// across a couple of nodes would read as a skipped stretch and block a real
/// completion. 50 m is the window between those two constraints.
let maxSkippedRunMeters = 50.0

extension CoverageScore {
    /// The completion gate. One place, so every caller agrees.
    var completesTrail: Bool {
        fraction >= completionFractionThreshold
            && longestSkippedRunM <= maxSkippedRunMeters
    }
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
        // Longest contiguous stretch of trail LENGTH whose nodes went
        // uncovered — measured per segment, in metres, because "how much did
        // you skip" is a distance question and a node count is not. A segment
        // that goes entirely uncovered contributes its whole length as one
        // skipped run, which is what catches a disconnected fragment the hiker
        // never reached.
        var longestSkipped = 0.0
        for seg in trail.segments {
            var run = 0.0
            var previous: [Double]?
            for node in seg {
                guard node.count >= 2 else { continue }
                total += 1
                let hit = nodeVisited(node, withinMeters: bufferMeters)
                if hit { covered += 1 }
                if let p = previous, !hit {
                    run += haversineDistanceM(lat1: p[0], lon1: p[1],
                                              lat2: node[0], lon2: node[1])
                }
                if hit {
                    longestSkipped = max(longestSkipped, run)
                    run = 0
                }
                previous = node
            }
            longestSkipped = max(longestSkipped, run)
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
        result[trail.id] = CoverageScore(fraction: frac,
                                        endpointsVisited: endpointsHit,
                                        longestSkippedRunM: longestSkipped)
    }
    return result
}

/// Length-based coverage fraction per trail: sum of polyline-edge
/// lengths where BOTH endpoints fall within `bufferMeters` of a GPS
/// sample, divided by the trail's total polyline length. Mirrors the
/// "run of ≥ 2 consecutive covered nodes" rule used by
/// `TrailMapView.trailNodeRuns` for the orange post-completion
/// overlay — an edge contributes iff both of its nodes would belong
/// to the same rendered run.
///
/// Used for the displayed "% remaining" in `TrailDetailSheet`. The
/// node-count `measureCoverage` above still drives the completion
/// gate (intentionally looser at 30m so completion is reachable
/// despite GPS scatter); this function is purely for display so the
/// bar literally describes the length of orange drawn on the map.
///
/// Returns 0 for trails with no covered edges; omits trails with
/// zero total length (defensive — shouldn't happen for real OSM
/// polylines).
func measureCoverageByLength(
    path: [GpsPoint],
    trails: [Trail],
    bufferMeters: Double = 10.0
) -> [String: Double] {
    guard path.count >= 3 else { return [:] }

    var grid = SpatialGrid()
    for p in path { grid.insert(p) }

    var result: [String: Double] = [:]
    for trail in trails {
        var totalLen = 0.0
        var coveredLen = 0.0
        for seg in trail.segments {
            guard seg.count >= 2 else { continue }
            for i in 0..<(seg.count - 1) {
                let a = seg[i]
                let b = seg[i + 1]
                guard a.count >= 2, b.count >= 2 else { continue }
                let len = haversineDistanceM(lat1: a[0], lon1: a[1], lat2: b[0], lon2: b[1])
                totalLen += len
                let aHit = grid.hasNeighbor(lat: a[0], lon: a[1], withinMeters: bufferMeters)
                let bHit = grid.hasNeighbor(lat: b[0], lon: b[1], withinMeters: bufferMeters)
                if aHit && bHit { coveredLen += len }
            }
        }
        guard totalLen > 0 else { continue }
        result[trail.id] = min(1.0, coveredLen / totalLen)
    }
    return result
}
