import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release B task 2: the forward-only tracker replayed on real
/// area data (National Trail across its 178 m bridged gap) and, on Quentin's
/// machine only, on his own recorded hikes.
struct PlanProgressReplayTests {
    typealias M = PlanProgressMatcherTests
    typealias Key = TrailNetwork.TrailKey

    private struct Geo: Decodable { let id: String; let trails: [Trail] }

    private static func geom(_ areaId: String) -> Geo? {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("public/areas/geom/\(areaId).json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(Geo.self, from: data)
    }

    private static func network(_ g: Geo) -> (net: TrailNetwork, names: [Key: String]) {
        let inputs = g.trails.map { TrailNetwork.Input(areaId: g.id, trail: $0) }
        let names = Dictionary(inputs.map { ($0.key, $0.trail.name) }, uniquingKeysWith: { a, _ in a })
        return (TrailNetwork(trails: inputs), names)
    }

    /// Walks `route` from start to finish at 1.4 m/s with GPS noise.
    private static func walkRoute(_ r: PlanGuideRoute, sigma: Double = 3) -> M.Walker {
        var w = M.Walker.route(r)
        w.sigma = sigma
        var s = 0.0
        while s <= r.length {
            w.at(r.point(at: s))
            s += w.speed
        }
        return w
    }

    // MARK: - National Trail out and back across the 178 m gap

    /// National Trail edges walked away from `node`, leaving by `edge`, for
    /// about `meters`. At each node the straightest National Trail edge wins.
    private static func chain(_ net: TrailNetwork, _ key: Key, from node: Int, leaving edge: Int,
                              meters: Double) -> [RouteStep] {
        func oriented(_ id: Int, from n: Int) -> [LocalPt] {
            let e = net.edges[id]
            return e.from == n ? e.polyline : e.polyline.reversed()
        }
        func heading(_ pts: [LocalPt]) -> Double {
            let cum = TurnCueGeometry.cumulative(pts)
            let k = cum.firstIndex { $0 >= 20 } ?? pts.count - 1
            return TurnCueGeometry.bearing(from: pts[0], to: pts[k])
        }
        var out: [RouteStep] = [], n = node, prev = edge, total = 0.0, used: Set<Int> = [edge]
        while total < meters {
            let arrive = TurnCueGeometry.normalize360(heading(oriented(prev, from: n)) + 180)
            let next = net.nodes[n].edgeIds.filter {
                !used.contains($0) && net.edges[$0].kind == .trail && net.edges[$0].trailKeys.contains(key)
            }.min {
                abs(TurnCueGeometry.signedAngleDiff(from: arrive, to: heading(oriented($0, from: n))))
                    < abs(TurnCueGeometry.signedAngleDiff(from: arrive, to: heading(oriented($1, from: n))))
            }
            guard let id = next else { break }
            let e = net.edges[id]
            out.append(RouteStep(edgeId: id, direction: e.from == n ? .forward : .backward))
            used.insert(id)
            total += e.lengthMeters
            n = e.from == n ? e.to : e.from
            prev = id
        }
        return out
    }

    @Test func nationalTrailOutAndBackAcrossTheBridgedGap() throws {
        let g = try #require(Self.geom("south-mountain-park-and-preserve-az"))
        let (net, names) = Self.network(g)
        let key = Key(areaId: g.id, trailId: "national-trail")
        let bridge = try #require(net.bridgeEdges.first { $0.trailKeys == [key] && $0.lengthMeters > 170 })
        let west = Self.chain(net, key, from: bridge.from, leaving: bridge.id, meters: 1200)
        let east = Self.chain(net, key, from: bridge.to, leaving: bridge.id, meters: 1200)
        #expect(!west.isEmpty && !east.isEmpty)
        func flip(_ s: [RouteStep]) -> [RouteStep] {
            s.reversed().map { RouteStep(edgeId: $0.edgeId, direction: $0.direction.reversed) }
        }
        let out = flip(west) + [RouteStep(edgeId: bridge.id, direction: .forward)] + east
        let walk = out + flip(out)
        let infos = walk.map { s -> RoutePlan.StepInfo in
            let e = net.edges[s.edgeId]
            return .init(trailKey: key, kind: e.kind, isGoalCover: true)
        }
        let metrics = try #require(RouteMetrics(network: net, steps: walk))
        #expect(metrics.isContinuous)
        let first = net.edges[walk[0].edgeId]
        let start = walk[0].direction == .forward ? first.from : first.to
        let plan = RoutePlan(steps: walk, stepInfo: infos, finished: [], metrics: metrics, shape: .loop,
                             startNodeId: start, endNodeId: start, evaluations: 0)
        let steps = PlanStepBuilder.steps(plan: plan, network: net, names: names)
        let r = try #require(PlanGuideRoute(steps: steps))
        #expect(r.markers.filter(\.isTurnAround).count == 1)
        #expect(r.markers.filter { $0.kind == .bridge }.count == 2)

        var m = PlanProgressMatcher(route: r)
        let w = Self.walkRoute(r)
        let t = M.run(&m, w.fixes)
        print("REPLAY: national trail out and back length m = \(Int(r.length)) steps = \(r.markers.count) " +
              "fixes = \(w.fixes.count) offExpected = \(t.count(.offExpected)) skips = \(t.count(.skippedAhead))")
        #expect(t.steps == Array(r.markers.indices))
        #expect(t.count(.offExpected) == 0)
        #expect(t.count(.skippedAhead) == 0)
        #expect(m.cursorMeters > r.length - 20)

        // Task 3: the guide on the same walk. Every turn cued at the turn in
        // order, one turn-around cue, no off-route warning, one finish.
        var e = PlanGuideEngineTests.engine(r)
        let g2 = PlanGuideEngineTests.run(&e, w.fixes)
        let atTurn = g2.of(.atJunction)
        print("REPLAY: national trail guide cues = \(g2.cues.count) offRoute = \(g2.of(.offTrail).count) " +
              "completed = \(g2.completed.count)")
        #expect(atTurn.map(\.junctionId) == Array(1..<r.markers.count).map(Optional.some))
        #expect(atTurn.filter { $0.symbol == .turnAround }.count == 1)
        #expect(g2.of(.offTrail).isEmpty)
        #expect(g2.completed.count == 1)
    }

    // MARK: - Real recorded hikes (local only, never committed)

    /// Quentin's own recorded hikes. Personal location history: read at run
    /// time only, never copied into the repo. Path comes from the
    /// TREKDEX_PRIVATE_HIKES environment variable (pass it to xcodebuild as
    /// TEST_RUNNER_TREKDEX_PRIVATE_HIKES). Unset on CI, so the test skips.
    static let historyPath = ProcessInfo.processInfo.environment["TREKDEX_PRIVATE_HIKES"] ?? ""

    private struct Hike: Decodable { let areaId: String; let path: [[Double]] }

    /// Nearest point on any network edge within `radius`, via a 50 m grid.
    private struct Snapper {
        let segs: [(LocalPt, LocalPt)]
        let grid: [Int64: [Int]]
        static let cell = 50.0
        static func key(_ x: Int, _ y: Int) -> Int64 { Int64(x) << 32 | Int64(UInt32(bitPattern: Int32(y))) }

        init(_ net: TrailNetwork) {
            var segs: [(LocalPt, LocalPt)] = [], grid: [Int64: [Int]] = [:]
            for e in net.edges where e.kind == .trail {
                for (a, b) in zip(e.polyline, e.polyline.dropFirst()) {
                    let i = segs.count
                    segs.append((a, b))
                    for x in Int(floor(min(a.x, b.x) / Self.cell))...Int(floor(max(a.x, b.x) / Self.cell)) {
                        for y in Int(floor(min(a.y, b.y) / Self.cell))...Int(floor(max(a.y, b.y) / Self.cell)) {
                            grid[Self.key(x, y), default: []].append(i)
                        }
                    }
                }
            }
            self.segs = segs
            self.grid = grid
        }

        func snap(_ p: LocalPt, radius: Double) -> LocalPt? {
            let cx = Int(floor(p.x / Self.cell)), cy = Int(floor(p.y / Self.cell))
            var best: (LocalPt, Double)?
            for x in (cx - 1)...(cx + 1) {
                for y in (cy - 1)...(cy + 1) {
                    for i in grid[Self.key(x, y)] ?? [] {
                        let (a, b) = segs[i]
                        let (t, d) = TurnCueGeometry.projectOnSegment(p, a, b)
                        if d <= radius, d < (best?.1 ?? .infinity) {
                            best = (LocalPt(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t), d)
                        }
                    }
                }
            }
            return best?.0
        }
    }

    /// A plan matching a recorded track: the track every 15 m, snapped onto
    /// the mapped trails where it ran within 20 m of one, split into steps
    /// every 400 m and at every turn-around.
    private static func plan(for track: [LocalPt], net: TrailNetwork, snapper: Snapper) -> [PlanStep] {
        var line: [LocalPt] = []
        for p in track where line.last.map({ TurnCueGeometry.distance($0, p) >= 15 }) ?? true { line.append(p) }
        line = line.map { snapper.snap($0, radius: 20) ?? $0 }
        line = line.reduce(into: [LocalPt]()) { acc, p in
            if acc.last.map({ TurnCueGeometry.distance($0, p) >= 2 }) ?? true { acc.append(p) }
        }
        guard line.count >= 3 else { return [] }
        func reverses(_ k: Int) -> Bool {
            guard k >= 2, k + 2 < line.count else { return false }
            let a = TurnCueGeometry.bearing(from: line[k - 2], to: line[k])
            let b = TurnCueGeometry.bearing(from: line[k], to: line[k + 2])
            return abs(TurnCueGeometry.signedAngleDiff(from: a, to: b)) > 150
        }
        // Step starts: vertex index and whether it is a turn-around.
        var starts: [(Int, Bool)] = [(0, false)]
        var since = 0.0
        for k in 1..<(line.count - 1) {
            since += TurnCueGeometry.distance(line[k - 1], line[k])
            if reverses(k), k - starts.last!.0 >= 3 { starts.append((k, true)); since = 0 }
            else if since >= 400 { starts.append((k, false)); since = 0 }
        }
        func coord(_ p: LocalPt) -> PlannedRoute.Coord {
            let c = net.coordinate(of: p)
            return PlannedRoute.Coord(lat: c.lat, lon: c.lon)
        }
        return starts.enumerated().map { i, s in
            let end = i + 1 < starts.count ? starts[i + 1].0 : line.count - 1
            let pts = Array(line[s.0...end])
            let action: PlanStep.Action = i == 0 ? .start : (s.1 ? .turnAround : .straight)
            return PlanStep(number: i + 1, action: action, kind: .trail, trailName: "Trail", staysOnTrail: true,
                            meters: TurnCueGeometry.cumulative(pts).last ?? 0, at: coord(pts[0]),
                            coords: pts.map(coord), isLast: i == starts.count - 1)
        }
    }

    /// Each recorded hike (in an area whose trail data is in the repo)
    /// replayed fix by fix against a plan matching it. Prints counts only:
    /// never a coordinate, area or trail name.
    @Test(.enabled(if: FileManager.default.fileExists(atPath: historyPath), "local only: needs hike-history.json"))
    func realRecordedHikesReplayLocally() throws {
        let data = try Data(contentsOf: URL(fileURLWithPath: Self.historyPath))
        let hikes = try JSONDecoder().decode([Hike].self, from: data)
        var nets: [String: (TrailNetwork, Snapper)] = [:], missing: Set<String> = []
        var replayed = 0, guideOffRoute = 0, guideCompleted = 0
        for (i, h) in hikes.enumerated() where h.path.count >= 100 && !missing.contains(h.areaId) {
            if nets[h.areaId] == nil {
                guard let g = Self.geom(h.areaId) else { missing.insert(h.areaId); continue }
                let net = Self.network(g).net
                nets[h.areaId] = (net, Snapper(net))
            }
            guard let (net, snapper) = nets[h.areaId] else { continue }
            let fixes = h.path.filter { $0.count >= 3 }.map {
                TurnCueFix(lat: $0[0], lon: $0[1], timestamp: $0[2] / 1000, horizontalAccuracy: 10, speed: nil)
            }
            let track = fixes.map { net.projector.local(lat: $0.lat, lon: $0.lon) }
            guard let r = PlanGuideRoute(steps: Self.plan(for: track, net: net, snapper: snapper)),
                  r.length >= 500 else { continue }
            var m = PlanProgressMatcher(route: r)
            let t = M.run(&m, fixes)
            let used = fixes.count - t.count(.ignored)
            let matched = Double(t.count(.matched)) / Double(max(1, used))
            let reached = m.cursorMeters / r.length
            let inOrder = t.steps == Array(t.steps.sorted())
            print("REPLAY: real hike \(i) km = \(String(format: "%.1f", r.length / 1000)) steps = \(r.markers.count) " +
                  "turnarounds = \(r.markers.filter(\.isTurnAround).count) fixes = \(fixes.count) " +
                  "matched = \(String(format: "%.3f", matched)) reached = \(String(format: "%.3f", reached)) " +
                  "stepsSeen = \(t.steps.count) skips = \(t.count(.skippedAhead))")
            #expect(inOrder, "hike \(i)")
            #expect(t.cursorNeverMovedBack, "hike \(i)")
            #expect(reached > 0.95, "hike \(i)")
            #expect(matched > 0.9, "hike \(i)")
            // Task 3: the guide on the same fixes. Counts only.
            var e = PlanGuideEngineTests.engine(r)
            let g = PlanGuideEngineTests.run(&e, fixes)
            print("REPLAY: real hike \(i) guide atTurn = \(g.of(.atJunction).count) of \(r.markers.count - 1) " +
                  "offRoute = \(g.of(.offTrail).count) gpsWeak = \(g.of(.gpsWeak).count) " +
                  "completed = \(g.completed.count)")
            guideOffRoute += g.of(.offTrail).count
            guideCompleted += g.completed.count
            replayed += 1
        }
        print("REPLAY: real hikes replayed = \(replayed) of \(hikes.count) " +
              "guide offRoute total = \(guideOffRoute) completed = \(guideCompleted)")
        #expect(replayed > 0)
    }
}
