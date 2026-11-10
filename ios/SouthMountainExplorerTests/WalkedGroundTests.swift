import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike round 4: new miles count only ground no saved track walked, on
/// trails that are not complete. Before, a trail was new in full until it was
/// marked complete.
struct WalkedGroundTests {
    typealias K = PlannerKit

    /// A track along the x axis from `x0` to `x1` (metres), one point every 3 m.
    static func track(_ x0: Double, _ x1: Double, y: Double = 0) -> [GpsPoint] {
        stride(from: x0, through: x1, by: 3).map { K.ll($0, y) }
    }

    static func coords(_ pts: [(Double, Double)]) -> [TrailNetwork.Coordinate] {
        K.line(pts).map { TrailNetwork.Coordinate(lat: $0[0], lon: $0[1]) }
    }

    // MARK: - Walked share of a line

    @Test func noTracksMeansAllNew() {
        #expect(WalkedGround.none.isEmpty)
        #expect(WalkedGround.none.unwalkedFraction(of: Self.coords([(0, 0), (1000, 0)])) == 1)
        #expect(WalkedGround(paths: [[]]).isEmpty)
    }

    @Test func partlyWalkedLineCountsOnlyTheRest() {
        let line = Self.coords([(0, 0), (1000, 0)])
        let walked = WalkedGround(paths: [Self.track(0, 400)])
        // 400 m walked (plus up to the 10 m buffer at its end): about 60 percent left.
        let f = walked.unwalkedFraction(of: line)
        #expect(abs(f - 0.6) < 0.02, "unwalked \(f)")
        #expect(WalkedGround(paths: [Self.track(0, 1000)]).unwalkedFraction(of: line) == 0)
    }

    @Test func shortGpsDropoutCountsAsWalkedButALongHoleDoesNot() {
        let line = Self.coords([(0, 0), (1000, 0)])
        // A 12 m gap between points: a dropout, filled.
        let dropout = WalkedGround(paths: [Self.track(0, 494) + Self.track(506, 1000)])
        #expect(dropout.unwalkedFraction(of: line) == 0)
        // An 80 m hole is unwalked ground (about 60 m once the 10 m buffers are taken off).
        let hole = WalkedGround(paths: [Self.track(0, 460) + Self.track(540, 1000)])
        let f = hole.unwalkedFraction(of: line)
        #expect(f > 0.05 && f < 0.08, "unwalked \(f)")
    }

    @Test func aTrackOnAParallelPathMoreThan10mAwayDoesNotCount() {
        let line = Self.coords([(0, 0), (1000, 0)])
        let f = WalkedGround(paths: [Self.track(0, 1000, y: 15)]).unwalkedFraction(of: line)
        #expect(f == 1)
    }

    // MARK: - New metres per edge

    @Test func newMetersPerEdgeUseCompletionThenTracks() throws {
        // A east, B west, both 1000 m from the start; B is marked complete.
        let a = K.trail("A", [(0, 0), (1000, 0)])
        let b = K.trail("B", [(0, 0), (-1000, 0)])
        let net = TrailNetwork(trails: K.inputs([a, b]))
        let lookup = TrailCompletionLookup(completed: [K.key("B")], walked: WalkedGround(paths: [Self.track(0, 500)]))
        let m = net.newMetersByEdge(lookup)
        let aNew = net.edgeIds(of: K.key("A")).reduce(0) { $0 + m[$1] }
        let bNew = net.edgeIds(of: K.key("B")).reduce(0) { $0 + m[$1] }
        #expect(abs(aNew - 500) < 20, "A new \(aNew)")
        #expect(bNew == 0)
        // No tracks: the old whole-trail answer.
        let flags = net.newMetersByEdge(TrailCompletionLookup(completed: [K.key("B")]))
        let aAll = net.edgeIds(of: K.key("A")).reduce(0) { $0 + flags[$1] }
        let aLen = net.edgeIds(of: K.key("A")).reduce(0) { $0 + net.edges[$1].lengthMeters }
        #expect(abs(aAll - aLen) < 1e-9)
        // Resolving caches the same numbers for that network.
        #expect(lookup.resolved(for: net).edgeNewMeters == m)
    }

    // MARK: - Planner, score and low value

    static func outcome(_ trails: [Trail], walked: [[GpsPoint]], limit: Double) throws -> RoutePlan? {
        let v = try K.view(trails)
        let lookup = TrailCompletionLookup(completed: [], walked: WalkedGround(paths: walked))
        switch RoutePlanningService.planOutcome(
            PlanOutcomeRequest(network: v, trails: K.inputs(trails), completion: lookup, timeLimitSeconds: limit)) {
        case .planned(let best, _): return best
        case .nothingFits(let n): return n.fallback
        case .noCandidates: return nil
        }
    }

    @Test func plannerPicksTheUnwalkedTrailOverAMostlyWalkedOne() throws {
        // Two 1000 m spurs; time for one out-and-back only. A is 90 percent walked.
        let a = K.trail("A", [(0, 0), (1000, 0)])
        let b = K.trail("B", [(0, 0), (-1000, 0)])
        let limit = K.flatSeconds(2600)
        let plan = try #require(try Self.outcome([a, b], walked: [Self.track(0, 900)], limit: limit))
        #expect(K.ids(plan) == ["B"])
        #expect(abs(plan.metrics.newMeters - 1000) < 15, "new \(plan.metrics.newMeters)")
        #expect(!plan.isLowValue)
    }

    @Test func mostlyWalkedTrailScoresOnlyItsUnwalkedPartAndIsLowValue() throws {
        let a = K.trail("A", [(0, 0), (1000, 0)])
        let plan = try #require(try Self.outcome([a], walked: [Self.track(0, 900)], limit: K.flatSeconds(2600)))
        // Out and back over A: 2000 m walked, about 90 m of it new.
        #expect(plan.metrics.newMeters > 60 && plan.metrics.newMeters < 120, "new \(plan.metrics.newMeters)")
        #expect(abs(plan.score - RouteScore.value(newMeters: plan.metrics.newMeters,
                                                  totalMeters: plan.metrics.meters, finished: plan.finished.count)) < 1e-9)
        #expect(plan.efficiency < RouteScore.lowValueEfficiency)
        #expect(plan.isLowValue)
        // Without tracks the same trail is all new and not low value.
        let fresh = try #require(try Self.outcome([a], walked: [], limit: K.flatSeconds(2600)))
        #expect(abs(fresh.metrics.newMeters - 1000) < 15)
        #expect(!fresh.isLowValue)
    }

    @Test func routeMetricsCountEachEdgesNewPartOnce() throws {
        let a = K.trail("A", [(0, 0), (1000, 0)])
        let v = try K.view([a])
        let net = v.network
        let perEdge = net.newMetersByEdge(TrailCompletionLookup(completed: [], walked: WalkedGround(paths: [Self.track(0, 500)])))
        let out = net.edgeIds(of: K.key("A")).map { RouteStep(edgeId: $0, direction: .forward) }
        let back = out.reversed().map { RouteStep(edgeId: $0.edgeId, direction: .backward) }
        let m = try #require(RouteMetrics(network: net, steps: out + back, newMetersByEdge: perEdge))
        let expected = net.edgeIds(of: K.key("A")).reduce(0) { $0 + perEdge[$1] }
        #expect(abs(m.newMeters - expected) < 1e-9)
        #expect(abs(m.newMeters - 500) < 20)
    }
}
