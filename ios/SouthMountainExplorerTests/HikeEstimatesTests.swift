import Foundation
import CoreLocation
import Testing
@testable import SouthMountainExplorer

/// Pure, deterministic tests for the mid-hike "Remaining" / "Back" estimates:
/// PaceModel (Tobler + personal factor), TrailProgress, HikeEstimator,
/// BackRoute and the text/formatting helpers. No clock, no location service.
struct HikeEstimatesTests {
    // MARK: - Synthetic world (local meters <-> lat/lon)

    static let lat0 = 33.3, lon0 = -112.0
    static let k = TrailGeometry.metersPerDegree
    static let baseMs = 1_700_000_000_000.0

    static func ll(_ x: Double, _ y: Double) -> [Double] {
        [lat0 + y / k, lon0 + x / (k * cos(lat0 * .pi / 180))]
    }

    /// A polyline in local meters with cumulative arc length.
    struct Route {
        let pts: [(x: Double, y: Double)]
        let cum: [Double]
        init(_ pts: [(Double, Double)]) {
            self.pts = pts.map { (x: $0.0, y: $0.1) }
            var c = [0.0]
            for i in 1..<pts.count {
                c.append(c[i - 1] + ((pts[i].0 - pts[i - 1].0) * (pts[i].0 - pts[i - 1].0)
                                     + (pts[i].1 - pts[i - 1].1) * (pts[i].1 - pts[i - 1].1)).squareRoot())
            }
            cum = c
        }
        var total: Double { cum.last! }
        func at(_ s: Double) -> (x: Double, y: Double) {
            let s = min(total, max(0, s))
            var i = 0
            while i < cum.count - 2 && cum[i + 1] < s { i += 1 }
            let f = (s - cum[i]) / max(1e-9, cum[i + 1] - cum[i])
            return (pts[i].x + (pts[i + 1].x - pts[i].x) * f, pts[i].y + (pts[i + 1].y - pts[i].y) * f)
        }
    }

    /// Straight trail heading north.
    static func straight(_ meters: Double) -> Route { Route([(0, 0), (0, meters)]) }

    /// Walk `route` from arc `a` to `b` (either direction). `speed(s)` m/s,
    /// `alt(s)` meters. Returns points and the end timestamp (ms).
    static func walk(_ route: Route, from a: Double, to b: Double, t0: Double = baseMs,
                     speed: (Double) -> Double = { _ in 1.4 },
                     alt: (Double) -> Double? = { _ in nil },
                     step: Double = 2.8) -> (pts: [GpsPoint], t: Double) {
        var out: [GpsPoint] = []
        var s = a, t = t0
        func emit() {
            let p = route.at(s)
            let c = ll(p.x, p.y)
            if let h = alt(s) { out.append([c[0], c[1], t, h]) } else { out.append([c[0], c[1], t]) }
        }
        emit()
        let dir: Double = b >= a ? 1 : -1
        while (b - s) * dir > 1e-9 {
            let d = min(step, abs(b - s))
            let v = max(0.05, speed(s))
            s += dir * d
            t += d / v * 1000
            emit()
        }
        return (out, t)
    }

    static func trail(_ route: Route, id: String = "t", name: String = "Test Trail",
                      profile: ((Double) -> Double)? = nil) -> Trail {
        let seg = route.pts.map { ll($0.x, $0.y) }
        var ft: [Int]? = nil
        if let profile {
            let n = Int(route.total / 10) + 1
            ft = (0..<n).map { Int((profile(route.total * Double($0) / Double(n - 1)) / 0.3048).rounded()) }
        }
        return Trail(id: id, name: name, distanceMi: route.total / 1609.344, difficulty: .easy,
                     segments: [seg], gainFt: nil, profileFt: ft)
    }

    static func date(_ ms: Double) -> Date { Date(timeIntervalSince1970: ms / 1000) }

    /// Run the estimator once over a full path, fix fresh at the last sample.
    static func estimate(_ path: [GpsPoint], trail: Trail?, nowOffset: Double = 0) -> HikeEstimates {
        var e = HikeEstimator()
        let now = date(path.last![2] + nowOffset)
        return e.update(path: path, geometry: trail.flatMap { TrailGeometry(trail: $0) },
                        now: now, lastFixAt: now)
    }

    static let flatSpeed = PaceModel.tobler(grade: 0)

    // MARK: - Tobler / pace model

    @Test func toblerReferenceValues() {
        #expect(abs(PaceModel.tobler(grade: 0) - 1.399) < 0.005)
        #expect(abs(PaceModel.tobler(grade: 0.10) - 0.986) < 0.005)
        #expect(abs(PaceModel.tobler(grade: -0.05) - 1.667) < 0.005)
        // Steep downhill is slower than a gentle one; grade is clamped at ±0.35.
        #expect(PaceModel.tobler(grade: -0.25) < PaceModel.tobler(grade: -0.05))
        #expect(PaceModel.tobler(grade: 0.9) == PaceModel.tobler(grade: 0.35))
    }

    @Test func steadyFlatWalkHasFactorOne() {
        let r = Self.straight(2000)
        let w = Self.walk(r, from: 0, to: 900, speed: { _ in Self.flatSpeed })
        let m = PaceModel.evaluate(path: w.pts)
        #expect(abs(m.factor - 1.0) < 0.03)
        #expect(m.weight == 1)
    }

    @Test func slowWalkerFactorBelowOne() {
        let r = Self.straight(2000)
        let w = Self.walk(r, from: 0, to: 700, speed: { _ in Self.flatSpeed * 0.7 })
        let m = PaceModel.evaluate(path: w.pts)
        #expect(abs(m.factor - 0.7) < 0.03)
    }

    @Test func climbIsNotMistakenForSlowness() {
        // 10% climb at exactly Tobler speed -> factor ~1, not ~0.7.
        let r = Self.straight(2000)
        let w = Self.walk(r, from: 0, to: 700, speed: { _ in PaceModel.tobler(grade: 0.10) },
                          alt: { 0.10 * $0 })
        #expect(abs(PaceModel.evaluate(path: w.pts).factor - 1.0) < 0.06)
    }

    @Test func earlyHikeBlendsTowardDefault() {
        // 120 s of moving data at 70% of Tobler: weight 0.2 -> 0.2*0.7 + 0.8 = 0.94.
        let r = Self.straight(2000)
        let w = Self.walk(r, from: 0, to: 117, speed: { _ in Self.flatSpeed * 0.7 })
        let m = PaceModel.evaluate(path: w.pts)
        #expect(abs(m.movingSeconds - 120) < 6)
        #expect(abs(m.effectiveFactor - 0.94) < 0.02)
        // 10+ minutes of data: fully personal.
        let long = Self.walk(r, from: 0, to: 700, speed: { _ in Self.flatSpeed * 0.7 })
        #expect(abs(PaceModel.evaluate(path: long.pts).effectiveFactor - 0.7) < 0.03)
    }

    @Test func pauseDoesNotChangePace() {
        let r = Self.straight(3000)
        let a = Self.walk(r, from: 0, to: 400, speed: { _ in Self.flatSpeed })
        // 10 minutes standing still (no samples are stored), then resume.
        let b = Self.walk(r, from: 400, to: 800, t0: a.t + 600_000, speed: { _ in Self.flatSpeed })
        let withPause = PaceModel.evaluate(path: a.pts + b.pts)
        let straightThrough = PaceModel.evaluate(path: Self.walk(r, from: 0, to: 800,
                                                               speed: { _ in Self.flatSpeed }).pts)
        #expect(abs(withPause.factor - straightThrough.factor) < 0.01)
        #expect(abs(withPause.movingSeconds - straightThrough.movingSeconds) < 3)
        // And the estimate is available right after resuming, not missing for ~30 s.
        let est = Self.estimate(a.pts + b.pts, trail: Self.trail(r))
        #expect(est.remaining?.status == .ok)
    }

    @Test func gpsGapIsNotCountedAsSlowWalking() {
        let r = Self.straight(3000)
        let a = Self.walk(r, from: 0, to: 400, speed: { _ in Self.flatSpeed })
        // 60 s gap during which the hiker covered 84 m, then keeps walking.
        let b = Self.walk(r, from: 484, to: 900, t0: a.t + 60_000, speed: { _ in Self.flatSpeed })
        let m = PaceModel.evaluate(path: a.pts + b.pts)
        #expect(abs(m.factor - 1.0) < 0.03)
    }

    // MARK: - Remaining

    @Test func flatRemainingToEnd() {
        let r = Self.straight(3000)
        let w = Self.walk(r, from: 0, to: 1000, speed: { _ in Self.flatSpeed })
        let rem = Self.estimate(w.pts, trail: Self.trail(r)).remaining
        #expect(rem?.status == .ok)
        #expect(abs((rem?.distanceMeters ?? 0) - 2000) < 10)
        let expected = 2000 / Self.flatSpeed
        #expect(abs((rem?.durationSeconds ?? 0) - expected) / expected < 0.03)
        #expect(rem?.targetName == "Test Trail")
        #expect(rem?.etaDate != nil)
    }

    @Test func incrementalFeedMatchesSingleShot() {
        let r = Self.straight(3000)
        let w = Self.walk(r, from: 0, to: 1000, speed: { _ in Self.flatSpeed })
        let t = Self.trail(r)
        var e = HikeEstimator()
        var last: HikeEstimates? = nil
        var n = 0
        while n < w.pts.count {
            n = min(w.pts.count, n + 25)
            let now = Self.date(w.pts[n - 1][2])
            last = e.update(path: Array(w.pts[0..<n]), geometry: TrailGeometry(trail: t),
                            now: now, lastFixAt: now)
        }
        let once = Self.estimate(w.pts, trail: t)
        #expect(abs((last?.remaining?.distanceMeters ?? 0) - (once.remaining?.distanceMeters ?? 1)) < 1)
    }

    private func remainingDuration(grade: Double) -> Double {
        let r = Self.straight(3000)
        let w = Self.walk(r, from: 0, to: 1000, speed: { _ in PaceModel.tobler(grade: grade) },
                          alt: { 1000 + grade * $0 })
        let t = Self.trail(r, profile: { 1000 + grade * $0 })
        return Self.estimate(w.pts, trail: t).remaining?.durationSeconds ?? .nan
    }

    @Test func uphillIsSlowerDownhillFaster() {
        let flat = remainingDuration(grade: 0)
        let up = remainingDuration(grade: 0.10)
        let down = remainingDuration(grade: -0.05)
        let steepDown = remainingDuration(grade: -0.25)
        #expect(up > flat)
        #expect(down < flat)
        #expect(steepDown > down)
        let expectedUp = 2000 / PaceModel.tobler(grade: 0.10)
        #expect(abs(up - expectedUp) / expectedUp < 0.04)
    }

    @Test func loopRemainingUsesRing() {
        let side = 1250.0
        let r = Self.Route([(0, 0), (side, 0), (side, side), (0, side), (0, 0)])
        let w = Self.walk(r, from: 0, to: 4000, speed: { _ in Self.flatSpeed })
        let trail = Self.trail(r)
        let rem = Self.estimate(w.pts, trail: trail).remaining
        #expect(rem?.status == .ok)
        #expect(abs((rem?.distanceMeters ?? 0) - 1000) < 25)
        // The legacy estimator gave up on loops entirely.
        let coord = CLLocationCoordinate2D(latitude: w.pts.last![0], longitude: w.pts.last![1])
        #expect(TrailETA.compute(currentLocation: coord, trail: trail, paceMetersPerSec: 1.4) == nil)
    }

    @Test func outAndBackDirectionLatchesAndFlips() {
        let r = Self.straight(2000)
        let t = Self.trail(r)
        let out = Self.walk(r, from: 0, to: 1000, speed: { _ in Self.flatSpeed })
        #expect(abs((Self.estimate(out.pts, trail: t).remaining?.distanceMeters ?? 0) - 1000) < 10)
        // At the far end.
        let atEnd = Self.walk(r, from: 0, to: 1995, speed: { _ in Self.flatSpeed })
        #expect(Self.estimate(atEnd.pts, trail: t).remaining?.status == .arrived)
        // Turn around and walk back 300 m: direction flips, 1700 m to the other end.
        let back = Self.walk(r, from: 1995, to: 1695, t0: atEnd.t, speed: { _ in Self.flatSpeed })
        let rem = Self.estimate(atEnd.pts + back.pts, trail: t).remaining
        #expect(rem?.status == .ok)
        #expect(abs((rem?.distanceMeters ?? 0) - 1695) < 15)
    }

    @Test func standingStillJitterDoesNotFlipDirection() {
        let r = Self.straight(3000)
        let w = Self.walk(r, from: 0, to: 1000, speed: { _ in Self.flatSpeed })
        // 20 m of jitter back and forth at the end of the walk.
        var p = w.pts
        var t = w.t
        for i in 0..<8 {
            t += 2000
            let c = Self.ll(0, 1000 + (i % 2 == 0 ? -8 : 8))
            p.append([c[0], c[1], t])
        }
        let rem = Self.estimate(p, trail: Self.trail(r)).remaining
        #expect(abs((rem?.distanceMeters ?? 0) - 2000) < 25)
    }

    @Test func estimatingBeforeEnoughData() {
        let r = Self.straight(3000)
        let w = Self.walk(r, from: 0, to: 60, speed: { _ in Self.flatSpeed })
        let est = Self.estimate(w.pts, trail: Self.trail(r))
        #expect(est.remaining?.status == .estimating)
        #expect(est.back?.status == .estimating)
        #expect(est.remaining?.durationSeconds == nil)
    }

    @Test func offTrailShowsRejoinDistanceWithHysteresis() {
        let r = Self.straight(3000)
        let t = Self.trail(r)
        let on = Self.walk(r, from: 0, to: 600, speed: { _ in Self.flatSpeed })
        func leg(x: Double, y0: Double, y1: Double, t0: Double) -> (pts: [GpsPoint], t: Double) {
            var out: [GpsPoint] = []; var tt = t0; var y = y0
            while y <= y1 { let c = Self.ll(x, y); out.append([c[0], c[1], tt, ]); y += 2.8; tt += 2000 }
            return (out, tt)
        }
        let off = leg(x: 80, y0: 600, y1: 640, t0: on.t + 2000)
        var est = Self.estimate(on.pts + off.pts, trail: t)
        if case .offTrail(let m) = est.remaining?.status ?? .unavailable {
            #expect(abs(m - 80) < 3)
        } else { Issue.record("expected offTrail, got \(String(describing: est.remaining?.status))") }
        #expect(est.remaining?.durationSeconds == nil)
        // 40 m is inside the 50 m on-trail threshold but outside the 35 m rejoin one: still off.
        let near = leg(x: 40, y0: 640, y1: 660, t0: off.t)
        est = Self.estimate(on.pts + off.pts + near.pts, trail: t)
        if case .offTrail = est.remaining?.status ?? .unavailable {} else { Issue.record("40 m should still be off") }
        // 20 m: back on.
        let back = leg(x: 20, y0: 660, y1: 700, t0: near.t)
        est = Self.estimate(on.pts + off.pts + near.pts + back.pts, trail: t)
        #expect(est.remaining?.status == .ok)
        // Back is unaffected by being off trail.
        #expect(est.back?.distanceMeters != nil)
    }

    @Test func staleKeepsLastGoodValue() {
        let r = Self.straight(3000)
        let w = Self.walk(r, from: 0, to: 1000, speed: { _ in Self.flatSpeed })
        let g = TrailGeometry(trail: Self.trail(r))
        var e = HikeEstimator()
        let t0 = Self.date(w.t)
        let good = e.update(path: w.pts, geometry: g, now: t0, lastFixAt: t0)
        let stale = e.update(path: w.pts, geometry: g, now: t0.addingTimeInterval(60), lastFixAt: t0)
        #expect(good.remaining?.status == .ok)
        #expect(stale.remaining?.status == .stale)
        #expect(stale.back?.status == .stale)
        #expect(stale.remaining?.durationSeconds == good.remaining?.durationSeconds)
        #expect(stale.remaining?.distanceMeters == good.remaining?.distanceMeters)
        // Fix comes back: live again.
        let t2 = t0.addingTimeInterval(62)
        let live = e.update(path: w.pts, geometry: g, now: t2, lastFixAt: t2)
        #expect(live.remaining?.status == .ok)
    }

    @Test func staleWithNothingGoodIsEstimating() {
        var e = HikeEstimator()
        let now = Self.date(Self.baseMs)
        let est = e.update(path: [], geometry: TrailGeometry(trail: Self.trail(Self.straight(500))),
                           now: now, lastFixAt: now.addingTimeInterval(-120))
        #expect(est.remaining?.status == .estimating)
    }

    @Test func roamHasNoRemainingButHasBack() {
        let r = Self.straight(3000)
        let w = Self.walk(r, from: 0, to: 500, speed: { _ in Self.flatSpeed })
        let est = Self.estimate(w.pts, trail: nil)
        #expect(est.remaining == nil)
        #expect(abs((est.back?.distanceMeters ?? 0) - 500) < 15)
    }

    // MARK: - Back

    @Test func backErasesSpurAndDetour() {
        // 600 m north, a 250 m spur east and back, then 400 m more north.
        let r = Self.Route([(0, 0), (0, 600), (250, 600), (0, 600), (0, 1000)])
        let w = Self.walk(r, from: 0, to: r.total, speed: { _ in Self.flatSpeed })
        let back = Self.estimate(w.pts, trail: nil).back
        #expect(r.total > 1450)
        #expect(abs((back?.distanceMeters ?? 0) - 1000) < 40)
    }

    @Test func backAcrossGpsGapIsApproximate() {
        let r = Self.straight(2000)
        let a = Self.walk(r, from: 0, to: 500, speed: { _ in Self.flatSpeed })
        let b = Self.walk(r, from: 560, to: 800, t0: a.t + 60_000, speed: { _ in Self.flatSpeed })
        let back = Self.estimate(a.pts + b.pts, trail: nil).back
        #expect(back?.status == .approximate)
        #expect(abs((back?.distanceMeters ?? 0) - 800) < 25)
    }

    @Test func backIsReversedGrade() {
        // Climb out at 10%: the way back is downhill (-10%), so faster than climbing.
        let r = Self.straight(2000)
        let up = Self.walk(r, from: 0, to: 700, speed: { _ in PaceModel.tobler(grade: 0.10) },
                           alt: { 0.10 * $0 })
        let down = Self.walk(r, from: 0, to: 700, speed: { _ in PaceModel.tobler(grade: -0.10) },
                             alt: { -0.10 * $0 })
        let tUp = Self.estimate(up.pts, trail: nil).back?.durationSeconds ?? 0
        let tDown = Self.estimate(down.pts, trail: nil).back?.durationSeconds ?? 0
        // Out on a climb -> back downhill at -10%.
        let expectedBackDownhill = 700 / PaceModel.tobler(grade: -0.10)
        #expect(abs(tUp - expectedBackDownhill) / expectedBackDownhill < 0.06)
        // Out downhill -> back is a 10% climb, slower.
        let expectedBackUphill = 700 / PaceModel.tobler(grade: 0.10)
        #expect(abs(tDown - expectedBackUphill) / expectedBackUphill < 0.06)
        #expect(tDown > tUp)
    }

    @Test func backAtStartIsArrived() {
        let r = Self.straight(1000)
        let out = Self.walk(r, from: 0, to: 500, speed: { _ in Self.flatSpeed })
        let home = Self.walk(r, from: 500, to: 3, t0: out.t, speed: { _ in Self.flatSpeed })
        #expect(Self.estimate(out.pts + home.pts, trail: nil).back?.status == .arrived)
    }

    // MARK: - Regression: old formula vs new

    @Test func regressionClimbThenFlatRemaining() {
        // 3 km trail, 1 km in, climbing 10% for 500 m more then flat. Truth ~26 min.
        let r = Self.straight(3000)
        let profile: (Double) -> Double = { $0 < 1500 ? 0.10 * $0 : 150 }
        let t = Self.trail(r, profile: profile)
        let w = Self.walk(r, from: 0, to: 1000, speed: { _ in PaceModel.tobler(grade: 0.10) },
                          alt: { 0.10 * $0 })
        let coord = CLLocationCoordinate2D(latitude: w.pts.last![0], longitude: w.pts.last![1])
        let prior = CLLocationCoordinate2D(latitude: w.pts[w.pts.count - 2][0], longitude: w.pts[w.pts.count - 2][1])
        let oldPace = RecordingService.paceMetersPerSec(path: w.pts, windowSeconds: 60)
        let old = TrailETA.compute(currentLocation: coord, priorLocation: prior, trail: t,
                                   paceMetersPerSec: oldPace) ?? .nan
        let truth = 500 / PaceModel.tobler(grade: 0.10) + 1500 / PaceModel.tobler(grade: 0)
        let new = Self.estimate(w.pts, trail: t).remaining?.durationSeconds ?? .nan
        #expect(abs(old - 2029) / 2029 < 0.03)               // old: ~34 min
        #expect(abs(old - truth) / truth > 0.25)             // old is >25% off
        #expect(abs(new - truth) / truth < 0.05)             // new within 5% of ~26 min
    }

    @Test func regressionLoopBack() {
        // 5 km loop, 4 km walked: carrying on is 1 km (~12 min). Old said ~48 min.
        let side = 1250.0
        let r = Self.Route([(0, 0), (side, 0), (side, side), (0, side), (0, 0)])
        let w = Self.walk(r, from: 0, to: 4000, speed: { _ in 1.4 })
        let old = TrailETA.returnToStart(walkedMeters: 4000, paceMetersPerSec: 1.4) ?? .nan
        let truth = 1000 / PaceModel.tobler(grade: 0)
        let new = Self.estimate(w.pts, trail: Self.trail(r)).back?.durationSeconds ?? .nan
        #expect(abs(old - 2857) < 2)
        #expect(old / truth > 3.5)
        #expect(abs(new - truth) / truth < 0.05)
    }

    @Test func regressionRestDoesNotFreezeOrDropEstimate() {
        // Old: pace came from the 60 s window hanging off the last STORED sample, so
        // a rest froze it, and the first sample after the rest left it nil for ~30 s.
        let r = Self.straight(3000)
        let a = Self.walk(r, from: 0, to: 700, speed: { _ in Self.flatSpeed })
        let b = Self.walk(r, from: 700, to: 712, t0: a.t + 600_000, speed: { _ in Self.flatSpeed })
        let path = a.pts + b.pts
        #expect(RecordingService.paceMetersPerSec(path: path, windowSeconds: 60) == nil)
        #expect(Self.estimate(path, trail: Self.trail(r)).remaining?.status == .ok)
    }

    // MARK: - Text and formatting

    @Test func durationFormatting() {
        #expect(UnitFormatter.duration(seconds: 0) == "<1 min")
        #expect(UnitFormatter.duration(seconds: 29) == "<1 min")
        #expect(UnitFormatter.duration(seconds: 59) == "1 min")
        #expect(UnitFormatter.duration(seconds: 60) == "1 min")
        #expect(UnitFormatter.duration(seconds: 12 * 60) == "12 min")
        #expect(UnitFormatter.duration(seconds: 59 * 60) == "59 min")
        #expect(UnitFormatter.duration(seconds: 61 * 60) == "1h 01m")
        #expect(UnitFormatter.duration(seconds: 125 * 60) == "2h 05m")
        #expect(UnitFormatter.duration(seconds: .nan) == "<1 min")
        #expect(UnitFormatter.duration(seconds: -5) == "<1 min")
    }

    private func sample(_ status: HikeEstimate.Status = .ok, d: Double? = 2253, t: Double? = 24 * 60) -> HikeEstimate {
        HikeEstimate(distanceMeters: d, durationSeconds: t, status: status,
                     targetName: "National Trail", computedAt: Self.date(Self.baseMs))
    }

    private var utc: TimeZone { TimeZone(identifier: "UTC")! }
    private var enUS: Locale { Locale(identifier: "en_US") }

    @Test func primarySecondaryAndUnits() {
        let ref = Calendar(identifier: .gregorian).date(
            from: DateComponents(timeZone: utc, year: 2026, month: 11, day: 1, hour: 15, minute: 18))!
        let e = sample()
        #expect(HikeEstimateText.primary(e, kind: .remaining) == "Remaining 24 min")
        #expect(HikeEstimateText.primary(e, kind: .back) == "Back 24 min")
        let imperial = HikeEstimateText.secondary(e, units: .imperial, reference: ref, timeZone: utc, locale: enUS)
        #expect(imperial?.hasPrefix("1.40 mi · 3:42") == true)
        let metric = HikeEstimateText.secondary(e, units: .metric, reference: ref, timeZone: utc, locale: enUS)
        #expect(metric?.hasPrefix("2.25 km · 3:42") == true)
        #expect(HikeEstimateText.primary(sample(.approximate), kind: .back) == "Back ~24 min")
        #expect(HikeEstimateText.primary(sample(.estimating, d: nil, t: nil), kind: .remaining) == "Remaining · estimating…")
        #expect(HikeEstimateText.primary(sample(.arrived, d: 0, t: 0), kind: .remaining) == "Remaining · at the end")
        #expect(HikeEstimateText.primary(sample(.arrived, d: 0, t: 0), kind: .back) == "Back · at the start")
        #expect(HikeEstimateText.primary(nil, kind: .back) == "Back · estimating…")
        let off = HikeEstimateText.secondary(sample(.offTrail(rejoinMeters: 120), d: nil, t: nil),
                                             units: .metric, reference: ref)
        #expect(off == "120 m to rejoin")
        let stale = HikeEstimateText.secondary(sample(.stale), units: .imperial, reference: ref.addingTimeInterval(3600),
                                               timeZone: utc, locale: enUS)
        #expect(stale?.hasSuffix("last known") == true)
    }

    @Test func voiceOverLabels() {
        let ref = Self.date(Self.baseMs)
        let rem = HikeEstimateText.accessibility(sample(), kind: .remaining, units: .imperial,
                                                 reference: ref, timeZone: utc, locale: enUS)
        #expect(rem.hasPrefix("Remaining, about 24 minutes, 1.4 miles, to finish National Trail, done around"))
        let back = HikeEstimateText.accessibility(sample(.approximate), kind: .back, units: .metric,
                                                  reference: ref, timeZone: utc, locale: enUS)
        #expect(back.contains("roughly 24 minutes"))
        #expect(back.contains("to return to start"))
        #expect(HikeEstimateText.accessibility(sample(.stale), kind: .back, units: .imperial,
                                               reference: ref, timeZone: utc, locale: enUS).hasPrefix("Last known, Back"))
        #expect(HikeEstimateText.accessibility(nil, kind: .remaining, units: .imperial, reference: ref)
                == "Remaining, still estimating.")
        #expect(HikeEstimateText.explanation(hasTrail: true, trailName: "National Trail")
                .contains("to finish National Trail"))
        #expect(!HikeEstimateText.explanation(hasTrail: false, trailName: nil).contains("Remaining"))
    }

    @Test func snapshotMirrorsEstimate() {
        let s = sample(.offTrail(rejoinMeters: 5), d: nil, t: nil).snapshot
        #expect(s.statusKey == "offTrail")
        #expect(s.targetName == "National Trail")
        let ok = sample().snapshot
        #expect(ok.durationSeconds == 1440)
        #expect(ok.etaDate == Self.date(Self.baseMs).addingTimeInterval(1440))
    }
}
