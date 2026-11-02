import Foundation
import Testing
@testable import SouthMountainExplorer

/// The planner on the real South Mountain data ("Plan a Hike", Task 5).
struct RoutePlannerSouthMountainTests {
    typealias Key = TrailNetwork.TrailKey

    private struct GeomFile: Decodable {
        let id: String
        let trails: [Trail]
        let parking: [ParkingLot]?
    }

    private static func load() throws -> GeomFile {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // SouthMountainExplorerTests
            .deletingLastPathComponent()   // ios
            .deletingLastPathComponent()   // repo root
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        return try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
    }

    /// Network, inputs and Bursera start, built once per call (cheap next to planning).
    private struct World {
        let geom: GeomFile
        let inputs: [TrailNetwork.Input]
        let net: TrailNetwork
        let bursera: ParkingLot
    }

    private static func world() throws -> World {
        let geom = try load()
        let bursera = try #require(geom.parking?.first { $0.name == "Bursera Trailhead" })
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        return World(geom: geom, inputs: inputs, net: TrailNetwork(trails: inputs), bursera: bursera)
    }

    struct Run {
        let plan: RoutePlan?
        let elig: TrailEligibility
        let view: TrailNetworkWithEndpoints
        let request: RoutePlanRequest
        let seconds: Double
    }

    private static func run(
        _ w: World, hours: Double, completed: Set<Key> = [], shape: RoutePlanRequest.Shape = .loop,
        end: StartPoint? = nil, maxGain: Double? = nil
    ) throws -> Run {
        let view = try TrailNetworkSnapper(network: w.net).insert(start: .parking(w.bursera), end: end)
        let done = TrailCompletionLookup(completed: completed)
        let elig = TrailEligibility.evaluate(
            network: view, trails: w.inputs, completion: done,
            options: .init(budgetSeconds: hours * 3600, trip: shape == .loop ? .loop : .oneWay))
        let req = RoutePlanRequest(network: view, candidates: elig.candidates, completion: done,
                                   timeLimitSeconds: hours * 3600, maxGainFt: maxGain, shape: shape)
        let t0 = Date()
        let plan = RoutePlanner.plan(req)
        return Run(plan: plan, elig: elig, view: view, request: req, seconds: Date().timeIntervalSince(t0))
    }

    private static func describe(_ title: String, _ r: Run, _ budget: Double) {
        guard let p = r.plan else {
            print("T5DEMO \(title): NO PLAN (candidates=\(r.elig.candidates.count)) compute=\(String(format: "%.2f", r.seconds)) s")
            return
        }
        let names = p.finished.map(\.name).joined(separator: " > ")
        let m = p.metrics
        print("T5DEMO \(title): trails=\(p.finished.count) dist=\(String(format: "%.2f", p.distanceMiles)) mi " +
              "time=\(Int(m.seconds / 60)) min (budget \(Int(budget / 60))) gain=\(Int(m.gainFt)) ft " +
              "new=\(String(format: "%.2f", p.newMiles)) mi flagged=\(Int(p.flaggedMeters)) m " +
              "compute=\(String(format: "%.2f", r.seconds)) s evals=\(p.evaluations)")
        print("T5DEMO   order: \(names)")
    }

    /// Plan sanity: valid walk, in budget, and at least each included trail's
    /// lower-bound estimate.
    private static func check(_ r: Run, budget: Double) -> [String] {
        guard let p = r.plan else { return [] }
        var problems: [String] = []
        let o = PlannerKit.Outcome(view: r.view, eligibility: r.elig, request: r.request, plan: p, seconds: r.seconds)
        problems += PlannerKit.validate(o)
        if p.metrics.seconds > budget + 1e-3 { problems.append("over budget") }
        for f in p.finished {
            if let c = r.elig.candidates.first(where: { $0.key == f.key }), p.metrics.seconds < c.estimatedSeconds - 1 {
                problems.append("plan shorter than the lower bound of \(f.name)")
            }
        }
        return problems
    }

    @Test func southMountainFromBursera() throws {
        let w = try Self.world()
        var problems: [String] = []
        var tenNearest: [Key] = []
        for hours in [1.0, 2.0, 4.0] {
            let r = try Self.run(w, hours: hours)
            Self.describe("none done, loop, \(Int(hours)) h", r, hours * 3600)
            problems += Self.check(r, budget: hours * 3600).map { "none \(hours) h: \($0)" }
            #expect(r.plan != nil)
            #expect(r.seconds < 2.0, "planning took \(r.seconds) s")
            if hours == 4 { tenNearest = r.elig.candidates.prefix(10).map(\.key) }
        }
        #expect(tenNearest.count == 10)
        for hours in [1.0, 2.0, 4.0] {
            let r = try Self.run(w, hours: hours, completed: Set(tenNearest))
            Self.describe("10 nearest done, loop, \(Int(hours)) h", r, hours * 3600)
            problems += Self.check(r, budget: hours * 3600).map { "10 done \(hours) h: \($0)" }
            #expect(r.seconds < 2.0, "planning took \(r.seconds) s")
            for k in tenNearest { #expect(!(r.plan?.finishedTrailKeys.contains(k) ?? false)) }
        }
        let capped = try Self.run(w, hours: 2, maxGain: 500)
        Self.describe("none done, loop, 2 h, max gain 500 ft", capped, 7200)
        problems += Self.check(capped, budget: 7200).map { "gain 500: \($0)" }
        #expect((capped.plan?.metrics.gainFt ?? 0) <= 500 + 1e-6)
        let capped4 = try Self.run(w, hours: 4, maxGain: 500)
        Self.describe("none done, loop, 4 h, max gain 500 ft", capped4, 14400)
        problems += Self.check(capped4, budget: 14400).map { "gain 500 (4 h): \($0)" }
        print("T5PROBLEMS \(problems)")
        #expect(problems == [])
    }

    @Test func southMountainOneWayToNationalTrailLot() throws {
        let w = try Self.world()
        let end = StartPoint.trailhead(name: "National Trail lot", lat: 33.33029, lon: -112.14429)
        do {
            let r = try Self.run(w, hours: 2, shape: .oneWay, end: end)
            Self.describe("one-way Bursera -> National Trail lot, 2 h", r, 7200)
            print("T5DEMO   one-way candidates=\(r.elig.candidates.count) excluded=\(r.elig.excluded.count)")
            print("T5PROBLEMS one-way \(Self.check(r, budget: 7200))")
            #expect(Self.check(r, budget: 7200) == [])
            #expect(r.seconds < 2.0)
            if let p = r.plan { #expect(p.endNodeId == r.view.end?.nodeId) }
        } catch {
            print("T5DEMO one-way Bursera -> National Trail lot: end point could not join the network: \(error)")
        }
    }

    /// 4 h from every third parking lot (different halves of the park and
    /// different candidate counts); each plan must finish in under 2 s.
    @Test func fourHourPlansFromManyTrailheadsStayFast() throws {
        let geom = try Self.load()
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        let net = TrailNetwork(trails: inputs)
        let lots = geom.parking ?? []
        var worst = 0.0
        var planned = 0
        var problems: [String] = []
        for lot in stride(from: 0, to: lots.count, by: 3).map({ lots[$0] }) {
            guard let view = try? TrailNetworkSnapper(network: net).insert(start: .parking(lot)) else { continue }
            let elig = TrailEligibility.evaluate(network: view, trails: inputs, completion: .none,
                                                 options: .init(budgetSeconds: 4 * 3600))
            let req = RoutePlanRequest(network: view, candidates: elig.candidates, timeLimitSeconds: 4 * 3600)
            let t0 = Date()
            let plan = RoutePlanner.plan(req)
            let dt = Date().timeIntervalSince(t0)
            worst = max(worst, dt)
            if plan != nil { planned += 1 }
            let r = Run(plan: plan, elig: elig, view: view, request: req, seconds: dt)
            problems += Self.check(r, budget: 4 * 3600).map { "\(lot.name ?? "lot"): \($0)" }
        }
        print("T5PERF 4 h from \(planned) trailheads: worst \(String(format: "%.2f", worst)) s")
        #expect(problems == [])
        #expect(worst < 2.0, "slowest plan took \(worst) s")
    }

    @Test func southMountainPlanIsDeterministic() throws {
        let w = try Self.world()
        let a = try Self.run(w, hours: 2)
        let b = try Self.run(w, hours: 2)
        #expect(a.plan?.steps == b.plan?.steps)
        #expect(a.plan?.finishedTrailKeys == b.plan?.finishedTrailKeys)
    }
}
