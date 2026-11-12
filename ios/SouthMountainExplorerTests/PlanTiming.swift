import Foundation

/// Timing guards for the planner tests that hold on a slow CI runner and still
/// catch real slowdowns.
///
/// - Each plan is timed in CPU time of the calling thread, so tests running in
///   parallel on other cores do not count against it. A plan runs
///   synchronously on one thread.
/// - The limit is a laptop limit times `machineFactor`: how much slower this
///   machine runs a fixed workload than the laptop the limits were set on
///   (1 on that laptop, never below 1, at most `maxFactor`). The workload is
///   plain Swift (sorting and hashing), not planner code, so a planner
///   slowdown can never raise the allowance that is meant to catch it.
/// - On CI (`TREKDEX_CI=1`, set by ios-pr-build.yml through
///   `TEST_RUNNER_TREKDEX_CI`) every limit is also multiplied by `ciSlack`.
///   Shared runners slow down unevenly during a run, so the one calibration
///   at the start can undershoot (7.07 s measured against a 6.99 s limit).
///   Locally `ciSlack` is 1 and the limits stay strict.
enum PlanTiming {
    /// CPU seconds of `calibrationWork` on the laptop (Debug build, simulator),
    /// measured 11/6 while the planner tests ran.
    static let laptopCalibrationSeconds = 0.21
    static let maxFactor = 5.0
    static let ciFactor = 3.0

    /// True when the CI workflow asked for CI limits.
    static let isCI = ProcessInfo.processInfo.environment["TREKDEX_CI"] == "1"

    /// Extra headroom on CI only: `ciFactor` there, 1 everywhere else.
    static var ciSlack: Double { isCI ? ciFactor : 1 }

    /// A fixed wall-clock limit (not calibrated), loosened on CI only.
    static func wallLimit(_ laptopValue: Double) -> Double { laptopValue * ciSlack }

    /// CPU seconds the current thread spent in `body`, and its result.
    static func cpuSeconds<T>(_ body: () throws -> T) rethrows -> (T, Double) {
        let t0 = threadCPU()
        let out = try body()
        return (out, threadCPU() - t0)
    }

    /// This machine's slowdown against the laptop, 1...maxFactor. Best of
    /// three runs, so one interrupted run does not inflate it.
    static var machineFactor: Double { min(maxFactor, max(1, calibrationSeconds / laptopCalibrationSeconds)) }

    /// CPU seconds of `calibrationWork` on this machine, best of three.
    static let calibrationSeconds: Double = {
        var best = Double.infinity
        for _ in 0..<3 { best = min(best, cpuSeconds { calibrationWork() }.1) }
        return best
    }()

    /// A laptop limit scaled to this machine, with `ciSlack` on CI.
    static func limit(_ laptopSeconds: Double) -> Double { laptopSeconds * machineFactor * ciSlack }

    static func threadCPU() -> Double {
        var ts = timespec()
        clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts)
        return Double(ts.tv_sec) + Double(ts.tv_nsec) / 1e9
    }

    /// Fixed, deterministic CPU work: sort 200k pseudo-random doubles and
    /// hash 100k integers.
    @discardableResult
    static func calibrationWork() -> Double {
        var s: UInt64 = 0x9E37_79B9_7F4A_7C15
        var xs = [Double](repeating: 0, count: 200_000)
        for i in xs.indices {
            s = s &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
            xs[i] = Double(s >> 11)
        }
        xs.sort()
        var d: [Int: Int] = [:]
        for i in 0..<100_000 { d[(i &* 2_654_435_761) & 0xFFFFF, default: 0] += i }
        return xs[xs.count / 2] + Double(d.count)
    }
}
