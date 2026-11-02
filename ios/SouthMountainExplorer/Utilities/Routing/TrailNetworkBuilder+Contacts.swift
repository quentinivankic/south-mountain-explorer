import Foundation

/// Phase 2: where do pieces touch? Everything is searched against the ORIGINAL
/// geometry; cut points are collected and inserted afterwards in phase 3.
extension TrailNetworkBuilder {
    mutating func findContacts() {
        segStart = []
        for (pi, piece) in pieces.enumerated() {
            segStart.append(segPiece.count)
            for i in 0..<(piece.pts.count - 1) {
                grid.insert(Int32(segPiece.count), a: piece.pts[i], b: piece.pts[i + 1])
                segPiece.append(Int32(pi))
                segIndex.append(Int32(i))
            }
        }
        stamps = [Int32](repeating: 0, count: segPiece.count)
        findVertexContacts()
        findCrossings()
    }

    /// Endpoints: touch (<= contactMeters), bridge (<= bridgeMeters), or
    /// nothing. Mid-trail vertices: only a tight merge (<= vertexMergeMeters),
    /// which stitches collinear and co-aligned geometry whose vertices differ.
    private mutating func findVertexContacts() {
        for (pi, piece) in pieces.enumerated() {
            let last = piece.pts.count - 1
            for v in 0...last {
                let isEnd = v == 0 || v == last
                let reach = isEnd ? options.bridgeMeters : options.vertexMergeMeters
                guard let hit = nearest(to: piece.pts[v], excluding: pi, within: reach) else { continue }
                let host = resolve(piece: Int(segPiece[hit.seg]), seg: Int(segIndex[hit.seg]), t: hit.t)
                let here = VRef.orig(pi, v)
                let touching = hit.dist <= (isEnd ? options.contactMeters : options.vertexMergeMeters)
                if touching {
                    unions.append((here, host))
                } else {
                    bridges.append((here, host))
                }
            }
        }
    }

    /// Intersections between segments of different pieces (or non-adjacent
    /// segments of one piece). Includes a line passing exactly through the
    /// other's vertex; `resolve` then reuses that vertex and cuts the other.
    private mutating func findCrossings() {
        for s in 0..<segPiece.count {
            let pa = Int(segPiece[s]), ia = Int(segIndex[s])
            let a0 = pieces[pa].pts[ia], a1 = pieces[pa].pts[ia + 1]
            stampClock += 1
            stamps[s] = stampClock
            var found: [Int] = []
            for cx in grid.cellRange(min(a0.x, a1.x), max(a0.x, a1.x)) {
                for cy in grid.cellRange(min(a0.y, a1.y), max(a0.y, a1.y)) {
                    guard let ids = grid.cells[SegmentGrid.key(cx, cy)] else { continue }
                    for id in ids {
                        let o = Int(id)
                        if o <= s || stamps[o] == stampClock { continue }
                        stamps[o] = stampClock
                        found.append(o)
                    }
                }
            }
            found.sort()
            for o in found {
                let pb = Int(segPiece[o]), ib = Int(segIndex[o])
                if pa == pb && ib - ia < 2 { continue }
                let b0 = pieces[pb].pts[ib], b1 = pieces[pb].pts[ib + 1]
                guard let (t, u) = Self.intersection(a0, a1, b0, b1) else { continue }
                let ra = resolve(piece: pa, seg: ia, t: t)
                let rb = resolve(piece: pb, seg: ib, t: u)
                if ra != rb { unions.append((ra, rb)) }
            }
        }
    }

    /// Parameters of a crossing (closed intervals), else nil. Parallel and
    /// collinear segments never intersect here.
    static func intersection(
        _ p: LocalPt, _ p2: LocalPt, _ q: LocalPt, _ q2: LocalPt
    ) -> (Double, Double)? {
        let rx = p2.x - p.x, ry = p2.y - p.y
        let sx = q2.x - q.x, sy = q2.y - q.y
        let den = rx * sy - ry * sx
        guard abs(den) > 1e-9 else { return nil }
        let qpx = q.x - p.x, qpy = q.y - p.y
        let t = (qpx * sy - qpy * sx) / den
        let u = (qpx * ry - qpy * rx) / den
        guard t >= 0, t <= 1, u >= 0, u <= 1 else { return nil }
        return (t, u)
    }

    /// Closest segment to `p` within `reach` metres, ignoring one piece.
    /// Ties go to the lower segment id so results are order-stable.
    mutating func nearest(
        to p: LocalPt, excluding piece: Int, within reach: Double
    ) -> (seg: Int, t: Double, dist: Double)? {
        stampClock += 1
        var best: (seg: Int, t: Double, dist: Double)?
        for cx in grid.cellRange(p.x - reach, p.x + reach) {
            for cy in grid.cellRange(p.y - reach, p.y + reach) {
                guard let ids = grid.cells[SegmentGrid.key(cx, cy)] else { continue }
                for id in ids {
                    let s = Int(id)
                    if stamps[s] == stampClock { continue }
                    stamps[s] = stampClock
                    let pi = Int(segPiece[s])
                    if pi == piece { continue }
                    let i = Int(segIndex[s])
                    let r = TurnCueGeometry.projectOnSegment(p, pieces[pi].pts[i], pieces[pi].pts[i + 1])
                    guard r.dist <= reach else { continue }
                    if best == nil || r.dist < best!.dist || (r.dist == best!.dist && s < best!.seg) {
                        best = (s, r.t, r.dist)
                    }
                }
            }
        }
        return best
    }

    /// Turns "point at parameter t on a segment" into a vertex reference:
    /// an existing vertex if within vertexMergeMeters, else a (shared) cut.
    mutating func resolve(piece: Int, seg: Int, t: Double) -> VRef {
        let cum = pieces[piece].cum
        let segLen = cum[seg + 1] - cum[seg]
        let along = t * segLen
        if along <= options.vertexMergeMeters { return .orig(piece, seg) }
        if segLen - along <= options.vertexMergeMeters { return .orig(piece, seg + 1) }
        let global = segStart[piece] + seg
        for existing in cutsBySeg[global] ?? [] where abs(existing.t - t) * segLen <= options.vertexMergeMeters {
            return .cut(existing.id)
        }
        let id = cuts.count
        cuts.append(Cut(piece: piece, seg: seg, t: t))
        cutsBySeg[global, default: []].append((t, id))
        return .cut(id)
    }
}
