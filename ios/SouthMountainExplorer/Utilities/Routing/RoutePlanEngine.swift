import Foundation

/// A walk that covers every required edge of one trail, entering and leaving
/// at boundary nodes.
struct PlanCoverWalk {
    let steps: [RouteStep]
    let seconds: Double
    let gainFt: Double
}

/// One planner job: a single candidate trail, or a "cluster" of two adjacent
/// candidate trails covered together (so a spur trail can be walked from the
/// middle of the trail it leaves, instead of retracing).
struct PlanJob {
    let index: Int
    let key: TrailNetwork.TrailKey
    let name: String
    /// Indices of the single-trail jobs this job finishes (itself for a single).
    let members: [Int]
    /// Which member owns each required edge (first member wins on shared edges).
    let owner: [Int: TrailNetwork.TrailKey]
    var isCluster: Bool { members.count > 1 }
    /// Required edges, ascending.
    let edgeIds: [Int]
    let requiredMeters: Double
    /// Boundary nodes (network node ids), in priority order.
    let nodes: [Int]
    /// Key index (distance-table row) of each boundary node.
    var keyIndex: [Int] = []
    /// `cover[entry][exit]`: indices into `nodes`. nil when no walk was built.
    let cover: [[PlanCoverWalk?]]
    /// Bonus added to the greedy score: tiny, always < 1.
    let bonus: Double
    /// `tailSecs[entry][k]`: least cover-then-walk-to-key-node-`k` seconds from
    /// entry `entry` (k == key-node count means "no next stop"). Gain-free
    /// shortcut for the greedy scan; built with the distance tables.
    var tailSecs: [[Double]] = []
    /// Seconds to walk start, job, finish alone (inf if it cannot).
    var soloSecs = Double.infinity
}

/// State shared by the planner's phases. A class so the Dijkstra cache and the
/// scratch stamp array are reused; it never escapes `RoutePlanner.plan`.
final class RoutePlanEngine {
    let request: RoutePlanRequest
    let net: TrailNetwork
    let costs: RouteCosts
    let startNode: Int
    /// nil only for a one-way plan with no end point (free end).
    let finishNode: Int?
    let limit: Double
    let maxGain: Double?
    /// true for trail edges with at least one uncompleted trail.
    let isNewEdge: [Bool]
    let completedKeys: Set<TrailNetwork.TrailKey>

    var jobs: [PlanJob] = []
    /// Jobs `0..<singleCount` are single trails; the rest are clusters.
    var singleCount = 0
    private var trees: [Int: ShortestPaths] = [:]

    // Distance tables between key nodes (start, finish, every boundary node).
    var keyNodes: [Int] = []
    var keyOf: [Int: Int] = [:]
    var secsTable: [Double] = []
    var gainTable: [Double] = []
    var startK = 0
    var finishK: Int?

    // Scratch for exact evaluation.
    var stamp: [Int]
    var gen = 0
    private var legCache: [Int: [RouteStep]] = [:]
    var evaluations = 0
    /// Sequence solves attempted (DP calls); caps the local search deterministically.
    var work = 0

    static let eps = 1e-6

    init?(_ request: RoutePlanRequest) {
        self.request = request
        let view = request.network
        net = view.network
        costs = RouteCosts(network: net, paceFactor: request.paceFactor)
        startNode = view.start.nodeId
        switch request.shape {
        case .loop: finishNode = view.start.nodeId
        case .oneWay: finishNode = view.end?.nodeId
        }
        limit = request.timeLimitSeconds
        maxGain = request.maxGainFt
        guard startNode >= 0, startNode < net.nodes.count else { return nil }
        if let f = finishNode, f < 0 || f >= net.nodes.count { return nil }

        var done = Set<TrailNetwork.TrailKey>()
        for k in net.trailKeys where request.completion.isComplete(k) { done.insert(k) }
        completedKeys = done
        isNewEdge = net.edges.map { e in
            e.kind == .trail && e.trailKeys.contains { !done.contains($0) }
        }
        stamp = [Int](repeating: 0, count: net.edges.count)
    }

    // MARK: - Distances

    /// Truncated Dijkstra, cached. Nodes beyond the time limit are unreached.
    func tree(_ node: Int) -> ShortestPaths? {
        if let t = trees[node] { return t }
        guard let t = net.shortestPaths(from: node, costs: costs, maxSeconds: limit) else { return nil }
        trees[node] = t
        return t
    }

    /// Builds the distance tables once the boundary nodes are known.
    func buildTables() {
        var set = Set<Int>([startNode])
        if let f = finishNode { set.insert(f) }
        for j in jobs { for n in j.nodes { set.insert(n) } }
        keyNodes = set.sorted()
        keyOf = [:]
        for (i, n) in keyNodes.enumerated() { keyOf[n] = i }
        let k = keyNodes.count
        secsTable = [Double](repeating: .infinity, count: k * k)
        gainTable = [Double](repeating: .infinity, count: k * k)
        for (a, na) in keyNodes.enumerated() {
            guard let t = tree(na) else { continue }
            for (b, nb) in keyNodes.enumerated() where t.isReachable(nb) {
                secsTable[a * k + b] = t.seconds[nb]
                gainTable[a * k + b] = t.gainFt[nb]
            }
        }
        startK = keyOf[startNode]!
        finishK = finishNode.flatMap { keyOf[$0] }
        for i in jobs.indices {
            jobs[i].keyIndex = jobs[i].nodes.map { keyOf[$0]! }
            let job = jobs[i]
            var tails = [[Double]](repeating: [Double](repeating: .infinity, count: k + 1), count: job.nodes.count)
            for s in 0..<job.nodes.count {
                for t in 0..<job.nodes.count {
                    guard let cw = job.cover[s][t] else { continue }
                    tails[s][k] = min(tails[s][k], cw.seconds)
                    for next in 0..<k {
                        let c = conn(job.keyIndex[t], next)
                        if c.isFinite { tails[s][next] = min(tails[s][next], cw.seconds + c) }
                    }
                }
            }
            jobs[i].tailSecs = tails
            // Solo time: start -> job -> finish.
            var solo = Double.infinity
            for s in 0..<job.nodes.count {
                let a = conn(startK, job.keyIndex[s])
                let rest = finishK.map { tails[s][$0] } ?? tails[s][k]
                solo = min(solo, a + rest)
            }
            jobs[i].soloSecs = solo
        }
    }

    @inline(__always) func conn(_ a: Int, _ b: Int) -> Double { secsTable[a * keyNodes.count + b] }
    @inline(__always) func connGain(_ a: Int, _ b: Int) -> Double { gainTable[a * keyNodes.count + b] }

    /// Time-shortest connecting steps between two key nodes (cached).
    func leg(_ a: Int, _ b: Int) -> [RouteStep] {
        let id = a * keyNodes.count + b
        if let c = legCache[id] { return c }
        let steps = tree(keyNodes[a])?.path(to: keyNodes[b]) ?? []
        legCache[id] = steps
        return steps
    }

    // MARK: - Helpers on steps

    func startOf(_ s: RouteStep) -> Int {
        let e = net.edges[s.edgeId]
        return s.direction == .forward ? e.from : e.to
    }

    func endOf(_ s: RouteStep) -> Int {
        let e = net.edges[s.edgeId]
        return s.direction == .forward ? e.to : e.from
    }

    func makeCover(_ steps: [RouteStep]) -> PlanCoverWalk {
        var secs = 0.0, gain = 0.0
        for s in steps {
            let w = costs.walk(s.edgeId, s.direction)
            secs += w.seconds
            gain += w.gainFt
        }
        return PlanCoverWalk(steps: steps, seconds: secs, gainFt: gain)
    }
}
