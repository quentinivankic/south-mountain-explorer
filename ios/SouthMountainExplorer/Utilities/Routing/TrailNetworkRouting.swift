import Foundation

/// Shortest paths and route timing over a `TrailNetwork` ("Plan a Hike", Task 3).
///
/// Pure and deterministic. Cost is walking time in seconds from `PaceModel`
/// (Tobler). Everything works on the base network or on a view with start / end
/// endpoints spliced in (`TrailNetworkWithEndpoints.network`).

/// Which way an edge is walked: `from` to `to` (forward) or back.
enum RouteDirection: Sendable, Equatable, Hashable {
    case forward
    case backward
    var reversed: RouteDirection { self == .forward ? .backward : .forward }
}

/// One edge walked in one direction.
struct RouteStep: Sendable, Equatable, Hashable {
    let edgeId: Int
    let direction: RouteDirection
    init(edgeId: Int, direction: RouteDirection) {
        self.edgeId = edgeId
        self.direction = direction
    }
}

/// Distance, time and elevation of one edge walked in one direction.
struct EdgeWalk: Sendable, Equatable {
    var meters: Double
    var seconds: Double
    var gainFt: Double
    var lossFt: Double
}

extension TrailNetwork {
    static let metersPerFoot = 0.3048

    /// Cost of walking `edge` in `direction` at `paceFactor` (1.0 = Tobler;
    /// Task 8 supplies "my pace").
    ///
    /// Grade is the edge-average net grade in the walking direction: forward
    /// gain is the climb going `from`->`to`, and the loss is the other
    /// direction's gain. The network stores only per-edge totals (no per-vertex
    /// profile), so per-50 m chunks are not available; the average is used.
    /// `.connector` and `.bridge` edges are flat: nothing verified is known
    /// about their elevation, so grade 0 and no gain or loss.
    static func walk(_ edge: Edge, direction: RouteDirection, paceFactor: Double = 1.0) -> EdgeWalk {
        let length = edge.lengthMeters
        var gain = 0.0, loss = 0.0
        if edge.kind == .trail {
            let fwd = edge.gainForwardFt, back = edge.gainBackwardFt
            (gain, loss) = direction == .forward ? (fwd, back) : (back, fwd)
        }
        var grade = 0.0
        if length > 0 {
            grade = (gain - loss) * metersPerFoot / length
        }
        let seconds = PaceModel.seconds(length: length, grade: grade, factor: paceFactor)
        return EdgeWalk(meters: length, seconds: seconds, gainFt: gain, lossFt: loss)
    }

    func walk(edgeId: Int, direction: RouteDirection, paceFactor: Double = 1.0) -> EdgeWalk {
        Self.walk(edges[edgeId], direction: direction, paceFactor: paceFactor)
    }
}

/// Per-edge, per-direction costs precomputed once for a network and pace.
struct RouteCosts: Sendable {
    let paceFactor: Double
    private let forward: [EdgeWalk]
    private let backward: [EdgeWalk]

    init(network: TrailNetwork, paceFactor: Double = 1.0) {
        self.paceFactor = paceFactor
        forward = network.edges.map { TrailNetwork.walk($0, direction: .forward, paceFactor: paceFactor) }
        backward = network.edges.map { TrailNetwork.walk($0, direction: .backward, paceFactor: paceFactor) }
    }

    func walk(_ edgeId: Int, _ direction: RouteDirection) -> EdgeWalk {
        direction == .forward ? forward[edgeId] : backward[edgeId]
    }
}

/// Result of one Dijkstra run: best time, plus distance / gain / loss along
/// that best path, and the predecessor tree.
struct ShortestPaths: Sendable {
    let source: Int
    /// Indexed by node id. `.infinity` = not reached.
    let seconds: [Double]
    let meters: [Double]
    let gainFt: [Double]
    let lossFt: [Double]
    /// Step taken to arrive at each node (nil for the source and unreached).
    let arrival: [RouteStep?]
    /// Node the arrival step came from (-1 for the source and unreached).
    let predecessor: [Int]

    func isReachable(_ node: Int) -> Bool {
        node >= 0 && node < seconds.count && seconds[node].isFinite
    }

    /// Ordered steps from the source to `node`; `[]` for the source itself,
    /// nil when unreachable or out of range.
    func path(to node: Int) -> [RouteStep]? {
        guard isReachable(node) else { return nil }
        var steps: [RouteStep] = []
        var cur = node
        while cur != source {
            guard let s = arrival[cur] else { return nil }
            steps.append(s)
            cur = predecessor[cur]
        }
        return steps.reversed()
    }

    /// Node ids (ascending) reached within `budgetSeconds`, source included.
    func nodes(within budgetSeconds: Double) -> [Int] {
        seconds.indices.filter { seconds[$0] <= budgetSeconds }
    }
}

/// Distances between a small set of key nodes (repeated Dijkstra).
struct KeyNodePaths: Sendable {
    let keyNodes: [Int]
    private let runs: [Int: ShortestPaths]

    fileprivate init(keyNodes: [Int], runs: [Int: ShortestPaths]) {
        self.keyNodes = keyNodes
        self.runs = runs
    }

    func paths(from node: Int) -> ShortestPaths? { runs[node] }
    func seconds(from a: Int, to b: Int) -> Double? {
        guard let r = runs[a], r.isReachable(b) else { return nil }
        return r.seconds[b]
    }
    func meters(from a: Int, to b: Int) -> Double? {
        guard let r = runs[a], r.isReachable(b) else { return nil }
        return r.meters[b]
    }
    func gainFt(from a: Int, to b: Int) -> Double? {
        guard let r = runs[a], r.isReachable(b) else { return nil }
        return r.gainFt[b]
    }
    func path(from a: Int, to b: Int) -> [RouteStep]? { runs[a]?.path(to: b) }
}

extension TrailNetwork {
    /// Dijkstra (binary heap) from `source`, cost = seconds. Ties break on the
    /// lower node id, so results are deterministic. With `maxSeconds`, nodes
    /// costing more are left unreached (used by reachability).
    func shortestPaths(from source: Int, costs: RouteCosts, maxSeconds: Double = .infinity) -> ShortestPaths? {
        let n = nodes.count
        guard source >= 0, source < n else { return nil }
        var secs = [Double](repeating: .infinity, count: n)
        var meters = [Double](repeating: .infinity, count: n)
        var gain = [Double](repeating: .infinity, count: n)
        var loss = [Double](repeating: .infinity, count: n)
        var arrival = [RouteStep?](repeating: nil, count: n)
        var pred = [Int](repeating: -1, count: n)
        var done = [Bool](repeating: false, count: n)
        secs[source] = 0; meters[source] = 0; gain[source] = 0; loss[source] = 0

        var heap = MinHeap()
        heap.push(cost: 0, node: source)
        while let (cost, u) = heap.pop() {
            if done[u] || cost > secs[u] { continue }
            done[u] = true
            for eid in nodes[u].edgeIds {
                let e = edges[eid]
                if e.from == e.to { continue }   // a loop never shortens a path
                let dir: RouteDirection = e.from == u ? .forward : .backward
                let v = dir == .forward ? e.to : e.from
                if done[v] { continue }
                let w = costs.walk(eid, dir)
                let c = secs[u] + w.seconds
                if c > maxSeconds { continue }
                // Strictly better, or equal with a lower predecessor id.
                if c < secs[v] || (c == secs[v] && u < pred[v]) {
                    let improved = c < secs[v]
                    secs[v] = c
                    meters[v] = meters[u] + w.meters
                    gain[v] = gain[u] + w.gainFt
                    loss[v] = loss[u] + w.lossFt
                    arrival[v] = RouteStep(edgeId: eid, direction: dir)
                    pred[v] = u
                    if improved { heap.push(cost: c, node: v) }
                }
            }
        }
        return ShortestPaths(source: source, seconds: secs, meters: meters, gainFt: gain,
                             lossFt: loss, arrival: arrival, predecessor: pred)
    }

    func shortestPaths(from source: Int, paceFactor: Double = 1.0) -> ShortestPaths? {
        shortestPaths(from: source, costs: RouteCosts(network: self, paceFactor: paceFactor))
    }

    /// Repeated Dijkstra from each of a SMALL set of key nodes (duplicates and
    /// invalid ids ignored). Not meant for large sets.
    func keyNodePaths(_ keyNodes: [Int], costs: RouteCosts) -> KeyNodePaths {
        var runs: [Int: ShortestPaths] = [:]
        var keys: [Int] = []
        for k in keyNodes where runs[k] == nil {
            if let r = shortestPaths(from: k, costs: costs) {
                runs[k] = r
                keys.append(k)
            }
        }
        return KeyNodePaths(keyNodes: keys, runs: runs)
    }

    /// Nodes (ascending) reachable from `source` within `budgetSeconds`.
    func reachableNodes(from source: Int, withinSeconds budgetSeconds: Double,
                        paceFactor: Double = 1.0) -> [Int] {
        let costs = RouteCosts(network: self, paceFactor: paceFactor)
        return shortestPaths(from: source, costs: costs, maxSeconds: budgetSeconds)?
            .nodes(within: budgetSeconds) ?? []
    }
}

/// Binary min-heap on (cost, node id).
private struct MinHeap {
    private var items: [(cost: Double, node: Int)] = []

    private func less(_ a: (cost: Double, node: Int), _ b: (cost: Double, node: Int)) -> Bool {
        a.cost < b.cost || (a.cost == b.cost && a.node < b.node)
    }

    mutating func push(cost: Double, node: Int) {
        items.append((cost, node))
        var i = items.count - 1
        while i > 0 {
            let p = (i - 1) / 2
            if less(items[i], items[p]) { items.swapAt(i, p); i = p } else { break }
        }
    }

    mutating func pop() -> (Double, Int)? {
        guard !items.isEmpty else { return nil }
        let top = items[0]
        let last = items.removeLast()
        if !items.isEmpty {
            items[0] = last
            var i = 0
            while true {
                let l = 2 * i + 1, r = l + 1
                var m = i
                if l < items.count, less(items[l], items[m]) { m = l }
                if r < items.count, less(items[r], items[m]) { m = r }
                if m == i { break }
                items.swapAt(i, m); i = m
            }
        }
        return (top.cost, top.node)
    }
}
