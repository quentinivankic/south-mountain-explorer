import Foundation

/// Phases 3-6: vertices, nodes, fine edges, simplification.
extension TrailNetworkBuilder {
    func build() -> TrailNetwork {
        var b = self
        return b.run()
    }

    private mutating func run() -> TrailNetwork {
        findContacts()

        // Phase 3: insert cuts, assign global vertex ids.
        var verts: [Vertex] = []
        var origGid: [[Int]] = []
        var cutGid = [Int](repeating: -1, count: cuts.count)
        var pieceGids: [[Int]] = []
        for (pi, piece) in pieces.enumerated() {
            var gids: [Int] = []
            var orig: [Int] = []
            let last = piece.pts.count - 1
            for v in 0...last {
                orig.append(verts.count)
                gids.append(verts.count)
                verts.append(Vertex(p: piece.pts[v], c: piece.ll[v], piece: pi,
                                    arc: piece.cum[v], rank: (v == 0 || v == last) ? 1 : 0))
                guard v < last else { continue }
                let onSeg = (cutsBySeg[segStart[pi] + v] ?? []).sorted { $0.t < $1.t }
                for cut in onSeg {
                    let a = piece.pts[v], bPt = piece.pts[v + 1]
                    let ca = piece.ll[v], cb = piece.ll[v + 1]
                    let t = cut.t
                    cutGid[cut.id] = verts.count
                    gids.append(verts.count)
                    verts.append(Vertex(
                        p: LocalPt(x: a.x + (bPt.x - a.x) * t, y: a.y + (bPt.y - a.y) * t),
                        c: Coordinate(lat: ca.lat + (cb.lat - ca.lat) * t, lon: ca.lon + (cb.lon - ca.lon) * t),
                        piece: pi,
                        arc: piece.cum[v] + (piece.cum[v + 1] - piece.cum[v]) * t,
                        rank: 0))
                }
            }
            origGid.append(orig)
            pieceGids.append(gids)
        }
        func gid(_ r: VRef) -> Int {
            switch r {
            case .orig(let p, let v): return origGid[p][v]
            case .cut(let c): return cutGid[c]
            }
        }

        // Phase 4: union-find over vertices.
        var parent = Array(0..<verts.count)
        func find(_ x: Int) -> Int {
            var r = x
            while parent[r] != r { r = parent[r] }
            var c = x
            while parent[c] != r { let n = parent[c]; parent[c] = r; c = n }
            return r
        }
        func union(_ a: Int, _ b: Int) {
            let ra = find(a), rb = find(b)
            if ra != rb { parent[max(ra, rb)] = min(ra, rb) }
        }
        // Identical 5-dp coordinates are the same point (~1 m).
        var byQuantised: [Int64: Int] = [:]
        for (pi, piece) in pieces.enumerated() {
            for v in 0..<piece.pts.count {
                let q = Int64((piece.ll[v].lat * 1e5).rounded())
                let w = Int64((piece.ll[v].lon * 1e5).rounded())
                let k = (q << 32) ^ (w & 0xffff_ffff)
                let g = origGid[pi][v]
                if let first = byQuantised[k] { union(first, g) } else { byQuantised[k] = g }
            }
        }
        for (a, c) in unions { union(gid(a), gid(c)) }

        var nodeOfRoot: [Int: Int] = [:]
        var nodeOfGid = [Int](repeating: 0, count: verts.count)
        var anchors: [Int] = []   // node id -> anchoring vertex
        for g in 0..<verts.count {
            let r = find(g)
            if let n = nodeOfRoot[r] {
                nodeOfGid[g] = n
                let cur = anchors[n]
                if verts[g].rank < verts[cur].rank { anchors[n] = g }
            } else {
                nodeOfRoot[r] = anchors.count
                nodeOfGid[g] = anchors.count
                anchors.append(g)
            }
        }
        let nodeCount = anchors.count

        // Elevation at every vertex, from its trail's profile along the chain.
        let elev: [Double?] = verts.map { v in
            let piece = pieces[v.piece]
            let info = trails[piece.trail]
            guard info.elevationMapped, let profile = info.profile else { return nil }
            let along = piece.chainOffset + (piece.flipped ? piece.length - v.arc : v.arc)
            return TrailProfile.elevationFt(profile, at: along / info.chainTotal)
        }

        // Phase 5: fine edges, deduplicated by node pair.
        var fine: [FineEdge] = []
        var fineIndex: [FineKey: Int] = [:]
        func addFine(_ g0: Int, _ g1: Int, bridge: Bool, trailIdx: [Int]) {
            let n0 = nodeOfGid[g0], n1 = nodeOfGid[g1]
            if n0 == n1 { return }
            let swap = n1 < n0
            let key = FineKey(a: swap ? n1 : n0, b: swap ? n0 : n1, bridge: bridge)
            let e0 = swap ? elev[g1] : elev[g0]
            let e1 = swap ? elev[g0] : elev[g1]
            if let i = fineIndex[key] {
                for t in trailIdx where !fine[i].trails.contains(t) { fine[i].trails.append(t) }
                if fine[i].elevA == nil || fine[i].elevB == nil, let e0, let e1 {
                    fine[i].elevA = e0
                    fine[i].elevB = e1
                }
            } else {
                fineIndex[key] = fine.count
                fine.append(FineEdge(a: key.a, b: key.b, bridge: bridge, trails: trailIdx,
                                     elevA: e0, elevB: e1))
            }
        }
        for (pi, gids) in pieceGids.enumerated() {
            for k in 0..<(gids.count - 1) {
                addFine(gids[k], gids[k + 1], bridge: false, trailIdx: [pieces[pi].trail])
            }
        }
        for (a, c) in bridges {
            let ga = gid(a), gc = gid(c)
            var sides = [pieces[verts[ga].piece].trail, pieces[verts[gc].piece].trail]
            sides = Array(Set(sides)).sorted()
            addFine(ga, gc, bridge: true, trailIdx: sides)
        }
        for i in 0..<fine.count { fine[i].trails.sort() }

        return simplify(fine: fine, verts: verts, anchors: anchors, nodeCount: nodeCount)
    }
}
