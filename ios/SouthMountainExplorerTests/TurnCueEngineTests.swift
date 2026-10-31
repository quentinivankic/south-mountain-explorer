import Foundation
import Testing
@testable import SouthMountainExplorer

struct TurnCueEngineTests {
    typealias F = CueFixtures

    private func mainWalk(from: Double = 10, to: Double = 380, noise: Double = 0, seed: UInt64 = 1, acc: Double = 5) -> [TurnCueFix] {
        F.walk(F.Path(F.mainPts), from: from, to: to, acc: acc, noise: noise, seed: seed)
    }

    // MARK: Approach

    @Test func forkBranchingLeftCuesKeepRight() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
        let hits = F.run(&e, mainWalk())
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .keepRight })
        let far = hits[0].event, near = hits[1].event
        #expect((50...100).contains(far.distanceMeters ?? 0))
        #expect((0...30).contains(near.distanceMeters ?? 99))
        #expect(far.spoken.hasPrefix("In "))
        #expect(far.spoken.hasSuffix("meters, keep right to stay on Main Trail"))
        #expect(near.spoken == "Keep right")
        #expect(hits[0].index < hits[1].index)
    }

    @Test func forkBranchingRightCuesKeepLeft() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 40)])
        let hits = F.run(&e, mainWalk())
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .keepLeft })
        #expect(hits[1].event.spoken == "Keep left")
    }

    @Test func walkingTheOtherWayAlsoCues() {
        // Southbound, a branch leaving to the south-west is on the hiker's
        // right, so the selected path is the "keep left" one.
        var e = F.engine(F.main(), others: [F.fork(bearing: 220)])
        let hits = F.run(&e, F.walk(F.Path([(0, 600), (0, 0)]), from: 10, to: 380))
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .keepLeft })
    }

    @Test func branchMergingFromBehindIsNotCued() {
        // Northbound, a branch leaving to the north-west is ahead (cued);
        // the same branch seen southbound merges from behind (silent).
        var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
        let hits = F.run(&e, F.walk(F.Path([(0, 600), (0, 0)]), from: 10, to: 380))
        #expect(hits.isEmpty)
    }

    @Test func bendRightWithStraightAlternativeCuesTurnRight() {
        let selected = F.trail("bend", "Bend Trail", [(0, 0), (0, 300), (300, 300)])
        let straight = F.trail("straight", "Straight On", [(0, 300), (0, 500)])
        var e = F.engine(selected, others: [straight])
        let hits = F.run(&e, F.walk(F.Path([(0, 0), (0, 300), (300, 300)]), from: 10, to: 360))
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .turnRight })
        #expect(hits[0].event.spoken.hasSuffix("turn right to stay on Bend Trail"))
    }

    // MARK: No cue

    @Test func straightThroughCrossingGivesNoCue() {
        let cross = F.trail("cross", "Cross Trail", [(-100, 300), (100, 300)])
        var e = F.engine(F.main(), others: [cross])
        #expect(e.route.junctions.count == 1)
        #expect(F.run(&e, mainWalk()).isEmpty)
    }

    @Test func bendWithoutAnyBranchGivesNoCue() {
        let bend = F.trail("bend", "Bend Trail", [(0, 0), (0, 300), (300, 300)])
        var e = F.engine(bend, others: [])
        #expect(F.run(&e, F.walk(F.Path([(0, 0), (0, 300), (300, 300)]), from: 10, to: 560)).isEmpty)
    }

    @Test func trailEndingAtAJunctionGivesNoCue() {
        let short = F.trail("short", "Short Trail", [(0, 0), (0, 300)])
        let cross = F.trail("cross", "Cross", [(-100, 300), (100, 300)])
        var e = F.engine(short, others: [cross])
        #expect(F.run(&e, F.walk(F.Path([(0, 0), (0, 300)]), from: 10, to: 300)).isEmpty)
    }

    @Test func standingStillAtTheTrailheadGivesNoCue() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 320, at: (0, 40))])
        let still = (0..<40).map { F.fix((0, 5), t: F.t0 + Double($0)) }
        #expect(F.run(&e, still).isEmpty)
    }

    @Test func parallelOverlapCuesOnlyAtTheExit() {
        let other = F.trail("par", "Parallel", [(-60, 140), (0, 200), (0, 320), (60, 380)])
        var e = F.engine(F.main(), others: [other])
        #expect(e.route.junctions.count == 2)
        let hits = F.run(&e, mainWalk(to: 420))
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        // Only the exit divergence (north end of the overlap) is cued.
        #expect(hits.allSatisfy { $0.event.symbol == .keepLeft })
        #expect((hits[0].event.distanceMeters ?? 0) > 50)
    }

    // MARK: Switchbacks

    @Test func switchbackNeighbourLegDoesNotTeleportTheCursorOrCueEarly() {
        // Three east/west legs 12 m apart, 100 m each, joined by 12 m risers.
        let zig: [F.XY] = [(0, 0), (100, 0), (100, 12), (0, 12), (0, 24), (100, 24)]
        let selected = F.trail("zig", "Zig Trail", zig)
        // Junction on the third leg at (50,24): other trail leaves at 50 deg.
        let other = F.trail("up", "Up Trail", [(50, 24), F.offset((50, 24), bearing: 50, meters: 120)])
        var e = F.engine(selected, others: [other])
        #expect(e.route.junctions.count == 1)
        let jArc = e.route.junctions[0].arc
        #expect(abs(jArc - 274) < 8)

        let path = F.Path(zig)
        var fixes = F.walk(path, from: 5, to: 330)
        // Wobble: pull a few mid-leg-one fixes 7 m toward leg two.
        for i in [30, 31, 32, 45] where i < fixes.count {
            let s = 5 + 1.4 * Double(i)
            let p = path.position(at: s)
            fixes[i] = F.fix((p.x, p.y + 7), t: fixes[i].timestamp)
        }
        var hits: [F.Hit] = []
        for (i, f) in fixes.enumerated() {
            for ev in e.ingest(f, now: f.timestamp) { hits.append(.init(index: i, event: ev)) }
            let s = min(5 + 1.4 * Double(i), path.length)
            if let c = e.cursorArc, i > 8 {
                // Cursor never leaves the true along-trail position.
                #expect(abs(c - s) < 20, "cursor \(c) vs true \(s) at \(i)")
            }
        }
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        // First cue only once the along-trail distance is within far range,
        // although the leg above is 24 m away in a straight line from fix 0.
        let sAtFar = 5 + 1.4 * Double(hits[0].index)
        #expect(jArc - sAtFar <= 105 && jArc - sAtFar >= 45)
    }

    // MARK: Jitter

    @Test func gpsJitterNearAJunctionFiresEachCueExactlyOnce() {
        for seed in 1...25 {
            var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
            let hits = F.run(&e, mainWalk(to: 400, noise: 12, seed: UInt64(seed), acc: 10))
            let kinds = hits.map(\.event.kind)
            #expect(kinds == [.approach, .atJunction], "seed \(seed): \(kinds)")
            #expect(hits.allSatisfy { $0.event.symbol == .keepRight }, "seed \(seed)")
        }
    }

    // MARK: Direction

    @Test func reversalMidTrailResetsAndCuesTheOtherForkFromTheOtherSide() {
        // Fork A (north-west at 300) is ahead northbound; fork B (south-west
        // at 150) is ahead southbound.
        let a = F.fork(bearing: 320)
        let b = F.fork(bearing: 220, at: (0, 150), id: "b", name: "B Trail")
        var e = F.engine(F.main(), others: [a, b])
        var hits = F.run(&e, mainWalk(to: 380))
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .keepRight })
        let back = F.walk(F.Path([(0, 380), (0, 0)]), from: 0, to: 380, startT: F.t0 + 400)
        hits = F.run(&e, back)
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .keepLeft })
    }

    @Test func loopApproachAcrossTheSeamCues() {
        // Square loop 200 m a side, seam at (0,0). The fork sits 100 m past
        // the seam; the hiker starts 150 m before it and walks across.
        let loop: [F.XY] = [(0, 0), (0, 200), (200, 200), (200, 0), (0, 0)]
        let selected = F.trail("loop", "Loop Trail", loop)
        let other = F.trail("spur", "Spur", [(0, 100), F.offset((0, 100), bearing: 315, meters: 120)])
        var e = F.engine(selected, others: [other])
        #expect(e.route.chains[0].isLoop)
        let path = F.Path(loop + Array(loop.dropFirst()))
        let hits = F.run(&e, F.walk(path, from: 650, to: 960))
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .keepRight })
    }

    // MARK: Off trail

    private let offPath: [F.XY] = [(0, 10), (0, 200), (60, 200), (60, 260), (0, 260), (0, 350)]

    @Test func leavingAndReturningCuesOffTrailThenBackOnce() {
        var e = F.engine(F.main(), others: [])
        let hits = F.run(&e, F.walk(F.Path(offPath), from: 0, to: F.Path(offPath).length))
        #expect(hits.map(\.event.kind) == [.offTrail, .backOnTrail])
        #expect(hits[0].event.spoken == "You may be off Main Trail")
        #expect(hits[1].event.spoken == "Back on Main Trail")
        #expect(e.advisory == nil)
    }

    @Test func standingStillOffTrailDoesNotCue() {
        var e = F.engine(F.main(), others: [])
        var fixes = F.walk(F.Path([(0, 10), (0, 200)]), from: 0, to: 190)
        let last = fixes.last!.timestamp
        // Appears 60 m off the trail and stays put (1.5 m jitter) for 80 s.
        fixes += (1...80).map { k in
            F.fix((60 + (k % 2 == 0 ? 1.5 : -1.5), 200), t: last + Double(k))
        }
        #expect(F.run(&e, fixes).isEmpty)
    }

    @Test func startingFarFromTheTrailStaysSilent() {
        var e = F.engine(F.main(), others: [])
        let far = F.walk(F.Path([(300, 100), (300, 300), (250, 300)]), from: 0, to: 400)
        #expect(F.run(&e, far).isEmpty)
    }

    @Test func poorAccuracyWidensTheOffTrailBand() {
        var e = F.engine(F.main(), others: [])
        // 60 m away is inside T_off for a 40 m accuracy fix (T_off = 95).
        let on = F.walk(F.Path([(0, 10), (0, 200)]), from: 0, to: 190, acc: 40)
        var fixes = on
        let lastT = on.last!.timestamp
        fixes += F.walk(F.Path([(60, 200), (60, 300)]), from: 0, to: 100, acc: 40, startT: lastT + 1)
        #expect(F.run(&e, fixes).isEmpty)
    }

    // MARK: GPS gap / degraded

    @Test func gapSuppressesCuesAndWarmupDelaysThemAfterward() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
        let path = F.Path(F.mainPts)
        // Walk to 150 m before the junction, then lose signal for 25 s.
        var hits = F.run(&e, F.walk(path, from: 10, to: 145))
        #expect(hits.isEmpty)
        // Last fix was at t0+96 (s=144); 29 s of silence moves the hiker ~40 m.
        let after = F.walk(path, from: 185, to: 295, startT: F.t0 + 96 + 29)
        hits = F.run(&e, after)
        // First five post-gap fixes are silent (warm-up), then the far cue still comes.
        #expect(hits.first.map { $0.index >= 5 } ?? false)
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
    }

    @Test func junctionPassedDuringAGapIsNeverAnnouncedLate() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
        let path = F.Path(F.mainPts)
        F.run(&e, F.walk(path, from: 10, to: 145))
        let after = F.walk(path, from: 340, to: 420, startT: F.t0 + 96 + 40)
        #expect(F.run(&e, after).isEmpty)
    }

    @Test func badAccuracyAndStaleFixesAreSilentAndWeakFiresOncePerEpisode() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
        let path = F.Path(F.mainPts)
        // 60 s of good tracking, so a weak notice is allowed.
        F.run(&e, F.walk(path, from: 10, to: 220))
        var t = F.t0 + 151
        var hits: [F.Hit] = []
        // 30 s of 80 m accuracy while walking through the cue zone.
        for k in 0..<30 {
            let p = path.position(at: 230 + 1.4 * Double(k))
            let f = F.fix(p, t: t + Double(k), acc: 80)
            for ev in e.ingest(f, now: f.timestamp) { hits.append(.init(index: k, event: ev)) }
        }
        #expect(hits.map(\.event.kind) == [.gpsWeak])
        #expect(hits[0].event.spoken == "GPS signal weak")
        #expect(e.advisory == nil)
        // Invalid accuracy and a stale fix are silent too.
        t += 30
        let invalid = F.fix(path.position(at: 275), t: t, acc: -1)
        #expect(e.ingest(invalid, now: t).isEmpty)
        let stale = F.fix(path.position(at: 277), t: t + 1, acc: 5)
        #expect(e.ingest(stale, now: t + 1 + 30).isEmpty)
    }

    @Test func staleStreamIsNoticedByTickAndWeakRateLimited() {
        var e = F.engine(F.main(), others: [])
        let path = F.Path(F.mainPts)
        let fixes = F.walk(path, from: 10, to: 100)
        F.run(&e, fixes)
        let last = fixes.last!.timestamp
        #expect(e.tick(now: last + 5).isEmpty)
        #expect(e.tick(now: last + 12).isEmpty)
        #expect(e.tick(now: last + 17).map(\.kind) == [.gpsWeak])
        #expect(e.tick(now: last + 40).isEmpty)
    }

    @Test func invalidInputIsIgnored() {
        var e = F.engine(F.main(), others: [])
        let bad = [
            TurnCueFix(lat: .nan, lon: -112, timestamp: F.t0, horizontalAccuracy: 5, speed: nil),
            TurnCueFix(lat: 95, lon: -112, timestamp: F.t0, horizontalAccuracy: 5, speed: nil),
            TurnCueFix(lat: 33.3, lon: -112, timestamp: .infinity, horizontalAccuracy: 5, speed: nil),
            TurnCueFix(lat: 33.3, lon: -112, timestamp: F.t0 + 100, horizontalAccuracy: 5, speed: nil),
        ]
        for f in bad { #expect(e.ingest(f, now: F.t0).isEmpty) }
        #expect(e.advisory == nil)
    }

    // MARK: Phrasing through the engine

    @Test func metricAndImperialPhrasingForTheSameScenario() {
        let others = [F.fork(bearing: 320)]
        var metric = F.engine(F.main(), others: others, units: .metric)
        var imperial = F.engine(F.main(), others: others, units: .imperial)
        let m = F.run(&metric, mainWalk())[0].event.spoken
        let i = F.run(&imperial, mainWalk())[0].event.spoken
        #expect(m.contains(" meters, keep right to stay on Main Trail"))
        #expect(i.contains(" feet, keep right to stay on Main Trail"))
        let meters = Int(m.split(separator: " ")[1])!
        let feet = Int(i.split(separator: " ")[1])!
        #expect(meters % 10 == 0 && feet % 50 == 0)
        #expect(abs(Double(feet) - Double(meters) * 3.28084) <= 30)
    }

    @Test func unnamedTrailOmitsStayOn() {
        let unnamed = F.trail("unnamed-way-1", "", F.mainPts)
        var e = F.engine(unnamed, others: [F.fork(bearing: 320)])
        let hits = F.run(&e, mainWalk())
        #expect(hits[0].event.spoken.hasSuffix(", keep right"))
        #expect(!hits[0].event.spoken.contains("stay on"))
    }

    @Test func advisoryTracksTheCuedJunctionAndClearsAfterPassing() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
        let fixes = mainWalk(to: 380)
        var sawAdvisory = false
        for f in fixes {
            _ = e.ingest(f, now: f.timestamp)
            if let a = e.advisory, a.symbol == .keepRight {
                sawAdvisory = true
                #expect(a.headline == "Keep right")
            }
        }
        #expect(sawAdvisory)
        #expect(e.advisory == nil)
    }
}
