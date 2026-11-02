import Foundation
import Testing
@testable import SouthMountainExplorer

/// `planOutcome` on the real South Mountain data from Bursera Trailhead ("Plan a Hike", Task 6).
struct RoutePlanOutcomeSouthMountainTests {
    typealias Key = TrailNetwork.TrailKey

    private struct GeomFile: Decodable {
        let id: String
        let trails: [Trail]
        let parking: [ParkingLot]?
    }

    private struct World {
        let inputs: [TrailNetwork.Input]
        let view: TrailNetworkWithEndpoints
    }

    private static func world() throws -> World {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        let geom = try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
        let bursera = try #require(geom.parking?.first { $0.name == "Bursera Trailhead" })
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        let view = try TrailNetworkSnapper(network: TrailNetwork(trails: inputs)).insert(start: .parking(bursera))
        return World(inputs: inputs, view: view)
    }

    private static func request(_ w: World, minutes: Double, completed: Set<Key> = [], maxGain: Double? = nil)
        -> PlanOutcomeRequest {
        PlanOutcomeRequest(network: w.view, trails: w.inputs, completion: TrailCompletionLookup(completed: completed),
                           timeLimitSeconds: minutes * 60, maxGainFt: maxGain)
    }

    private static func row(_ p: RoutePlan) -> String {
        "trails=\(p.finished.count) [\(p.finished.map(\.name).joined(separator: ", "))] " +
        "\(Int((p.metrics.seconds / 60).rounded())) min gain=\(Int(p.metrics.gainFt.rounded())) ft " +
        "new=\(String(format: "%.2f", p.newMiles)) mi dist=\(String(format: "%.2f", p.distanceMiles)) mi"
    }

    /// Runs, prints, and checks every plan in the outcome against the limits.
    @discardableResult
    private static func demo(_ title: String, _ w: World, _ req: PlanOutcomeRequest) -> (PlanOutcome, Double) {
        let t0 = Date()
        let out = RoutePlanningService.planOutcome(req)
        let dt = Date().timeIntervalSince(t0)
        var plans: [(String, RoutePlan)] = []
        switch out {
        case .planned(let best, let alts):
            print("T6DEMO \(title): PLANNED \(String(format: "%.2f", dt)) s")
            print("T6DEMO   best: \(row(best))")
            plans.append(("best", best))
            for a in alts {
                print("T6DEMO   \(a.label.title): \(row(a.plan)) | \(a.why)")
                plans.append((a.label.title, a.plan))
            }
        case .nothingFits(let n):
            print("T6DEMO \(title): NOTHING FITS \(String(format: "%.2f", dt)) s")
            print("T6DEMO   message: \(n.message)")
            if let f = n.fallback { print("T6DEMO   fallback: \(row(f))"); plans.append(("fallback", f)) }
            else { print("T6DEMO   fallback: none") }
        case .noCandidates(let s):
            print("T6DEMO \(title): NO CANDIDATES \(String(format: "%.2f", dt)) s | \(s.message)")
        }
        for (name, p) in plans {
            #expect(p.metrics.isContinuous, "\(title) \(name)")
            #expect(p.metrics.seconds <= req.timeLimitSeconds + 1e-3, "\(title) \(name) over time")
            if let g = req.maxGainFt { #expect(p.metrics.gainFt <= g + 1e-6, "\(title) \(name) over gain") }
            let m = RouteMetrics(network: w.view.network, steps: p.steps, completed: Set(w.view.network.trailKeys.filter(req.completion.isComplete)))
            #expect(m?.newMeters == p.metrics.newMeters, "\(title) \(name) metrics")
        }
        #expect(dt < 3.0, "\(title) took \(dt) s")
        return (out, dt)
    }

    @Test func southMountainFromBursera() throws {
        let w = try Self.world()
        // Tiny budgets: nothing fits; message, exact rounded time, fallback.
        for minutes in [15.0, 30.0] {
            let (out, _) = Self.demo("none done, loop, \(Int(minutes)) min", w, Self.request(w, minutes: minutes))
            if case .nothingFits(let n) = out {
                let c = try #require(n.closest)
                #expect(c.roundedSeconds > minutes * 60)
                // The one-tap action must yield a plan.
                let again = RoutePlanningService.planOutcome(Self.request(w, minutes: c.roundedSeconds / 60))
                if case .planned = again {} else { Issue.record("re-plan at \(c.roundedSeconds) s did not plan") }
            }
        }
        for minutes in [120.0, 240.0] {
            let (out, _) = Self.demo("none done, loop, \(Int(minutes / 60)) h", w, Self.request(w, minutes: minutes))
            if case .planned = out {} else { Issue.record("expected a plan at \(minutes) min") }
        }
        // The ten nearest trails done (same set as Task 5's demo).
        let wide = TrailEligibility.evaluate(
            network: w.view, trails: w.inputs, completion: .none, options: .init(budgetSeconds: 4 * 3600))
        let ten = Set(wide.candidates.prefix(10).map(\.key))
        #expect(ten.count == 10)
        for minutes in [15.0, 60.0, 120.0] {
            let (out, _) = Self.demo("10 nearest done, loop, \(Int(minutes)) min", w,
                                     Self.request(w, minutes: minutes, completed: ten))
            if case .nothingFits(let n) = out, let c = n.closest {
                let again = RoutePlanningService.planOutcome(Self.request(w, minutes: c.roundedSeconds / 60, completed: ten))
                if case .planned = again {} else { Issue.record("re-plan at \(c.roundedSeconds) s did not plan") }
            }
        }
        let (capped, _) = Self.demo("none done, loop, 2 h, max gain 500 ft", w, Self.request(w, minutes: 120, maxGain: 500))
        if case .planned = capped {} else { Issue.record("expected a capped plan") }
    }

    @Test func southMountainOutcomeIsDeterministic() throws {
        let w = try Self.world()
        let a = RoutePlanningService.planOutcome(Self.request(w, minutes: 240))
        let b = RoutePlanningService.planOutcome(Self.request(w, minutes: 240))
        guard case .planned(let ba, let aa) = a, case .planned(let bb, let ab) = b else {
            Issue.record("expected plans"); return
        }
        #expect(ba.steps == bb.steps)
        #expect(aa.map(\.plan.steps) == ab.map(\.plan.steps))
        #expect(aa.map(\.why) == ab.map(\.why))
    }
}
