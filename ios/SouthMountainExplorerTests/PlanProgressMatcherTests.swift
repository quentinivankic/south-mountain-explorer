import Foundation
import Testing
@testable import SouthMountainExplorer

/// Plan a Hike Release B task 2: the forward-only position tracker, replayed
/// over synthetic walks with GPS noise.
struct PlanProgressMatcherTests {
    typealias K = PlannerKit
    typealias Kind = PlanProgressMatcher.Update.Kind

    /// Deterministic noise: SplitMix64 plus Box-Muller.
    struct Noise {
        var state: UInt64
        mutating func uniform() -> Double {
            state &+= 0x9E37_79B9_7F4A_7C15
            var z = state
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            z ^= z >> 31
            return Double(z >> 11) / Double(UInt64(1) << 53)
        }
        mutating func gauss(_ sigma: Double) -> Double {
            let u = max(uniform(), 1e-12), v = uniform()
            return sigma * sqrt(-2 * log(u)) * cos(2 * .pi * v)
        }
    }

    /// Turns walked positions into one fix per second with noise.
    struct Walker {
        /// Position (metres in the walker's frame) to lat/lon.
        let toLatLon: (LocalPt) -> (lat: Double, lon: Double)
        var t: Double = 1_000_000
        var noise = Noise(state: 42)
        var sigma = 3.0
        var accuracy = 5.0
        var speed = 1.4
        private(set) var fixes: [TurnCueFix] = []
        /// The true position of each fix.
        private(set) var truth: [LocalPt] = []

        /// Walker in the PlannerKit frame (`K.ll`).
        static func kit() -> Walker {
            Walker { p in let c = K.ll(p.x, p.y); return (c[0], c[1]) }
        }

        /// Walker in a route's own frame.
        static func route(_ r: PlanGuideRoute) -> Walker {
            Walker { p in
                let m = TurnCueProjector.metersPerDegree, o = r.projector
                return (o.originLat + p.y / m, o.originLon + p.x / (m * cos(o.originLat * .pi / 180)))
            }
        }

        mutating func at(_ p: LocalPt) {
            let q = LocalPt(x: p.x + noise.gauss(sigma), y: p.y + noise.gauss(sigma))
            let c = toLatLon(q)
            fixes.append(TurnCueFix(lat: c.lat, lon: c.lon, timestamp: t, horizontalAccuracy: accuracy, speed: speed))
            truth.append(p)
            t += 1
        }

        /// Walks the polyline at `speed`, one fix per second, from its first point.
        mutating func walk(_ pts: [(Double, Double)]) {
            let line = pts.map { LocalPt(x: $0.0, y: $0.1) }
            let cum = TurnCueGeometry.cumulative(line)
            var s = 0.0
            while s <= cum.last! {
                at(Self.point(line, cum, s))
                s += speed
            }
        }

        /// Stands at `p` for `seconds`.
        mutating func stand(_ p: (Double, Double), seconds: Int) {
            for _ in 0..<seconds { at(LocalPt(x: p.0, y: p.1)) }
        }

        static func point(_ line: [LocalPt], _ cum: [Double], _ s: Double) -> LocalPt {
            let i = max(0, min(cum.count - 2, (cum.lastIndex { $0 <= s } ?? 0)))
            let span = cum[i + 1] - cum[i]
            let t = span > 0 ? min(1, (s - cum[i]) / span) : 0
            return LocalPt(x: line[i].x + (line[i + 1].x - line[i].x) * t, y: line[i].y + (line[i + 1].y - line[i].y) * t)
        }
    }

    /// What a replay did.
    struct Trace {
        /// Current step after each fix, consecutive repeats removed.
        var steps: [Int] = []
        var kinds: [Kind: Int] = [:]
        /// Cursor and step after each fix.
        var cursors: [Double] = []
        var stepAt: [Int] = []
        func count(_ k: Kind) -> Int { kinds[k] ?? 0 }
        var cursorNeverMovedBack: Bool { zip(cursors, cursors.dropFirst()).allSatisfy { $0 <= $1 } }
    }

    static func run(_ m: inout PlanProgressMatcher, _ fixes: [TurnCueFix], into trace: inout Trace) {
        if trace.steps.isEmpty { trace.steps = [m.stepIndex] }
        for f in fixes {
            let before = m.stepIndex
            let u = m.ingest(f)
            trace.kinds[u.kind, default: 0] += 1
            if trace.steps.last != m.stepIndex { trace.steps.append(m.stepIndex) }
            #expect(u.stepChanged == (before != m.stepIndex))
            trace.cursors.append(m.cursorMeters)
            trace.stepAt.append(m.stepIndex)
        }
    }

    static func run(_ m: inout PlanProgressMatcher, _ fixes: [TurnCueFix]) -> Trace {
        var t = Trace()
        run(&m, fixes, into: &t)
        return t
    }

    static func matcher(_ trails: [Trail], _ pts: [(Double, Double)],
                        shape: RoutePlanRequest.Shape = .loop) throws -> (PlanProgressMatcher, [PlanStep]) {
        let (s, _) = try PlanStepsTests.steps(trails, pts, shape: shape)
        let r = try #require(PlanGuideRoute(steps: s))
        return (PlanProgressMatcher(route: r), s)
    }

    // MARK: - Out and back

    @Test func outAndBackFollowsEveryStepInOrderAndKnowsTheWayBack() throws {
        let (r, s) = try PlanGuideRouteTests.outAndBack()
        var m = PlanProgressMatcher(route: r)
        var w = Walker.kit()
        w.walk([(0, 0), (500, 0), (500, 300), (500, 0), (0, 0)])
        let t = Self.run(&m, w.fixes)
        #expect(t.steps == Array(s.indices))
        #expect(t.count(.offExpected) == 0)
        #expect(t.count(.skippedAhead) == 0)
        #expect(t.cursorNeverMovedBack)
        #expect(m.cursorMeters > r.length - 20)
        // Same ground, different pass: halfway up B on the way out is step 2,
        // halfway down B on the way back is step 3; A on the way back is step 4.
        let i = w.truth.indices
        let upB = try #require(i.first { abs(w.truth[$0].y - 150) < 1 && $0 < w.truth.count / 2 })
        let downB = try #require(i.last { abs(w.truth[$0].y - 150) < 1 })
        let backA = try #require(i.last { abs(w.truth[$0].x - 250) < 1 })
        #expect(t.stepAt[upB] == 1)
        #expect(t.stepAt[downB] == 2)
        #expect(t.stepAt[backA] == 3)
        // The cursor's point stays close to where you are. Near the dead end
        // the way back opens within the 25 m gate, so the distance along the
        // line may lead by up to twice that while the point stays near you.
        for (k, c) in t.cursors.enumerated() where k % 10 == 0 {
            let ll = K.ll(w.truth[k].x, w.truth[k].y)
            #expect(TurnCueGeometry.distance(r.point(at: c), r.local(lat: ll[0], lon: ll[1])) < 25, "fix \(k)")
            let along = abs(c - Double(k) * w.speed)
            #expect(along < 25 || abs(Double(k) * w.speed - r.markers[2].startMeters) < 25, "fix \(k)")
        }
    }

    // MARK: - Loop with a shared stem walked both ways

    @Test func loopWithSharedStemWalkedBothWays() throws {
        let trails = [K.trail("S", [(0, 0), (0, 300)]), K.trail("A", [(0, 300), (0, 800)]),
                      K.trail("B", [(0, 800), (500, 800)]), K.trail("C", [(500, 800), (500, 300)]),
                      K.trail("D", [(500, 300), (0, 300)])]
        let walk: [(Double, Double)] = [(0, 0), (0, 300), (0, 800), (500, 800), (500, 300), (0, 300), (0, 0)]
        var (m, s) = try Self.matcher(trails, walk)
        var w = Walker.kit()
        w.walk(walk)
        let t = Self.run(&m, w.fixes)
        #expect(t.steps == Array(s.indices))
        #expect(t.count(.offExpected) == 0)
        #expect(t.count(.skippedAhead) == 0)
        // Halfway up the stem is step 1; halfway down it, the last step.
        let up = try #require(w.truth.indices.first { abs(w.truth[$0].y - 150) < 1 })
        let down = try #require(w.truth.indices.last { abs(w.truth[$0].y - 150) < 1 && w.truth[$0].x == 0 })
        #expect(t.stepAt[up] == 0)
        #expect(t.stepAt[down] == s.count - 1)
    }

    // MARK: - Repeated pass (two laps)

    @Test func twoLapsOfALoopFollowTheSecondLapToo() throws {
        let trails = [K.trail("A", [(0, 0), (500, 0)]), K.trail("B", [(500, 0), (500, 500)]),
                      K.trail("C", [(500, 500), (0, 500)]), K.trail("D", [(0, 500), (0, 0)])]
        let lap: [(Double, Double)] = [(0, 0), (500, 0), (500, 500), (0, 500), (0, 0)]
        let walk = lap + lap.dropFirst()
        var (m, s) = try Self.matcher(trails, walk)
        #expect(s.count >= 8)
        var w = Walker.kit()
        w.walk(walk)
        let t = Self.run(&m, w.fixes)
        #expect(t.steps == Array(s.indices))
        #expect(t.count(.offExpected) == 0)
        #expect(t.count(.skippedAhead) == 0)
        #expect(m.cursorMeters > m.route.length - 20)
        // Halfway along A on lap 1 vs lap 2: different steps, about 2000 m apart.
        let a1 = try #require(w.truth.indices.first { abs(w.truth[$0].x - 250) < 1 && w.truth[$0].y == 0 })
        let a2 = try #require(w.truth.indices.last { abs(w.truth[$0].x - 250) < 1 && w.truth[$0].y == 0 })
        #expect(t.stepAt[a1] < t.stepAt[a2])
        #expect(abs(t.cursors[a2] - t.cursors[a1] - 2000) < 30)
    }

    // MARK: - Standing still at a junction

    @Test func standingStillAtAJunctionNeverFlipsTheStep() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var m = PlanProgressMatcher(route: r)
        var w = Walker.kit()
        w.walk([(0, 0), (498, 0)])
        var t = Self.run(&m, w.fixes)
        #expect(m.stepIndex == 0)
        let n = w.fixes.count
        w.sigma = 2.5
        w.stand((500, 0), seconds: 180)
        Self.run(&m, Array(w.fixes[n...]), into: &t)
        #expect(t.stepAt[n...].allSatisfy { $0 == 0 })
        #expect(m.cursorMeters < r.markers[1].startMeters + PlanProgressMatcher.Config().stepSwitchMeters)
        // Walking on up B moves to step 2 once about 10 m past the junction.
        let k = w.fixes.count
        w.sigma = 3
        w.walk([(500, 0), (500, 60)])
        Self.run(&m, Array(w.fixes[k...]), into: &t)
        let flip = try #require(t.stepAt.indices.first { $0 >= k && t.stepAt[$0] == 1 })
        #expect(w.truth[flip].y > 3 && w.truth[flip].y < 25)
    }

    // MARK: - A 60 s GPS gap

    @Test func sixtySecondGpsGapCatchesUpWithoutSkippingAStep() throws {
        let a = K.trail("A", [(0, 0), (1000, 0)]), b = K.trail("B", [(1000, 0), (1000, -400)])
        let c = K.trail("C", [(1000, 0), (1500, 0)])
        var (m, s) = try Self.matcher([a, b, c], [(0, 0), (1000, 0), (1000, -400)], shape: .oneWay)
        var w = Walker.kit()
        w.walk([(0, 0), (300, 0)])
        var t = Self.run(&m, w.fixes)
        let before = m.cursorMeters
        // No fixes for 60 s while you walk on 84 m.
        w.t += 59
        let n = w.fixes.count
        w.walk([(384, 0), (1000, 0), (1000, -400)])
        Self.run(&m, Array(w.fixes[n...]), into: &t)
        #expect(t.cursors[n + 2] > before + 70)
        #expect(abs(t.cursors[n + 2] - (384 + 2 * w.speed)) < 15)
        #expect(t.steps == Array(s.indices))
        #expect(t.count(.offExpected) == 0)
        #expect(t.count(.skippedAhead) == 0)
    }

    // MARK: - An 80 m wrong turn and back

    @Test func eightyMetreWrongTurnAndBackKeepsTheStep() throws {
        let a = K.trail("A", [(0, 0), (500, 0)]), b = K.trail("B", [(500, 0), (500, 500)])
        let c = K.trail("C", [(500, 0), (1000, 0)])
        var (m, s) = try Self.matcher([a, b, c], [(0, 0), (500, 0), (500, 500)], shape: .oneWay)
        #expect(s.map(\.action) == [.start, .left])
        var w = Walker.kit()
        w.walk([(0, 0), (500, 0), (580, 0), (500, 0), (500, 500)])
        let t = Self.run(&m, w.fixes)
        #expect(t.count(.offExpected) > 0)
        #expect(t.count(.skippedAhead) == 0)
        #expect(t.steps == [0, 1])
        // Still step 1 until back at the junction and on up B.
        let farthest = try #require(w.truth.indices.max { w.truth[$0].x < w.truth[$1].x })
        let back = try #require(w.truth.indices.first { $0 > farthest && w.truth[$0].y > 0 })
        #expect(t.stepAt[...back].allSatisfy { $0 == 0 })
        #expect(m.cursorMeters > m.route.length - 20)
    }

    // MARK: - A shortcut that skips ahead

    static let square: [Trail] = [K.trail("A", [(0, 0), (600, 0)]), K.trail("B", [(600, 0), (600, 600)]),
                                  K.trail("C", [(600, 600), (0, 600)]), K.trail("D", [(0, 600), (0, 0)])]
    static let squareWalk: [(Double, Double)] = [(0, 0), (600, 0), (600, 600), (0, 600), (0, 0)]

    @Test func shortcutJoiningLaterSkipsAheadOnceAndSilently() throws {
        var (m, s) = try Self.matcher(Self.square, Self.squareWalk)
        #expect(s.count == 4)
        var w = Walker.kit()
        w.walk([(0, 0), (300, 0), (300, 600), (0, 600), (0, 0)])
        let t = Self.run(&m, w.fixes)
        #expect(t.count(.skippedAhead) == 1)
        #expect(t.count(.offExpected) > 0)
        #expect(t.steps == [0, 2, 3])
        // The jump lands within about 50 m of where you joined C.
        let jump = try #require(t.cursors.indices.first { t.stepAt[$0] == 2 })
        #expect(abs(t.cursors[jump] - 1500) < 60)
        #expect(m.cursorMeters > m.route.length - 20)
    }

    // MARK: - Walking a leg backward

    @Test func walkingALegBackwardHoldsTheStepAndWaits() throws {
        var (m, s) = try Self.matcher(Self.square, Self.squareWalk)
        var w = Walker.kit()
        w.walk([(0, 0), (600, 0), (600, 300), (600, 100), (600, 600), (0, 600), (0, 0)])
        let t = Self.run(&m, w.fixes)
        #expect(t.steps == Array(s.indices))
        #expect(t.count(.skippedAhead) == 0)
        #expect(t.count(.offExpected) > 0)
        #expect(t.cursorNeverMovedBack)
        // On B from the turn back down to y = 100 and up again to y = 300: step 2, cursor held.
        let top = try #require(w.truth.indices.first { abs(w.truth[$0].y - 300) < 1 && w.truth[$0].x == 600 })
        let back = try #require(w.truth.indices.first { $0 > top + 50 && abs(w.truth[$0].y - 300) < 1 && w.truth[$0].x == 600 })
        #expect(t.stepAt[top...back].allSatisfy { $0 == 1 })
        #expect(t.cursors[back] - t.cursors[top] < 15)
    }

    // MARK: - Back and Next

    @Test func nextWaitsForYouAndBackFollowsYouForwardAgain() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var m = PlanProgressMatcher(route: r)
        var w = Walker.kit()
        w.walk([(0, 0), (200, 0)])
        var t = Self.run(&m, w.fixes)
        #expect(m.stepIndex == 0)
        // Next at 200 m: the guide moves to step 2 and waits at the junction.
        m.move(toStep: 1)
        #expect(m.stepIndex == 1 && m.cursorMeters == r.markers[1].startMeters)
        var n = w.fixes.count
        w.walk([(200, 0), (500, 0), (500, 100)])
        Self.run(&m, Array(w.fixes[n...]), into: &t)
        let junction = try #require(w.truth.indices.first { $0 >= n && w.truth[$0].x >= 499 })
        #expect(t.stepAt[n...junction].allSatisfy { $0 == 1 })
        #expect(t.cursors[n..<(junction - 15)].allSatisfy { $0 == r.markers[1].startMeters })
        #expect(t.cursors[n...junction].allSatisfy { $0 < r.markers[1].startMeters + 10 })
        #expect(m.cursorMeters > 590 && m.stepIndex == 1)
        // Back to step 1 while on B: the guide goes back, then catches up
        // with you by skipping forward again within about 30 s.
        m.move(toStep: 0)
        #expect(m.stepIndex == 0 && m.cursorMeters == 0)
        n = w.fixes.count
        w.walk([(500, 100), (500, 160)])
        Self.run(&m, Array(w.fixes[n...]), into: &t)
        #expect(m.stepIndex == 1)
        #expect(abs(m.cursorMeters - 660) < 20)
    }

    // MARK: - Fixes not used

    @Test func poorOrOutOfOrderFixesAreIgnored() throws {
        let (r, _) = try PlanGuideRouteTests.outAndBack()
        var m = PlanProgressMatcher(route: r)
        var w = Walker.kit()
        w.sigma = 0
        w.walk([(0, 0), (100, 0)])
        _ = Self.run(&m, w.fixes)
        let c = m.cursorMeters
        let p = K.ll(300, 0)
        let bad = TurnCueFix(lat: p[0], lon: p[1], timestamp: w.t, horizontalAccuracy: 65, speed: 1.4)
        let invalid = TurnCueFix(lat: p[0], lon: p[1], timestamp: w.t, horizontalAccuracy: -1, speed: 1.4)
        let old = TurnCueFix(lat: p[0], lon: p[1], timestamp: w.t - 50, horizontalAccuracy: 5, speed: 1.4)
        for f in [bad, invalid, old] { #expect(m.ingest(f).kind == .ignored) }
        #expect(m.cursorMeters == c)
    }
}
