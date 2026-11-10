import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike round 6: the numbered steps ("1. Start on X, 0.6 mi",
/// "2. Left onto Y, 1.1 mi") built from a plan's edge walk.
struct PlanStepsTests {
    typealias K = PlannerKit

    /// A plan that walks node to node through the given points (local metres).
    /// Each hop uses the shortest edge between the two nodes nearest the points.
    static func plan(_ net: TrailNetwork, through pts: [(Double, Double)], via: [String]? = nil,
                     shape: RoutePlanRequest.Shape = .loop) throws -> RoutePlan {
        func node(_ p: (Double, Double)) throws -> Int {
            let c = K.ll(p.0, p.1)
            return try #require(net.nodes.min {
                Area.meters($0.coordinate.lat, $0.coordinate.lon, c[0], c[1]) < Area.meters($1.coordinate.lat, $1.coordinate.lon, c[0], c[1])
            }).id
        }
        var steps: [RouteStep] = []
        var infos: [RoutePlan.StepInfo] = []
        for (i, (a, b)) in zip(pts, pts.dropFirst()).enumerated() {
            let na = try node(a), nb = try node(b)
            let e = try #require(net.edges.filter {
                (($0.from == na && $0.to == nb) || ($0.from == nb && $0.to == na))
                    && (via == nil || $0.trailKeys.contains(K.key(via![i])))
            }
                .min { $0.lengthMeters < $1.lengthMeters }, "no edge \(a) to \(b)")
            steps.append(RouteStep(edgeId: e.id, direction: e.from == na ? .forward : .backward))
            infos.append(.init(trailKey: e.trailKeys.first, kind: e.kind, isGoalCover: true))
        }
        let metrics = try #require(RouteMetrics(network: net, steps: steps))
        #expect(metrics.isContinuous)
        return RoutePlan(steps: steps, stepInfo: infos, finished: [], metrics: metrics, shape: shape,
                         startNodeId: try node(pts[0]), endNodeId: try node(pts[pts.count - 1]), evaluations: 0)
    }

    static func steps(_ trails: [Trail], _ pts: [(Double, Double)], via: [String]? = nil,
                      shape: RoutePlanRequest.Shape = .loop) throws -> (steps: [PlanStep], plan: RoutePlan) {
        let net = TrailNetwork(trails: K.inputs(trails))
        let p = try plan(net, through: pts, via: via, shape: shape)
        let names = Dictionary(uniqueKeysWithValues: K.inputs(trails).map { ($0.key, $0.trail.name) })
        return (PlanStepBuilder.steps(plan: p, network: net, names: names), p)
    }

    static func checkInvariants(_ s: [PlanStep], _ p: RoutePlan) {
        #expect(!s.isEmpty)
        #expect(s.map(\.number) == Array(1...max(1, s.count)))
        #expect(s.first?.action == .start)
        #expect(s.filter(\.isLast).count == 1 && s.last?.isLast == true)
        #expect(abs(s.reduce(0) { $0 + $1.meters } - p.metrics.meters) < 1e-6)
        // Legs join end to start.
        for (a, b) in zip(s, s.dropFirst()) {
            let e = a.coords.last!, f = b.coords.first!
            #expect(Area.meters(e.lat, e.lon, f.lat, f.lon) < 1)
        }
    }

    @Test func straightThroughJunctionOnSameTrailIsNotNumbered() throws {
        // A runs east; B leaves north from its middle. Walking A straight past B is one step.
        let a = K.trail("A", [(0, 0), (500, 0), (1000, 0)])
        let b = K.trail("B", [(500, 0), (500, 500)])
        let (s, p) = try Self.steps([a, b], [(0, 0), (500, 0), (1000, 0)], shape: .oneWay)
        #expect(s.count == 1)
        #expect(s[0].line(units: .imperial) == "1. Start on Trail A, 0.62 mi")
        Self.checkInvariants(s, p)
    }

    @Test func oneWayTurnOntoAnotherTrail() throws {
        // East on A, then B goes south (right) while C carries on east.
        let a = K.trail("A", [(0, 0), (500, 0)])
        let b = K.trail("B", [(500, 0), (500, -500)])
        let c = K.trail("C", [(500, 0), (1000, 0)])
        let (s, p) = try Self.steps([a, b, c], [(0, 0), (500, 0), (500, -500)], shape: .oneWay)
        #expect(s.map(\.instruction) == ["Start on Trail A", "Right onto Trail B"])
        #expect(s.map(\.isLast) == [false, true])
        #expect(abs(s[1].meters - 500) < 5)
        // The decision point is the junction.
        let j = K.ll(500, 0)
        #expect(Area.meters(s[1].at.lat, s[1].at.lon, j[0], j[1]) < 2)
        Self.checkInvariants(s, p)
    }

    @Test func turnToStayOnTrailWhereAStraighterWayLeadsElsewhere() throws {
        // A bends north at x=500 where B carries straight on east.
        let a = K.trail("A", [(0, 0), (500, 0), (500, 500)])
        let b = K.trail("B", [(500, 0), (1000, 0)])
        let (s, p) = try Self.steps([a, b], [(0, 0), (500, 0), (500, 500)], shape: .oneWay)
        #expect(s.map(\.instruction) == ["Start on Trail A", "Left to stay on Trail A"])
        #expect(s[1].staysOnTrail)
        Self.checkInvariants(s, p)
    }

    @Test func loopNumbersOnlyTheTrailChange() throws {
        // A ring of two trails. A's bend at (500, 0) has no junction, so it is no step.
        let a = K.trail("A", [(0, 0), (500, 0), (500, 500)])
        let b = K.trail("B", [(500, 500), (0, 500), (0, 0)])
        let (s, p) = try Self.steps([a, b], [(0, 0), (500, 500), (0, 0)], via: ["A", "B"])
        #expect(s.map(\.instruction) == ["Start on Trail A", "Left onto Trail B"])
        #expect(abs(s[0].meters - 1000) < 10 && abs(s[1].meters - 1000) < 10)
        // The loop's last leg ends back at the first step's start.
        let first = s[0].at, last = try #require(s.last?.coords.last)
        #expect(Area.meters(first.lat, first.lon, last.lat, last.lon) < 1)
        Self.checkInvariants(s, p)
    }

    @Test func outAndBackTurnsAroundAtTheEnd() throws {
        let a = K.trail("A", [(0, 0), (800, 0)])
        let (s, p) = try Self.steps([a], [(0, 0), (800, 0), (0, 0)])
        #expect(s.map(\.instruction) == ["Start on Trail A", "Turn around on Trail A"])
        #expect(s[1].action == .turnAround && s[1].staysOnTrail)
        #expect(abs(s[0].meters - 800) < 5 && abs(s[1].meters - 800) < 5)
        Self.checkInvariants(s, p)
    }

    @Test func classifyAngles() {
        typealias B = PlanStepBuilder
        #expect(B.classify(theta: 5, others: []) == .straight)
        #expect(B.classify(theta: -10, others: [15]) == .keepLeft)
        #expect(B.classify(theta: 10, others: [-20]) == .keepRight)
        #expect(B.classify(theta: -90, others: [0]) == .left)
        #expect(B.classify(theta: 90, others: []) == .right)
        #expect(B.classify(theta: -140, others: []) == .sharpLeft)
        #expect(B.classify(theta: 140, others: []) == .sharpRight)
        #expect(B.classify(theta: 175, others: []) == .turnAround)
        #expect(B.classify(theta: 0, others: [], reversed: true) == .turnAround)
    }

    @Test func connectorStepsReadOffTrail() throws {
        // A planner run from a pin 60 m off a single trail: off-trail legs at both ends.
        let a = K.trail("A", [(0, 0), (600, 0)])
        let v = try K.view([a], start: (0, 60))
        let outcome = RoutePlanningService.planOutcome(
            PlanOutcomeRequest(network: v, trails: K.inputs([a]), completion: .init(completed: []),
                               timeLimitSeconds: K.flatSeconds(3000)))
        guard case .planned(let best, _) = outcome else { Issue.record("no plan"); return }
        let s = PlanStepBuilder.steps(plan: best, network: v.network, names: [K.key("A"): "Trail A"])
        #expect(s.first?.instruction == "Start off-trail")
        #expect(s.last?.kind == .connector)
        #expect(s.last?.instruction.hasSuffix("off-trail to the finish") == true)
        Self.checkInvariants(s, best)
    }
}
