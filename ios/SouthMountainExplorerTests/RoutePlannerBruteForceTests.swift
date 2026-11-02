import Foundation
import Testing
@testable import SouthMountainExplorer

/// The planner against exhaustive solvers on random small graphs
/// ("Plan a Hike", Task 5). Scenes are 3 x 3 lattices with irregular spacing,
/// seeded so every run sees the same graphs.
struct RoutePlannerBruteForceTests {
    typealias K = PlannerKit
    typealias Obj = PlannerOracles.Objective

    struct Scene {
        var trails: [Trail]
        var completed: Set<String>
        var start: (Double, Double)
        var end: (Double, Double)?
        var limit: Double
        var maxGain: Double?
        var shape: RoutePlanRequest.Shape
    }

    private static func lattice(_ r: inout PlannerLCG) -> (xs: [Double], ys: [Double]) {
        let xs = [0, 180 + Double(r.int(120)), 420 + Double(r.int(200))]
        let ys = [0, 180 + Double(r.int(120)), 420 + Double(r.int(200))]
        return (xs, ys)
    }

    /// All 12 lattice edges as (nodeA, nodeB) with node = iy * 3 + ix.
    private static let latticeEdges: [(Int, Int)] = {
        var out: [(Int, Int)] = []
        for iy in 0..<3 { for ix in 0..<3 {
            if ix < 2 { out.append((iy * 3 + ix, iy * 3 + ix + 1)) }
            if iy < 2 { out.append((iy * 3 + ix, (iy + 1) * 3 + ix)) }
        } }
        return out
    }()

    private static func shuffled<T>(_ a: [T], _ r: inout PlannerLCG) -> [T] {
        var a = a
        for i in stride(from: a.count - 1, to: 0, by: -1) { a.swapAt(i, r.int(i + 1)) }
        return a
    }

    private static func finish(_ r: inout PlannerLCG, touched: [Int], xs: [Double], ys: [Double],
                               trails: [Trail], completed: Set<String>, gainChance: Double) -> Scene {
        func pt(_ n: Int) -> (Double, Double) { (xs[n % 3], ys[n / 3]) }
        let startN = touched[r.int(touched.count)]
        var end: (Double, Double)?
        var shape = RoutePlanRequest.Shape.loop
        if r.chance(0.4), touched.count > 1 {
            let others = touched.filter { $0 != startN }
            end = pt(others[r.int(others.count)])
            shape = .oneWay
        }
        return Scene(trails: trails, completed: completed, start: pt(startN), end: end,
                     limit: 400 + Double(r.int(2400)),
                     maxGain: r.chance(gainChance) ? 20 + Double(r.int(160)) : nil, shape: shape)
    }

    /// Family A: every trail is one lattice edge (so one network edge), with
    /// elevation. Up to 6 uncompleted trails.
    static func sceneA(seed: UInt64) -> Scene {
        var r = PlannerLCG(seed: seed)
        let (xs, ys) = lattice(&r)
        let count = 5 + r.int(4)
        let picked = Array(shuffled(latticeEdges, &r).prefix(count))
        var trails: [Trail] = []
        var completed = Set<String>()
        var uncompleted = 0
        for (i, e) in picked.enumerated() {
            let (a, b) = r.chance(0.5) ? e : (e.1, e.0)
            let id = "t\(i)"
            let top = [0, 0, 40, 120][r.int(4)]
            trails.append(K.trail(id, [(xs[a % 3], ys[a / 3]), (xs[b % 3], ys[b / 3])], profile: K.ramp(top)))
            if uncompleted >= 6 || r.chance(0.25) { completed.insert(id) } else { uncompleted += 1 }
        }
        let touched = Array(Set(picked.flatMap { [$0.0, $0.1] })).sorted()
        return finish(&r, touched: touched, xs: xs, ys: ys, trails: trails, completed: completed, gainChance: 0.35)
    }

    /// Family B: trails of 1 to 3 connected lattice edges - paths, tees,
    /// stars, loops. No elevation (flat).
    static func sceneB(seed: UInt64) -> Scene {
        var r = PlannerLCG(seed: seed)
        let (xs, ys) = lattice(&r)
        var free = shuffled(latticeEdges, &r).prefix(6 + r.int(4)).map { $0 }
        var trails: [Trail] = []
        var completed = Set<String>()
        var touched = Set<Int>()
        var i = 0
        while let first = free.first {
            free.removeFirst()
            var group = [first]
            var nodes: Set<Int> = [first.0, first.1]
            for _ in 0..<r.int(3) {
                guard let k = free.firstIndex(where: { nodes.contains($0.0) || nodes.contains($0.1) }) else { break }
                group.append(free.remove(at: k))
                nodes.insert(group.last!.0); nodes.insert(group.last!.1)
            }
            let id = "t\(i)"; i += 1
            let pieces = group.map { [(xs[$0.0 % 3], ys[$0.0 / 3]), (xs[$0.1 % 3], ys[$0.1 / 3])] }
            trails.append(K.trail(id, pieces))
            touched.formUnion(nodes)
            if r.chance(0.3) { completed.insert(id) }
        }
        return finish(&r, touched: touched.sorted(), xs: xs, ys: ys, trails: trails, completed: completed,
                      gainChance: 0)
    }

    static func objective(_ plan: RoutePlan?) -> Obj? {
        plan.map { Obj(count: $0.finished.count, newMeters: $0.metrics.newMeters, seconds: $0.metrics.seconds) }
    }

    private static func run(_ s: Scene) throws -> PlannerKit.Outcome {
        try K.run(s.trails, completed: s.completed, start: s.start, end: s.end, limit: s.limit,
                  maxGain: s.maxGain, shape: s.shape)
    }

    // MARK: - Tests

    /// Family A against the subsets-and-orders oracle (full objective) and the
    /// all-walks oracle (finished count, when there is no gain limit).
    @Test func agreesWithBruteForceOnSingleEdgeTrails() throws {
        var total = 0, same = 0, worseCount = 0, worseNew = 0, worseTime = 0, better = 0, mismatchNil = 0
        var walkTotal = 0, walkSame = 0
        var missA: [String] = []
        var invalid: [String] = []
        for seed in 1...400 {
            let scene = Self.sceneA(seed: UInt64(seed))
            guard let o = try? Self.run(scene) else { continue }
            if o.eligibility.candidates.isEmpty { continue }
            let oracle = PlannerOracles.subsetsAndOrders(o)
            let got = Self.objective(o.plan)
            total += 1
            if o.plan != nil { for p in K.validate(o) { invalid.append("seed \(seed): \(p)") } }
            switch (got, oracle) {
            case (nil, nil): same += 1
            case (nil, _), (_, nil): mismatchNil += 1
            case (let g?, let b?):
                if g.isSame(as: b) { same += 1 }
                else if g.isBetter(than: b) { better += 1 }
                else if g.count < b.count { worseCount += 1 }
                else if g.newMeters < b.newMeters - 0.5 { worseNew += 1; missA.append("\(seed)") }
                else { worseTime += 1 }
            }
            if scene.maxGain == nil, let w = PlannerOracles.bestWalk(o) {
                walkTotal += 1
                if (got?.count ?? 0) == w.count { walkSame += 1 }
            }
        }
        print("T5AGREE A subsets/orders: scenes=\(total) exact=\(same) worseCount=\(worseCount) worseNewMiles=\(worseNew) worseTime=\(worseTime) plannerBetter=\(better) nilMismatch=\(mismatchNil)")
        print("T5AGREE A misses (new miles): \(missA)")
        print("T5AGREE A all-walks count: scenes=\(walkTotal) sameCount=\(walkSame)")
        #expect(invalid == [])
        #expect(better == 0, "planner beat the exhaustive solver: the oracle is wrong")
        #expect(mismatchNil == 0)
        #expect(total >= 100)
        #expect(Double(same) / Double(total) >= 0.97)
        #expect(worseCount <= total / 50)
        #expect(Double(walkSame) / Double(max(walkTotal, 1)) >= 0.98)
    }

    /// Family B (branches, loops, stars) against the all-walks oracle.
    @Test func agreesWithAllWalksOnBranchedAndLoopedTrails() throws {
        var total = 0, same = 0, fewer = 0, slower = 0
        var missB: [String] = [], slowB: [String] = []
        var invalid: [String] = []
        for seed in 1...400 {
            let scene = Self.sceneB(seed: UInt64(seed) &+ 1000)
            guard let o = try? Self.run(scene), !o.eligibility.candidates.isEmpty else { continue }
            guard o.net.edges.count <= 16 else { continue }
            let oracle = PlannerOracles.bestWalk(o)
            let got = o.plan
            total += 1
            if got != nil { for p in K.validate(o) { invalid.append("seed \(seed): \(p)") } }
            let gc = got?.finished.count ?? 0
            let bc = oracle?.count ?? 0
            if gc == bc { same += 1 } else if gc < bc { fewer += 1; missB.append("\(seed)") }
            if gc > bc { invalid.append("seed \(seed): planner finished \(gc) > exhaustive \(bc)") }
            if let g = got, let b = oracle, gc == bc, g.metrics.seconds < b.seconds - 1e-3 {
                invalid.append("seed \(seed): planner time below exhaustive minimum")
            }
            if let g = got, let b = oracle, gc == bc, g.metrics.seconds > b.seconds + 0.5 { slower += 1; slowB.append("\(seed)(\(Int(g.metrics.seconds))>\(Int(b.seconds)))") }
        }
        print("T5AGREE B all-walks count: scenes=\(total) sameCount=\(same) fewer=\(fewer) sameCountButSlower=\(slower) (informational: the planner prefers more new ground over less time)")
        print("T5AGREE B misses: \(missB) slower: \(slowB)")
        #expect(invalid == [])
        #expect(total >= 100)
        #expect(Double(same) / Double(total) >= 0.97)
    }
}
