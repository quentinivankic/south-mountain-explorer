import Foundation

/// Phase 6: collapse chains of fine (single-segment) edges into real edges.
/// A node survives when it is a dead end, a junction (degree 3+), a bridge
/// end, or where the set of trails along the path changes.
extension TrailNetworkBuilder {
    func simplify(
        fine: [FineEdge], verts: [Vertex], anchors: [Int], nodeCount: Int
    ) -> TrailNetwork {
        var incident = [[Int]](repeating: [], count: nodeCount)
        for (i, f) in fine.enumerated() {
            incident[f.a].append(i)
            incident[f.b].append(i)
        }
        var keep = [Bool](repeating: false, count: nodeCount)
        for n in 0..<nodeCount {
            let inc = incident[n]
            if inc.count != 2 { keep[n] = !inc.isEmpty; continue }
            let f0 = fine[inc[0]], f1 = fine[inc[1]]
            keep[n] = f0.bridge || f1.bridge || f0.trails != f1.trails
        }

        struct Chain {
            var nodes: [Int]
            var steps: [(fine: Int, forward: Bool)]
        }
        var visited = [Bool](repeating: false, count: fine.count)
        var chains: [Chain] = []

        func walk(from start: Int, first: Int) {
            var chain = Chain(nodes: [start], steps: [])
            var current = start
            var edge = first
            while true {
                visited[edge] = true
                let f = fine[edge]
                let forward = f.a == current
                let next = forward ? f.b : f.a
                chain.nodes.append(next)
                chain.steps.append((edge, forward))
                current = next
                if keep[current] || current == start { break }
                guard let onward = incident[current].first(where: { $0 != edge }) else { break }
                edge = onward
            }
            chains.append(chain)
        }
        for n in 0..<nodeCount where keep[n] {
            for e in incident[n] where !visited[e] { walk(from: n, first: e) }
        }
        // Closed loops with no junction at all: anchor at the lowest node.
        for e in 0..<fine.count where !visited[e] {
            keep[fine[e].a] = true
            walk(from: fine[e].a, first: e)
        }

        // Compact node ids to those actually used.
        var used = Set<Int>()
        for c in chains { used.insert(c.nodes.first!); used.insert(c.nodes.last!) }
        let usedSorted = used.sorted()
        var newId: [Int: Int] = [:]
        for (i, n) in usedSorted.enumerated() { newId[n] = i }

        var edges: [TrailNetwork.Edge] = []
        var perNode = [[Int]](repeating: [], count: usedSorted.count)
        var trailEdges: [Key: [Int]] = [:]
        for info in trails { trailEdges[info.key] = [] }

        for (id, c) in chains.enumerated() {
            let pts = c.nodes.map { verts[anchors[$0]].p }
            let ll = c.nodes.map { verts[anchors[$0]].c }
            var length = 0.0
            for k in 1..<pts.count { length += TurnCueGeometry.distance(pts[k - 1], pts[k]) }
            var up = 0.0, down = 0.0
            var known = true
            for s in c.steps {
                let f = fine[s.fine]
                guard let ea = f.elevA, let eb = f.elevB else { known = false; continue }
                let delta = s.forward ? eb - ea : ea - eb
                if delta > 0 { up += delta } else { down -= delta }
            }
            let first = fine[c.steps[0].fine]
            let isBridge = first.bridge
            var tset = Set<Int>()
            for s in c.steps { tset.formUnion(fine[s.fine].trails) }
            let keys = tset.sorted().map { trails[$0].key }
            let from = newId[c.nodes.first!]!, to = newId[c.nodes.last!]!
            edges.append(TrailNetwork.Edge(
                id: id, from: from, to: to, kind: isBridge ? .bridge : .trail,
                trailKeys: keys, polyline: pts, coordinates: ll,
                lengthMeters: length,
                gainForwardFt: known ? up : 0, gainBackwardFt: known ? down : 0,
                elevationKnown: known))
            perNode[from].append(id)
            if to != from { perNode[to].append(id) }
            if !isBridge { for k in keys { trailEdges[k, default: []].append(id) } }
        }
        let nodes = usedSorted.enumerated().map { (i, n) in
            TrailNetwork.Node(id: i, point: verts[anchors[n]].p, coordinate: verts[anchors[n]].c,
                              edgeIds: perNode[i].sorted())
        }
        var gaps: [Key: Double] = [:]
        for info in trails { gaps[info.key] = info.maxGap }
        return TrailNetwork(projector: projector, nodes: nodes, edges: edges,
                            trailEdgeIds: trailEdges, pieceGaps: gaps)
    }
}
