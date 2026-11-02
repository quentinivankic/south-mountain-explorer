import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `PersonalPace.estimate` ("my pace" from saved hikes).
struct PersonalPaceTests {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)
    private let degPerMeter = 1.0 / 111_000.0

    /// Synthetic hike walking north. Speed at each step is
    /// `factor * tobler(grade)`, so the true personal factor is `factor`.
    /// `grade` maps distance walked (m) to grade. Fixes every 5 s.
    private func hike(
        id: String, daysAgo: Double, minutes: Double = 20, factor: Double = 1.0,
        grade: (Double) -> Double = { _ in 0 }, altitude: Bool = true,
        mode: RecordingMode = .roam, pauseEvery: Int? = nil, jumpEvery: Int? = nil,
        factorAt: ((Double) -> Double)? = nil
    ) -> SavedRecording {
        let start = now.addingTimeInterval(-daysAgo * 86_400)
        var ts = start.timeIntervalSince1970 * 1000
        var s = 0.0, alt = 1000.0
        var path: [GpsPoint] = []
        func fix(_ extraNorth: Double = 0) {
            var p: GpsPoint = [33.3 + (s + extraNorth) * degPerMeter, -112.0, ts]
            if altitude { p.append(alt) }
            path.append(p)
        }
        fix()
        let steps = Int(minutes * 60 / 5)
        for k in 1...steps {
            let g = grade(s)
            let v = (factorAt?(s) ?? factor) * PaceModel.tobler(grade: g)
            s += v * 5
            alt += g * v * 5
            ts += 5000
            if let n = pauseEvery, k % n == 0 {
                // Stand still for 2 minutes (a gap), then keep walking.
                ts += 120_000
                fix()
            }
            if let n = jumpEvery, k % n == 0 {
                fix(5_000)   // bogus 5 km fix, then back to the real track
            }
            fix()
        }
        return SavedRecording(
            id: id, areaId: "a", startedAt: start,
            endedAt: start.addingTimeInterval(minutes * 60),
            distanceMi: s / 1609.34, durationSeconds: Int(minutes * 60),
            completedTrailIds: [], path: path, mode: mode)
    }

    private func hikes(_ n: Int, daysAgo: Double = 3, factor: Double = 1.0,
                       grade: @escaping (Double) -> Double = { _ in 0 },
                       altitude: Bool = true, mode: RecordingMode = .roam,
                       prefix: String = "h") -> [SavedRecording] {
        (0..<n).map {
            hike(id: "\(prefix)\($0)", daysAgo: daysAgo + Double($0), factor: factor,
                 grade: grade, altitude: altitude, mode: mode)
        }
    }

    @Test func fastHistoryGivesFactorAboveOne() throws {
        let r = try #require(PersonalPace.estimate(from: hikes(4, factor: 1.3), now: now))
        #expect(abs(r.factor - 1.3) < 0.03)
        #expect(r.hikesUsed == 4)
    }

    @Test func slowHistoryGivesFactorBelowOne() throws {
        let r = try #require(PersonalPace.estimate(from: hikes(4, factor: 0.7), now: now))
        #expect(abs(r.factor - 0.7) < 0.03)
    }

    @Test func mixedUphillDownhillAtToblerSpeedIsAboutOne() throws {
        // 100 m at +10%, 100 m at -10%, repeating.
        let g: (Double) -> Double = { Int($0 / 100) % 2 == 0 ? 0.10 : -0.10 }
        let r = try #require(PersonalPace.estimate(from: hikes(3, grade: g), now: now))
        #expect(abs(r.factor - 1.0) < 0.05)
    }

    @Test func noAltitudeTreatsGradeAsZero() throws {
        // Flat, no altitude: still measures correctly.
        let flat = try #require(PersonalPace.estimate(from: hikes(3, factor: 1.2, altitude: false), now: now))
        #expect(abs(flat.factor - 1.2) < 0.03)
        // Steady climb at Tobler speed without altitude reads as slower than
        // flat Tobler (grade is unknown, so it is assumed 0).
        let climb = try #require(PersonalPace.estimate(
            from: hikes(3, grade: { _ in 0.12 }, altitude: false), now: now))
        #expect(climb.factor < 0.8)
        // Same climb WITH altitude is ~1.
        let withAlt = try #require(PersonalPace.estimate(
            from: hikes(3, grade: { _ in 0.12 }), now: now))
        #expect(abs(withAlt.factor - 1.0) < 0.05)
    }

    @Test func stopsAndGapsAreIgnored() throws {
        let h = (0..<3).map { hike(id: "p\($0)", daysAgo: 2 + Double($0), factor: 1.1, pauseEvery: 20) }
        let r = try #require(PersonalPace.estimate(from: h, now: now))
        #expect(abs(r.factor - 1.1) < 0.03)
    }

    @Test func gpsJumpsAreIgnored() throws {
        let h = (0..<3).map { hike(id: "j\($0)", daysAgo: 2 + Double($0), factor: 1.0, jumpEvery: 15) }
        let r = try #require(PersonalPace.estimate(from: h, now: now))
        #expect(abs(r.factor - 1.0) < 0.05)
    }

    @Test func absurdAltitudeJumpChunksAreDropped() throws {
        var h = hikes(3, factor: 1.0)
        // Corrupt every 10th altitude by 400 m.
        h = h.map { rec in
            var p = rec.path
            for i in stride(from: 10, to: p.count, by: 10) { p[i][3] += 400 }
            return SavedRecording(id: rec.id, areaId: rec.areaId, startedAt: rec.startedAt,
                                  endedAt: rec.endedAt, distanceMi: rec.distanceMi,
                                  durationSeconds: rec.durationSeconds, completedTrailIds: [],
                                  path: p, mode: rec.mode)
        }
        let r = try #require(PersonalPace.estimate(from: h, now: now))
        #expect(abs(r.factor - 1.0) < 0.1)
    }

    @Test func tooLittleHistoryIsInsufficient() {
        #expect(PersonalPace.estimate(from: [], now: now) == nil)
        // One long hike: not enough hikes.
        #expect(PersonalPace.estimate(from: [hike(id: "one", daysAgo: 1, minutes: 90)], now: now) == nil)
        // Two short hikes: not enough moving time (2 x 10 min).
        let short = [hike(id: "s1", daysAgo: 1, minutes: 10), hike(id: "s2", daysAgo: 2, minutes: 10)]
        #expect(PersonalPace.estimate(from: short, now: now) == nil)
        // Two 16-minute hikes: just over the 30 minute / 2 hike minimum.
        let ok = [hike(id: "o1", daysAgo: 1, minutes: 16), hike(id: "o2", daysAgo: 2, minutes: 16)]
        #expect(PersonalPace.estimate(from: ok, now: now) != nil)
    }

    @Test func factorIsClamped() throws {
        let fast = try #require(PersonalPace.estimate(from: hikes(3, factor: 4.0), now: now))
        #expect(fast.factor == PaceModel.factorRange.upperBound)
        let slow = try #require(PersonalPace.estimate(from: hikes(3, factor: 0.25), now: now))
        #expect(slow.factor == PaceModel.factorRange.lowerBound)
    }

    @Test func recentHikesWeighMoreAndOldOnesDropOut() throws {
        // Recent fast + older slow: result leans fast (an unweighted mix is ~1.0).
        let mixed = hikes(2, daysAgo: 1, factor: 1.3, prefix: "new")
            + hikes(2, daysAgo: 80, factor: 0.7, prefix: "old")
        let r = try #require(PersonalPace.estimate(from: mixed, now: now))
        #expect(r.factor > 1.15)
        // Hikes older than 90 days are ignored entirely.
        let stale = hikes(2, daysAgo: 1, factor: 1.3, prefix: "new")
            + hikes(3, daysAgo: 120, factor: 0.5, prefix: "old")
        let r2 = try #require(PersonalPace.estimate(from: stale, now: now))
        #expect(abs(r2.factor - 1.3) < 0.03)
        #expect(r2.hikesUsed == 2)
        // Only the newest 20 hikes count.
        let many = hikes(20, daysAgo: 1, factor: 1.3, prefix: "n") + hikes(5, daysAgo: 0.5, factor: 0.8, prefix: "x")
        let r3 = try #require(PersonalPace.estimate(from: many, now: now))
        #expect(r3.hikesUsed == 20)
    }

    @Test func walkModeIsExcluded() throws {
        let walks = hikes(4, factor: 0.5, mode: .walk, prefix: "w")
        #expect(PersonalPace.estimate(from: walks, now: now) == nil)
        let r = try #require(PersonalPace.estimate(
            from: hikes(3, factor: 1.2) + walks, now: now))
        #expect(abs(r.factor - 1.2) < 0.03)
        #expect(r.hikesUsed == 3)
    }

    @Test func trailAndRoamModesBothCount() throws {
        let h = hikes(2, factor: 1.0, mode: .trail, prefix: "t") + hikes(2, factor: 1.0, mode: .roam, prefix: "r")
        #expect(PersonalPace.estimate(from: h, now: now)?.hikesUsed == 4)
    }

    @Test func trimmingDropsOutlierChunks() throws {
        // Steady 1.0 walk with a 60 m sprint (factor 4) every 1000 m. Only a few chunks
        // are outliers, so trimming (10% each end) removes them.
        let hs = (0..<3).map { i in
            hike(id: "m\(i)", daysAgo: 2 + Double(i), minutes: 20,
                 factorAt: { Int($0) % 1000 < 60 ? 4.0 : 1.0 })
        }
        let r = try #require(PersonalPace.estimate(from: hs, now: now))
        #expect(abs(r.factor - 1.0) < 0.03)
    }

    @Test func deterministicAndOrderIndependent() throws {
        let h = hikes(5, factor: 1.15, grade: { Int($0 / 80) % 2 == 0 ? 0.06 : -0.04 })
        let a = PersonalPace.estimate(from: h, now: now)
        let b = PersonalPace.estimate(from: h, now: now)
        let c = PersonalPace.estimate(from: h.reversed(), now: now)
        #expect(a == b)
        #expect(a == c)
    }

    @Test func summaryIsExplainable() {
        let slow = PersonalPaceResult(factor: 1 / 1.12, movingSeconds: 4.2 * 3600, hikesUsed: 6)
        #expect(slow.summary == "12% slower than the default, from 6 hikes / 4.2 h of moving time")
        let fast = PersonalPaceResult(factor: 1.25, movingSeconds: 45 * 60, hikesUsed: 2)
        #expect(fast.summary == "20% faster than the default, from 2 hikes / 45 min of moving time")
        let same = PersonalPaceResult(factor: 1.01, movingSeconds: 3600, hikesUsed: 1)
        #expect(same.summary.hasPrefix("About the same as the default"))
        #expect(same.summary.contains("1 hike /"))
    }

    @Test func factorFeedsRoutingCostLikePaceModel() {
        // factor > 1 shortens walking time exactly as PaceModel.seconds does.
        let base = PaceModel.seconds(length: 1000, grade: 0.05, factor: 1.0)
        #expect(PaceModel.seconds(length: 1000, grade: 0.05, factor: 1.25) < base)
        #expect(abs(PaceModel.seconds(length: 1000, grade: 0.05, factor: 0.8) - base / 0.8) < 1e-9)
    }
}
