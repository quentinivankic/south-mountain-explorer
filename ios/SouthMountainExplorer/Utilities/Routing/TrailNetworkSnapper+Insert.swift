import Foundation

/// Splicing snapped points into a network view. The base network is never
/// mutated: every array here is a copy.
extension TrailNetworkSnapper {
    /// Returns a view of the base network with `start` (and, for a one-way
    /// hike, `end`) added. Both are snapped against the BASE network first, so
    /// two points on the same edge split it together. Throws
    /// `TrailNetworkStartError` if either point cannot be joined.
    ///
    /// Splitting: the snapped edge becomes two (or more) edges that keep its
    /// trail keys and kind. The first piece keeps the original edge id; new
    /// pieces, nodes and connectors take the next free ids, in a fixed order,
    /// so the result is deterministic and `edges[i].id == i` still holds.
    /// Gain is apportioned by arc length (the network stores per-edge totals,
    /// not a per-vertex profile), so the pieces always sum to the original.
    func insert(start: StartPoint, end: StartPoint? = nil) throws -> TrailNetworkWithEndpoints {
        let startSnap = try snap(start)
        let endSnap = try end.map { try snapEnd($0) }
        var placements = [Placement(point: start, snap: startSnap)]
        if let end, let endSnap { placements.append(Placement(point: end, snap: endSnap)) }
        let result = splice(placements)
        return TrailNetworkWithEndpoints(
            network: result.network, start: result.endpoints[0],
            end: result.endpoints.count > 1 ? result.endpoints[1] : nil)
    }

    private struct Placement {
        let point: StartPoint
        let snap: TrailNetworkSnap
    }

    private enum Resolved {
        case node(Int)
        case cut(Int)
    }

    private struct Cut {
        let arc: Double
        let point: LocalPt
        let coordinate: TrailNetwork.Coordinate
        /// Index of the polyline segment holding the cut.
        let segment: Int
    }

    private func splice(_ placements: [Placement]) -> (network: TrailNetwork, endpoints: [TrailNetworkEndpoint]) {
        let base = network
        let merge = options.nodeMergeMeters

        var edges = base.edges
        var nodePoint = base.nodes.map(\.point)
        var nodeCoord = base.nodes.map(\.coordinate)
        var nodeEdges = base.nodes.map(\.edgeIds)
        var trailEdgeIds = base.trailEdgeIds

        // Group placements by edge, ascending edge id for deterministic ids.
        var byEdge: [Int: [Int]] = [:]
        for (i, p) in placements.enumerated() { byEdge[p.snap.edgeId, default: []].append(i) }

        var trailNode = [Int](repeating: -1, count: placements.count)
        for edgeId in byEdge.keys.sorted() {
            let original = base.edges[edgeId]
            let cum = cumulative(ofEdge: edgeId)
            let length = cum.last ?? original.lengthMeters
            let order = byEdge[edgeId]!.sorted {
                (placements[$0].snap.arcMeters, $0) < (placements[$1].snap.arcMeters, $1)
            }

            // Resolve each placement to an end node or an interior cut.
            var cuts: [Cut] = []
            var resolved: [(index: Int, target: Resolved)] = []
            for i in order {
                let arc = placements[i].snap.arcMeters
                if min(arc, length - arc) <= merge {
                    resolved.append((i, .node(arc <= length - arc ? original.from : original.to)))
                } else if let last = cuts.last, arc - last.arc <= merge {
                    resolved.append((i, .cut(cuts.count - 1)))
                } else {
                    cuts.append(makeCut(original, cum, arc))
                    resolved.append((i, .cut(cuts.count - 1)))
                }
            }

            var cutNode: [Int] = []
            if !cuts.isEmpty {
                // Pieces: boundaries 0, cut arcs..., length.
                let bounds = [0.0] + cuts.map(\.arc) + [length]
                // New pieces take consecutive ids from edges.count.
                let pieceIds = [edgeId] + (0..<cuts.count).map { edges.count + $0 }
                for c in cuts {
                    cutNode.append(nodePoint.count)
                    nodePoint.append(c.point)
                    nodeCoord.append(c.coordinate)
                    nodeEdges.append([])
                }
                let nodeChain = [original.from] + cutNode + [original.to]
                var newEdges: [TrailNetwork.Edge] = []
                for k in 0...cuts.count {
                    let a = bounds[k], b = bounds[k + 1]
                    let frac = length > 0 ? (b - a) / length : 0
                    let (pts, lls) = slicePolyline(original, cum, k == 0 ? nil : cuts[k - 1],
                                                   k == cuts.count ? nil : cuts[k])
                    newEdges.append(TrailNetwork.Edge(
                        id: pieceIds[k], from: nodeChain[k], to: nodeChain[k + 1],
                        kind: original.kind, trailKeys: original.trailKeys,
                        polyline: pts, coordinates: lls, lengthMeters: b - a,
                        gainForwardFt: original.gainForwardFt * frac,
                        gainBackwardFt: original.gainBackwardFt * frac,
                        elevationKnown: original.elevationKnown))
                }
                edges[edgeId] = newEdges[0]
                for e in newEdges.dropFirst() { edges.append(e) }
                for k in 0..<cutNode.count {
                    nodeEdges[cutNode[k]] = [pieceIds[k], pieceIds[k + 1]]
                }
                // The far end node now touches the last piece, not the original.
                let lastId = pieceIds[cuts.count]
                if original.from == original.to {
                    nodeEdges[original.to].append(lastId)
                } else {
                    nodeEdges[original.to] = nodeEdges[original.to].map { $0 == edgeId ? lastId : $0 }
                }
                for key in original.trailKeys where original.kind == .trail {
                    trailEdgeIds[key, default: []] += pieceIds.dropFirst()
                }
            }
            for r in resolved {
                switch r.target {
                case .node(let n): trailNode[r.index] = n
                case .cut(let c): trailNode[r.index] = cutNode[c]
                }
            }
        }

        // Connectors for points that are off the trail.
        var endpoints: [TrailNetworkEndpoint] = []
        for (i, p) in placements.enumerated() {
            let join = trailNode[i]
            guard p.snap.distanceMeters > options.onTrailToleranceMeters else {
                endpoints.append(TrailNetworkEndpoint(
                    point: p.point, snap: p.snap, nodeId: join, trailNodeId: join,
                    connectorEdgeId: nil, connectorMeters: 0))
                continue
            }
            let startPt = base.projector.local(lat: p.point.lat, lon: p.point.lon)
            let startNode = nodePoint.count
            nodePoint.append(startPt)
            nodeCoord.append(p.point.coordinate)
            let connectorId = edges.count
            nodeEdges.append([connectorId])
            nodeEdges[join].append(connectorId)
            let length = TurnCueGeometry.distance(startPt, nodePoint[join])
            edges.append(TrailNetwork.Edge(
                id: connectorId, from: startNode, to: join, kind: .connector, trailKeys: [],
                polyline: [startPt, nodePoint[join]],
                coordinates: [p.point.coordinate, nodeCoord[join]],
                lengthMeters: length, gainForwardFt: 0, gainBackwardFt: 0, elevationKnown: false))
            endpoints.append(TrailNetworkEndpoint(
                point: p.point, snap: p.snap, nodeId: startNode, trailNodeId: join,
                connectorEdgeId: connectorId, connectorMeters: length))
        }

        for k in trailEdgeIds.keys { trailEdgeIds[k]!.sort() }
        let nodes = (0..<nodePoint.count).map {
            TrailNetwork.Node(id: $0, point: nodePoint[$0], coordinate: nodeCoord[$0],
                              edgeIds: nodeEdges[$0].sorted())
        }
        let view = TrailNetwork(projector: base.projector, nodes: nodes, edges: edges,
                                trailEdgeIds: trailEdgeIds, pieceGaps: base.pieceGaps)
        return (view, endpoints)
    }

    /// Interior cut at `arc`, interpolated on the polyline (lat/lon linearly,
    /// like Task 1's cut points).
    private func makeCut(_ e: TrailNetwork.Edge, _ cum: [Double], _ arc: Double) -> Cut {
        var s = 0
        while s < e.polyline.count - 2, cum[s + 1] <= arc { s += 1 }
        let span = cum[s + 1] - cum[s]
        let t = span > 0 ? min(1, max(0, (arc - cum[s]) / span)) : 0
        let a = e.polyline[s], b = e.polyline[s + 1]
        let ca = e.coordinates[s], cb = e.coordinates[s + 1]
        return Cut(
            arc: arc,
            point: LocalPt(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t),
            coordinate: TrailNetwork.Coordinate(
                lat: ca.lat + (cb.lat - ca.lat) * t, lon: ca.lon + (cb.lon - ca.lon) * t),
            segment: s)
    }

    /// Vertices of the edge between two cuts (nil = edge start / end).
    private func slicePolyline(
        _ e: TrailNetwork.Edge, _ cum: [Double], _ from: Cut?, _ to: Cut?
    ) -> ([LocalPt], [TrailNetwork.Coordinate]) {
        var pts: [LocalPt] = []
        var lls: [TrailNetwork.Coordinate] = []
        if let from { pts.append(from.point); lls.append(from.coordinate) }
        let lo = from.map { $0.segment + 1 } ?? 0
        let hi = to.map { $0.segment } ?? (e.polyline.count - 1)
        if lo <= hi {
            for v in lo...hi {
                // Skip a vertex that coincides with a cut point.
                if let from, v == lo, cum[v] <= from.arc { continue }
                if let to, v == hi, cum[v] >= to.arc { continue }
                pts.append(e.polyline[v]); lls.append(e.coordinates[v])
            }
        }
        if let to { pts.append(to.point); lls.append(to.coordinate) }
        return (pts, lls)
    }
}
