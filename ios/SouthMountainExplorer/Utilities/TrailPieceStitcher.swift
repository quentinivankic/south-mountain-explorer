import Foundation

/// Orders the stored pieces (OSM ways) of ONE trail into a physical walking
/// order. Shared by the turn-cue route builder and the hike estimator so both
/// agree on piece order and on which gaps are bridged.
///
/// Bridge rule: pieces of the same trail are joined across a gap of up to
/// `sameTrailBridgeMeters` (flagged unverified: the straight line between the
/// ends is a guess, not mapped trail).
///
/// `sameTrailBridgeMeters` is the one same-trail bridge limit in the app:
/// the Plan a Hike network (`TrailNetwork.Options.sameTrailBridgeMeters`)
/// uses it as its default too.
enum TrailPieceStitcher {
    static let sameTrailBridgeMeters = 180.0
    /// Gaps at or below this are treated as contiguous (not flagged as a bridge).
    static let contiguousMeters = 12.0

    struct Placement: Equatable {
        /// Index into the input pieces.
        let piece: Int
        /// True when the piece is walked from its stored last point to its first.
        let reversed: Bool
        /// Distance between the previous placement's far end and this
        /// placement's near end. 0 for the first placement of a chain.
        let gapBefore: Double
    }

    /// Greedy nearest-end chaining. Each chain starts at the piece chosen by
    /// `pickStart` (given the indices still unplaced) and grows at both ends
    /// with the closest unplaced piece end within `maxGap`.
    /// - Returns: chains, each in walking order.
    static func order<P>(
        ends: [(first: P, last: P)],
        maxGap: Double,
        distance: (P, P) -> Double,
        pickStart: ([Int]) -> Int
    ) -> [[Placement]] {
        var remaining = Array(ends.indices)
        var chains: [[Placement]] = []
        while !remaining.isEmpty {
            let start = pickStart(remaining)
            remaining.removeAll { $0 == start }
            var chain: [Placement] = [Placement(piece: start, reversed: false, gapBefore: 0)]
            var head = ends[start].first
            var tail = ends[start].last
            while true {
                var bestPos: Int?
                var bestDist = maxGap
                var bestMode = 0 // 0 append, 1 append reversed, 2 prepend, 3 prepend reversed
                for (pos, i) in remaining.enumerated() {
                    let e = ends[i]
                    let cands: [(Double, Int)] = [
                        (distance(tail, e.first), 0),
                        (distance(tail, e.last), 1),
                        (distance(head, e.last), 2),
                        (distance(head, e.first), 3),
                    ]
                    for (d, m) in cands where d <= bestDist {
                        bestDist = d; bestPos = pos; bestMode = m
                    }
                }
                guard let pos = bestPos else { break }
                let i = remaining.remove(at: pos)
                let e = ends[i]
                switch bestMode {
                case 0:
                    chain.append(Placement(piece: i, reversed: false, gapBefore: bestDist))
                    tail = e.last
                case 1:
                    chain.append(Placement(piece: i, reversed: true, gapBefore: bestDist))
                    tail = e.first
                case 2:
                    // The old head now follows the new piece, so it carries the gap.
                    chain[0] = Placement(piece: chain[0].piece, reversed: chain[0].reversed, gapBefore: bestDist)
                    chain.insert(Placement(piece: i, reversed: false, gapBefore: 0), at: 0)
                    head = e.first
                default:
                    chain[0] = Placement(piece: chain[0].piece, reversed: chain[0].reversed, gapBefore: bestDist)
                    chain.insert(Placement(piece: i, reversed: true, gapBefore: 0), at: 0)
                    head = e.last
                }
            }
            chains.append(chain)
        }
        return chains
    }
}
