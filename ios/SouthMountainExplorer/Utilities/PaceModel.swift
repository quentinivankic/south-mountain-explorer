import Foundation

/// Terrain-aware, moving-only walking pace used by the mid-hike estimates
/// ("Remaining" and "Back"). Pure value-type math: no clock, no UI, no state.
///
/// Why not the 60 s `smoothedPaceMetersPerSec`: that window is "the pace of the
/// hill you are on right now", counts GPS gaps as slow walking, and freezes at
/// its pre-rest value while you stand still. Here:
///   - only MOVING pairs count (consecutive fixes <= `GpsIngest.gapMs` apart and
///     >= 0.3 m/s), so rests and GPS gaps contribute nothing;
///   - speed is compared to a Tobler hiking function over the same stretch, so a
///     climb does not read as "you are slow";
///   - the personal factor is blended with the plain Tobler default until there
///     are `fullConfidenceMovingSeconds` of moving data.
enum PaceModel {
    /// Moving seconds of history to consider (and for full confidence).
    static let fullConfidenceMovingSeconds: Double = 600
    static let chunkMeters: Double = 50
    static let minMovingSpeed: Double = 0.3
    static let maxMovingSpeed: Double = 6
    static let factorRange: ClosedRange<Double> = 0.4...1.8
    static let maxAbsGrade: Double = 0.35

    struct Result: Equatable, Sendable {
        /// Raw personal factor: Tobler-expected time / observed time. 1.0 walks like Tobler.
        var factor: Double
        /// Moving seconds the factor was measured over (capped at the window).
        var movingSeconds: Double
        /// 0...1 confidence in `factor`.
        var weight: Double { min(1, movingSeconds / PaceModel.fullConfidenceMovingSeconds) }
        /// Factor to apply to Tobler speeds: personal when confident, 1.0 when not.
        var effectiveFactor: Double { weight * factor + (1 - weight) * 1.0 }

        static let none = Result(factor: 1, movingSeconds: 0)
    }

    /// Tobler's hiking function in m/s. `grade` is rise/run (0.10 = 10% uphill).
    /// v(km/h) = 6·exp(-3.5·|s + 0.05|), grade clamped to ±0.35.
    static func tobler(grade: Double) -> Double {
        let s = min(maxAbsGrade, max(-maxAbsGrade, grade.isFinite ? grade : 0))
        return 6 * exp(-3.5 * abs(s + 0.05)) / 3.6
    }

    /// Seconds to cover `length` meters at `grade` for a walker with `factor`.
    static func seconds(length: Double, grade: Double, factor: Double) -> Double {
        guard length > 0 else { return 0 }
        return length / (max(0.05, factor) * tobler(grade: grade))
    }

    /// Measure the personal factor from the tail of the recorded path.
    static func evaluate(path: [GpsPoint]) -> Result {
        guard path.count >= 2 else { return .none }
        var moving = 0.0
        var expected = 0.0
        var observed = 0.0
        // Chunk accumulator, built walking BACKWARD in time.
        var chunkLen = 0.0, chunkDt = 0.0
        var altEnd: Double? = nil      // altitude at the later end of the chunk
        var altStart: Double? = nil    // altitude at the earlier end of the chunk

        func flush() {
            guard chunkLen > 0 else { return }
            var grade = 0.0
            if chunkLen >= chunkMeters / 2, let a = altStart, let b = altEnd {
                grade = (b - a) / chunkLen
            }
            expected += chunkLen / tobler(grade: grade)
            observed += chunkDt
            chunkLen = 0; chunkDt = 0; altEnd = nil; altStart = nil
        }

        var i = path.count - 1
        while i >= 1 && moving < fullConfidenceMovingSeconds {
            let a = path[i - 1], b = path[i]
            i -= 1
            guard a.count >= 3, b.count >= 3 else { flush(); continue }
            let dt = (b[2] - a[2]) / 1000
            guard dt > 0, dt <= GpsIngest.gapMs / 1000 else { flush(); continue }
            let d = MapMath.haversineMeters(lat1: a[0], lon1: a[1], lat2: b[0], lon2: b[1])
            let v = d / dt
            guard v >= minMovingSpeed, v <= maxMovingSpeed else { flush(); continue }
            moving += dt
            if altEnd == nil { altEnd = b.altitudeMeters }
            altStart = a.altitudeMeters
            chunkLen += d
            chunkDt += dt
            if chunkLen >= chunkMeters { flush() }
        }
        flush()
        guard observed > 0, expected > 0 else { return .none }
        let factor = min(factorRange.upperBound, max(factorRange.lowerBound, expected / observed))
        return Result(factor: factor, movingSeconds: moving)
    }
}
