import Foundation

/// Reproduces the piece ordering `trailforge/serve/elevation.py`
/// (`_chain_segments`) used when it baked `Trail.profileFt`.
///
/// The profile is NOT in stored segment order: the pipeline reorders and flips
/// a multi-segment trail's pieces (greedy nearest-end chaining, trying every
/// start and direction, keeping the least total gap) before sampling. To map a
/// position on a piece to an index in `profileFt` the app has to walk the same
/// chain. `TrailProfile.polyline` uses stored order, which is only right for
/// single-piece trails.
enum TrailNetworkChain {
    struct Link: Sendable, Equatable {
        let piece: Int
        let flipped: Bool
    }

    struct Result: Sendable {
        /// Pieces in chain order.
        let links: [Link]
        /// Gap in metres between consecutive links (`links.count - 1` entries).
        let gaps: [Double]
        /// false when the trail had too many pieces to try every start, so only
        /// the greedy chain from piece 0 was built. Elevation is then not
        /// mapped, because the order may differ from the pipeline's.
        let exhaustive: Bool
        var maxGap: Double { gaps.max() ?? 0 }
    }

    /// Above this many pieces, trying every start gets expensive and the
    /// result is not mapped to elevation. The 99th-percentile trail has a
    /// handful of pieces.
    static let exhaustiveLimit = 200

    /// `heads[i]` / `tails[i]` are piece i's first and last points.
    static func order(heads: [LocalPt], tails: [LocalPt]) -> Result {
        let n = heads.count
        guard n > 0 else { return Result(links: [], gaps: [], exhaustive: true) }
        guard n > 1 else {
            return Result(links: [Link(piece: 0, flipped: false)], gaps: [], exhaustive: true)
        }
        let exhaustive = n <= exhaustiveLimit
        var best: (total: Double, links: [Link], gaps: [Double])?
        let starts = exhaustive ? Array(0..<n) : [0]
        let flips = exhaustive ? [false, true] : [false]
        // Same iteration order as the Python: start outer, flip inner, keep the
        // first strictly-smaller total.
        for start in starts {
            for flip in flips {
                let candidate = build(start: start, flipStart: flip, heads: heads, tails: tails)
                if best == nil || candidate.total < best!.total { best = candidate }
            }
        }
        let b = best!
        return Result(links: b.links, gaps: b.gaps, exhaustive: exhaustive)
    }

    private static func build(
        start: Int, flipStart: Bool, heads: [LocalPt], tails: [LocalPt]
    ) -> (total: Double, links: [Link], gaps: [Double]) {
        var remaining = Array(0..<heads.count)
        remaining.remove(at: start)
        var links = [Link(piece: start, flipped: flipStart)]
        var gaps: [Double] = []
        var total = 0.0
        var tail = flipStart ? heads[start] : tails[start]
        while !remaining.isEmpty {
            var bestIdx = 0
            var bestD = Double.infinity
            var bestFlip = false
            for (k, piece) in remaining.enumerated() {
                let dHead = TurnCueGeometry.distance(tail, heads[piece])
                let dEnd = TurnCueGeometry.distance(tail, tails[piece])
                if dHead < bestD { bestIdx = k; bestD = dHead; bestFlip = false }
                if dEnd < bestD { bestIdx = k; bestD = dEnd; bestFlip = true }
            }
            let piece = remaining.remove(at: bestIdx)
            links.append(Link(piece: piece, flipped: bestFlip))
            gaps.append(bestD)
            total += bestD
            tail = bestFlip ? heads[piece] : tails[piece]
        }
        return (total, links, gaps)
    }
}
