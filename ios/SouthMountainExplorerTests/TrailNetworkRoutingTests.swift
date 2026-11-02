import Foundation
import Testing
@testable import SouthMountainExplorer

/// Shortest paths, edge timing and route metrics for "Plan a Hike" (Task 3).
/// Synthetic scenes are laid out in local metres (x east, y north).
struct TrailNetworkRoutingTests {
    typealias Key = TrailNetwork.TrailKey

    // MARK: - Helpers
    private static let lat0 = 33.34
    private static let lon0 = -112.0
    private static func ll(_ x: Double, _ y: Double) -> [Double] {
        let mpd = TurnCueProjector.metersPerDegree
        return [lat0 + y / mpd, lon0 + x / (cos(lat0 * .pi / 180) * mpd)]
    }
    private static func line(_ pts: [(Double, Double)], step: Double = 20) -> [[Double]] {
        var out: [[Double]] = []
        for (a, b) in zip(pts, pts.dropFirst()) {
            let n = max(1, Int((hypot(b.0 - a.0, b.1 - a.1) / step).rounded()))
            for i in 0..<n {
                let t = Double(i) / Double(n)
                out.append(ll(a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t))
            }
        }
        out.append(ll(pts.last!.0, pts.last!.1))
        return out
    }
    private static func trail(_ id: String, _ pts: [(Double, Double)], profile: [Int]? = nil) -> Trail {
        Trail(id: id, name: id, distanceMi: 0, difficulty: .easy,
              segments: [line(pts)], gainFt: nil, profileFt: profile)
    }
    private static func network(_ trails: [Trail]) -> TrailNetwork {
        TrailNetwork(trails: trails.map { .init(areaId: "a", trail: $0) })
    }
    private static func key(_ id: String) -> Key { Key(areaId: "a", trailId: id) }
    /// Node nearest to a local point.
    private static func node(_ n: TrailNetwork, _ x: Double, _ y: Double) -> Int {
        let c = ll(x, y)
        let p = n.projector.local(lat: c[0], lon: c[1])
        return n.nodes.min { TurnCueGeometry.distance($0.point, p) < TurnCueGeometry.distance($1.point, p) }!.id
    }
    private static func ramp(_ top: Int, samples: Int = 21) -> [Int] {
        (0..<samples).map { $0 * top / (samples - 1) }
    }
    private static func flat(samples: Int = 21) -> [Int] { [Int](repeating: 0, count: samples) }

    /// Steep 400 m direct trail (0,0)->(400,0) climbing 200 ft, versus a flat
    /// 600 m detour (0,0)->(0,100)->(400,100)->(400,0).
    private static func steepVersusFlat() -> TrailNetwork {
        network([
            trail("steep", [(0, 0), (400, 0)], profile: ramp(200)),
            trail("detour", [(0, 0), (0, 100), (400, 100), (400, 0)], profile: flat()),
        ])
    }

    private func sameSeconds(_ a: Double, _ b: Double) -> Bool { abs(a - b) < 1e-6 }

    // MARK: - Edge cost
    @Test func flatEdgeUsesToblerFlatSpeed() throws {
        let n = Self.network([Self.trail("A", [(0, 0), (500, 0)], profile: Self.flat())])
        let e = n.edges[0]
        let w = n.walk(edgeId: 0, direction: .forward)
        #expect(sameSeconds(w.seconds, e.lengthMeters / PaceModel.tobler(grade: 0)))
        let kmh = w.meters / w.seconds * 3.6
        #expect(kmh > 4.9 && kmh < 5.1)
    }

    @Test func uphillAndDownhillAreAsymmetric() throws {
        let n = Self.network([Self.trail("A", [(0, 0), (400, 0)], profile: Self.ramp(200))])
        let e = try #require(n.edges.first)
        // Which way is up depends on stored order; find it from the gains.
        let upDir: RouteDirection = e.gainForwardFt > e.gainBackwardFt ? .forward : .backward
        let up = n.walk(edgeId: 0, direction: upDir)
        let down = n.walk(edgeId: 0, direction: upDir.reversed)
        let grade = (up.gainFt - up.lossFt) * TrailNetwork.metersPerFoot / e.lengthMeters
        #expect(grade > 0.14 && grade < 0.16)
        #expect(sameSeconds(up.seconds, PaceModel.seconds(length: e.lengthMeters, grade: grade, factor: 1)))
        #expect(sameSeconds(down.seconds, PaceModel.seconds(length: e.lengthMeters, grade: -grade, factor: 1)))
        #expect(up.seconds > down.seconds)
        #expect(abs(up.gainFt - 200) < 1 && up.lossFt < 1)
        #expect(abs(down.lossFt - 200) < 1 && down.gainFt < 1)
    }

    @Test func paceFactorScalesTime() {
        let n = Self.network([Self.trail("A", [(0, 0), (400, 0)], profile: Self.ramp(100))])
        let base = n.walk(edgeId: 0, direction: .forward, paceFactor: 1.0).seconds
        let fast = n.walk(edgeId: 0, direction: .forward, paceFactor: 1.25).seconds
        let slow = n.walk(edgeId: 0, direction: .forward, paceFactor: 0.8).seconds
        #expect(sameSeconds(fast, base / 1.25))
        #expect(sameSeconds(slow, base / 0.8))
    }

    @Test func bridgeIsFlatWithNoGain() throws {
        // Two trails climbing steeply, 20 m apart: the bridge between them is flat.
        let a = Self.trail("A", [(0, 0), (100, 0)], profile: Self.ramp(100, samples: 6))
        let b = Self.trail("B", [(120, 0), (220, 0)], profile: Self.ramp(100, samples: 6))
        let n = Self.network([a, b])
        let bridge = try #require(n.bridgeEdges.first)
        for dir in [RouteDirection.forward, .backward] {
            let w = TrailNetwork.walk(bridge, direction: dir)
            #expect(w.gainFt == 0 && w.lossFt == 0)
            #expect(sameSeconds(w.seconds, bridge.lengthMeters / PaceModel.tobler(grade: 0)))
        }
    }

    @Test func connectorIsFlatWithNoGain() throws {
        let n = Self.network([Self.trail("A", [(0, 0), (400, 0)], profile: Self.ramp(200))])
        let snapper = TrailNetworkSnapper(network: n)
        let c = Self.ll(200, 60)
        let view = try snapper.insert(start: .pin(lat: c[0], lon: c[1]))
        let cid = try #require(view.start.connectorEdgeId)
        let conn = view.network.edges[cid]
        #expect(conn.kind == .connector)
        for dir in [RouteDirection.forward, .backward] {
            let w = TrailNetwork.walk(conn, direction: dir)
            #expect(w.gainFt == 0 && w.lossFt == 0)
            #expect(sameSeconds(w.seconds, conn.lengthMeters / PaceModel.tobler(grade: 0)))
        }
    }

    // MARK: - Shortest paths
    @Test func longerFlatterPathCanBeFaster() throws {
        let n = Self.steepVersusFlat()
        let a = Self.node(n, 0, 0), b = Self.node(n, 400, 0)
        let sp = try #require(n.shortestPaths(from: a))
        let path = try #require(sp.path(to: b))
        // The steep edge climbs 200 ft over 400 m (15%): 400 m at ~3.0 km/h.
        // The detour is 600 m flat at ~5.0 km/h and wins.
        #expect(path.count == 1)   // the detour is one edge (corners are not nodes)
        #expect(sp.meters[b] > 590 && sp.meters[b] < 610)
        let steepEdge = try #require(n.edges.first { $0.trailKeys == [Self.key("steep")] })
        #expect(!path.contains { $0.edgeId == steepEdge.id })
        // Walking the steep edge UP is slower than the detour; the detour is chosen.
        let upDir: RouteDirection = steepEdge.gainForwardFt > steepEdge.gainBackwardFt ? .forward : .backward
        let upSeconds = n.walk(edgeId: steepEdge.id, direction: upDir).seconds
        let upFrom = upDir == .forward ? steepEdge.from : steepEdge.to
        let upTo = upDir == .forward ? steepEdge.to : steepEdge.from
        let spUp = try #require(n.shortestPaths(from: upFrom))
        #expect(spUp.seconds[upTo] < upSeconds)
        // Coming back DOWN the steep edge is the faster option.
        let spDown = try #require(n.shortestPaths(from: upTo))
        #expect(sameSeconds(spDown.seconds[upFrom], n.walk(edgeId: steepEdge.id, direction: upDir.reversed).seconds))
        #expect(spDown.path(to: upFrom) == [RouteStep(edgeId: steepEdge.id, direction: upDir.reversed)])
    }

    @Test func gainIsAccumulatedInTheWalkingDirection() throws {
        let n = Self.network([
            Self.trail("A", [(0, 0), (200, 0)], profile: Self.ramp(100, samples: 11)),
            Self.trail("B", [(200, 0), (400, 0)], profile: Self.ramp(60, samples: 11)),
        ])
        let a = Self.node(n, 0, 0), b = Self.node(n, 400, 0)
        let out = try #require(n.shortestPaths(from: a))
        let back = try #require(n.shortestPaths(from: b))
        // Whichever way each trail is stored, going one way climbs 160 ft and the
        // other way descends the same, but both trails are rising eastward or
        // westward together here only if stored consistently; check conservation.
        #expect(abs((out.gainFt[b] - out.lossFt[b]) + (back.gainFt[a] - back.lossFt[a])) < 1)
        #expect(abs(out.gainFt[b] - back.lossFt[a]) < 1)
        #expect(abs(out.lossFt[b] - back.gainFt[a]) < 1)
        #expect(abs(max(out.gainFt[b], back.gainFt[a]) - 160) < 2)
    }

    @Test func pathReconstructionRoundTripsToMetrics() throws {
        let n = Self.steepVersusFlat()
        let costs = RouteCosts(network: n, paceFactor: 1.1)
        for from in n.nodes.map(\.id) {
            let sp = try #require(n.shortestPaths(from: from, costs: costs))
            for to in n.nodes.map(\.id) {
                let steps = try #require(sp.path(to: to))
                let m = try #require(RouteMetrics(network: n, steps: steps, paceFactor: 1.1))
                #expect(m.isContinuous)
                #expect(sameSeconds(m.seconds, sp.seconds[to]))
                #expect(sameSeconds(m.meters, sp.meters[to]))
                #expect(sameSeconds(m.gainFt, sp.gainFt[to]))
                #expect(sameSeconds(m.lossFt, sp.lossFt[to]))
                if let first = steps.first {
                    let e = n.edges[first.edgeId]
                    #expect((first.direction == .forward ? e.from : e.to) == from)
                }
                if let last = steps.last {
                    let e = n.edges[last.edgeId]
                    #expect((last.direction == .forward ? e.to : e.from) == to)
                }
            }
            #expect(sp.path(to: from) == [])
        }
    }

    @Test func unreachableNodeReturnsNil() throws {
        let n = Self.network([
            Self.trail("A", [(0, 0), (100, 0)]),
            Self.trail("B", [(500, 0), (600, 0)]),   // 400 m away: never bridged
        ])
        let a = Self.node(n, 0, 0), b = Self.node(n, 600, 0)
        let sp = try #require(n.shortestPaths(from: a))
        #expect(sp.path(to: b) == nil)
        #expect(!sp.isReachable(b))
        #expect(sp.seconds[b] == .infinity)
        #expect(sp.path(to: 999) == nil)
        #expect(n.shortestPaths(from: -1) == nil)
        #expect(n.shortestPaths(from: n.nodes.count) == nil)
    }

    @Test func bridgesAreWalkedAndCountedFlatInARoute() throws {
        let a = Self.trail("A", [(0, 0), (100, 0)], profile: Self.ramp(50, samples: 6))
        let b = Self.trail("B", [(120, 0), (220, 0)], profile: Self.ramp(50, samples: 6))
        let n = Self.network([a, b])
        let sp = try #require(n.shortestPaths(from: Self.node(n, 0, 0)))
        let steps = try #require(sp.path(to: Self.node(n, 220, 0)))
        let m = try #require(RouteMetrics(network: n, steps: steps))
        #expect(abs(m.bridgeMeters - 20) < 0.5)
        #expect(abs(m.trailMeters - 200) < 1)
        #expect(m.connectorMeters == 0)
        #expect(abs(m.meters - 220) < 1)
    }

    // MARK: - Route metrics
    @Test func metricsSplitDistanceByKindAndTrail() throws {
        let n = Self.network([Self.trail("A", [(0, 0), (400, 0)], profile: Self.flat())])
        let snapper = TrailNetworkSnapper(network: n)
        let s = Self.ll(100, 50), e = Self.ll(300, -50)
        let view = try snapper.insert(start: .pin(lat: s[0], lon: s[1]), end: .pin(lat: e[0], lon: e[1]))
        let v = view.network
        let endNode = try #require(view.end).nodeId
        let sp = try #require(v.shortestPaths(from: view.start.nodeId))
        let steps = try #require(sp.path(to: endNode))
        let m = try #require(RouteMetrics(network: v, steps: steps))
        #expect(abs(m.connectorMeters - 100) < 1.5)    // 50 m out and 50 m back
        #expect(abs(m.trailMeters - 200) < 1.5)
        #expect(m.bridgeMeters == 0)
        #expect(abs(m.metersByTrail[Self.key("A")]! - m.trailMeters) < 1e-6)
        #expect(m.trailKeys == [Self.key("A")])
        #expect(abs(m.newMeters - 200) < 1.5)
    }

    @Test func newMilesCountsASharedEdgeOnce() throws {
        let a = Self.trail("A", [(0, 0), (300, 0)])
        let b = Self.trail("B", [(100, 0), (200, 0)])
        let n = Self.network([a, b])
        let shared = try #require(n.sharedEdges.first)
        let whole = n.edges.map { RouteStep(edgeId: $0.id, direction: .forward) }
        let all = try #require(RouteMetrics(network: n, steps: whole))
        // Both trails list the shared stretch; each is credited it, new ground is 300 m.
        #expect(abs(all.metersByTrail[Self.key("B")]! - shared.lengthMeters) < 1e-6)
        #expect(abs(all.newMeters - 300) < 1)
        #expect(abs(all.meters - 300) < 1)
        // A done: the shared edge is still new because B is not done.
        let aDone = try #require(RouteMetrics(network: n, steps: whole, completed: [Self.key("A")]))
        #expect(abs(aDone.newMeters - 100) < 1)
        // Both done: nothing is new.
        let bothDone = try #require(RouteMetrics(network: n, steps: whole, completed: [Self.key("A"), Self.key("B")]))
        #expect(bothDone.newMeters == 0)
        // B done only: A's own two stretches are new, the shared one is not new for B but is for A.
        let bDone = try #require(RouteMetrics(network: n, steps: whole, completed: [Self.key("B")]))
        #expect(abs(bDone.newMeters - 300) < 1)
        // Out and back over the shared edge is walked twice but is new ground once.
        let twice = [RouteStep(edgeId: shared.id, direction: .forward), RouteStep(edgeId: shared.id, direction: .backward)]
        let t = try #require(RouteMetrics(network: n, steps: twice))
        #expect(abs(t.meters - 2 * shared.lengthMeters) < 1e-6)
        #expect(abs(t.newMeters - shared.lengthMeters) < 1e-6)
        #expect(t.isContinuous)
    }

    @Test func metricsRejectUnknownEdgesAndFlagGaps() {
        let n = Self.steepVersusFlat()
        #expect(RouteMetrics(network: n, steps: [RouteStep(edgeId: 999, direction: .forward)]) == nil)
        #expect(RouteMetrics(network: n, steps: [RouteStep(edgeId: -1, direction: .forward)]) == nil)
        let empty = RouteMetrics(network: n, steps: [])
        #expect(empty?.meters == 0 && empty?.isContinuous == true)
        // Two edges that do not meet head to tail.
        let e0 = n.edges[0]
        let other = n.edges.first { $0.from != e0.to && $0.to != e0.to && $0.from != e0.from && $0.to != e0.from }
        if let o = other {
            let m = RouteMetrics(network: n, steps: [
                RouteStep(edgeId: e0.id, direction: .forward), RouteStep(edgeId: o.id, direction: .forward)])
            #expect(m?.isContinuous == false)
        }
    }

    // MARK: - Key nodes and reachability
    @Test func keyNodePathsMatchSingleRuns() throws {
        let n = Self.steepVersusFlat()
        let costs = RouteCosts(network: n)
        let keys = [Self.node(n, 0, 0), Self.node(n, 400, 0), Self.node(n, 0, 0)]
        let kp = n.keyNodePaths(keys, costs: costs)
        #expect(kp.keyNodes.count == 2)   // duplicate dropped
        for a in kp.keyNodes {
            let sp = try #require(n.shortestPaths(from: a, costs: costs))
            for b in kp.keyNodes {
                #expect(kp.seconds(from: a, to: b) == sp.seconds[b])
                #expect(kp.meters(from: a, to: b) == sp.meters[b])
                #expect(kp.gainFt(from: a, to: b) == sp.gainFt[b])
                #expect(kp.path(from: a, to: b) == sp.path(to: b))
            }
        }
        #expect(kp.seconds(from: -5, to: 0) == nil)
    }

    @Test func reachabilityRespectsTheBudget() throws {
        let n = Self.network([Self.trail("A", [(0, 0), (1000, 0)], profile: Self.flat(samples: 51))])
        let a = Self.node(n, 0, 0), b = Self.node(n, 1000, 0)
        #expect(n.reachableNodes(from: a, withinSeconds: 0) == [a])
        let full = n.shortestPaths(from: a)!.seconds[b]
        #expect(n.reachableNodes(from: a, withinSeconds: full - 1) == [a])
        #expect(Set(n.reachableNodes(from: a, withinSeconds: full)) == [a, b])
        // Pruned run agrees with the full run on everything it reaches.
        let m = Self.steepVersusFlat()
        let src = Self.node(m, 0, 0)
        let costs = RouteCosts(network: m)
        let fullRun = m.shortestPaths(from: src, costs: costs)!
        let budget = (fullRun.seconds.filter(\.isFinite).max() ?? 0) / 2
        let pruned = m.shortestPaths(from: src, costs: costs, maxSeconds: budget)!
        for i in m.nodes.indices {
            if fullRun.seconds[i] <= budget { #expect(pruned.seconds[i] == fullRun.seconds[i]) }
            else { #expect(!pruned.isReachable(i)) }
        }
        #expect(m.reachableNodes(from: src, withinSeconds: budget) == fullRun.nodes(within: budget))
    }

    @Test func resultsAreDeterministicAndIndependentOfInputOrder() throws {
        let trails = [
            Self.trail("steep", [(0, 0), (400, 0)], profile: Self.ramp(200)),
            Self.trail("detour", [(0, 0), (0, 100), (400, 100), (400, 0)], profile: Self.flat()),
            Self.trail("cross", [(0, 50), (400, 50)], profile: Self.flat()),
        ]
        let n1 = Self.network(trails)
        let n2 = Self.network(trails.reversed())
        func fingerprint(_ n: TrailNetwork) -> [String] {
            let sp = n.shortestPaths(from: Self.node(n, 0, 0))!
            return n.nodes.map { nd in
                "\(nd.id):\(sp.seconds[nd.id]):\(sp.path(to: nd.id) ?? [])"
            }
        }
        #expect(fingerprint(n1) == fingerprint(n1))
        #expect(fingerprint(n1) == fingerprint(n2))
    }

    // MARK: - Real fixture: South Mountain
    private struct GeomFile: Decodable {
        let id: String
        let trails: [Trail]
        let parking: [ParkingLot]?
    }
    private static func loadSouthMountain() throws -> GeomFile {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // SouthMountainExplorerTests
            .deletingLastPathComponent()   // ios
            .deletingLastPathComponent()   // repo root
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        return try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
    }

    @Test func southMountainTrailheadToTrailheadAndPerformance() throws {
        let geom = try Self.loadSouthMountain()
        let lots = try #require(geom.parking)
        let net = TrailNetwork(trails: geom.trails.map { .init(areaId: geom.id, trail: $0) })
        let bursera = try #require(lots.first { $0.name == "Bursera Trailhead" })
        let national = try #require(lots.first { abs($0.lat - 33.33029) < 1e-4 && abs($0.lon + 112.14429) < 1e-4 })
        let snapper = TrailNetworkSnapper(network: net)
        let view = try snapper.insert(start: .parking(bursera), end: .parking(national))
        let v = view.network
        let end = try #require(view.end)
        #expect(view.start.snap.component.id == end.snap.component.id)

        // One-time costs, then Dijkstra alone.
        let t0 = ContinuousClock.now
        let costs = RouteCosts(network: v)
        let costMs = Self.ms(ContinuousClock.now - t0)
        var best = Double.infinity
        var sp: ShortestPaths!
        for _ in 0..<20 {
            let s = ContinuousClock.now
            sp = v.shortestPaths(from: view.start.nodeId, costs: costs)
            best = min(best, Self.ms(ContinuousClock.now - s))
        }
        print("ROUTING perf | \(v.nodes.count) nodes \(v.edges.count) edges | costs \(String(format: "%.2f", costMs)) ms | dijkstra best of 20 \(String(format: "%.2f", best)) ms")
        #expect(best < 50, "Dijkstra took \(best) ms")

        let steps = try #require(sp.path(to: end.nodeId))
        let m = try #require(RouteMetrics(network: v, steps: steps))
        #expect(m.isContinuous)
        #expect(abs(m.seconds - sp.seconds[end.nodeId]) < 1e-6)
        let straight = TrailNetworkSnapper.haversine(bursera.lat, bursera.lon, national.lat, national.lon)
        let kmh = m.meters / m.seconds * 3.6
        print(String(format: "ROUTING bursera->national | straight %.0f m | route %.0f m (trail %.0f, connector %.0f, bridge %.0f) | %.0f min | gain %.0f ft loss %.0f ft | %.2f km/h | %d steps",
                     straight, m.meters, m.trailMeters, m.connectorMeters, m.bridgeMeters, m.seconds / 60,
                     m.gainFt, m.lossFt, kmh, steps.count))
        print("ROUTING trails | " + m.trailKeys.map { String(format: "%@ %.0f m", $0.trailId, m.metersByTrail[$0]!) }.joined(separator: ", "))
        #expect(m.meters >= straight - 1)
        #expect(m.meters < straight * 4)
        #expect(abs(m.trailMeters + m.connectorMeters + m.bridgeMeters - m.meters) < 1e-6)
        // Tobler is ~5 km/h flat; with real climbs the average should sit near it.
        #expect(kmh > 3.5 && kmh < 5.6)
        // The route cannot be shorter than the sum of its parts or have gain without length.
        #expect(m.gainFt >= 0 && m.lossFt >= 0)

        // Reverse trip: same ground, asymmetric elevation and time.
        let back = try #require(v.shortestPaths(from: end.nodeId, costs: costs))
        let backSteps = try #require(back.path(to: view.start.nodeId))
        let bm = try #require(RouteMetrics(network: v, steps: backSteps))
        print(String(format: "ROUTING national->bursera | route %.0f m | %.0f min | gain %.0f ft loss %.0f ft", bm.meters, bm.seconds / 60, bm.gainFt, bm.lossFt))
        #expect(abs(bm.meters - m.meters) / m.meters < 0.15)
        #expect(abs(bm.gainFt - m.lossFt) < 0.2 * max(m.lossFt, 50) + 30)

        // Reachability grows with the budget and stays inside the component.
        let r30 = v.reachableNodes(from: view.start.nodeId, withinSeconds: 30 * 60)
        let r90 = v.reachableNodes(from: view.start.nodeId, withinSeconds: 90 * 60)
        #expect(!r30.isEmpty && Set(r30).isSubset(of: Set(r90)) && r90.count > r30.count)
        for id in r90 { #expect(sp.seconds[id] <= 90 * 60) }
    }

    private static func ms(_ d: Duration) -> Double {
        Double(d.components.seconds) * 1000 + Double(d.components.attoseconds) / 1e15
    }
}
