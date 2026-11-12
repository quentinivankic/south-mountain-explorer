import Foundation
import Testing
@testable import SouthMountainExplorer

/// The hand-written haversine copies were routed through
/// Utilities/Haversine.swift. The old formulas are kept here, verbatim, and
/// every shared call must give the same bits for each old radius and form.
struct HaversineTests {
    // MARK: - The old copies, verbatim

    /// Old `haversineDistanceMi`, HomeView, AreaCard, BrowseView (x2) and
    /// Trail+RouteType endpoint gap: R = 3958.8 mi, atan2.
    private static func oldMiles(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let R = 3958.8
        let dLat = (lat2 - lat1) * .pi / 180
        let dLon = (lon2 - lon1) * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(lat1 * .pi / 180) * cos(lat2 * .pi / 180)
            * sin(dLon / 2) * sin(dLon / 2)
        return R * 2 * atan2(sqrt(h), sqrt(1 - h))
    }

    /// Old `MapMath.haversineMeters`: R = 6,371,000 m, `2 * R * atan2`.
    private static func oldMapMathMeters(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let R = 6_371_000.0
        let dLat = (lat2 - lat1) * .pi / 180
        let dLon = (lon2 - lon1) * .pi / 180
        let a = sin(dLat / 2) * sin(dLat / 2) +
            cos(lat1 * .pi / 180) * cos(lat2 * .pi / 180) *
            sin(dLon / 2) * sin(dLon / 2)
        return 2 * R * atan2(sqrt(a), sqrt(1 - a))
    }

    /// Old `AreaDataService.segmentMiles` step: R = 6,371,000 m, `R * 2 * atan2`.
    private static func oldSegmentMeters(_ la1: Double, _ lo1: Double, _ la2: Double, _ lo2: Double) -> Double {
        let R = 6_371_000.0
        let dLat = (la2 - la1) * .pi / 180
        let dLon = (lo2 - lo1) * .pi / 180
        let a = sin(dLat/2)*sin(dLat/2) + cos(la1 * .pi/180)*cos(la2 * .pi/180)*sin(dLon/2)*sin(dLon/2)
        return R * 2 * atan2(sqrt(a), sqrt(1-a))
    }

    /// Old `Area.meters` and `TrailProfile.meters`: R = 6,371,000 m, asin.
    private static func oldAsinMeters(_ aLat: Double, _ aLon: Double, _ bLat: Double, _ bLon: Double) -> Double {
        let R = 6_371_000.0
        let dLat = (bLat - aLat) * .pi / 180, dLon = (bLon - aLon) * .pi / 180
        let la1 = aLat * .pi / 180, la2 = bLat * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(la1) * cos(la2) * sin(dLon / 2) * sin(dLon / 2)
        return 2 * R * asin(min(1, sqrt(h)))
    }

    // MARK: - Inputs

    /// Fixed edge cases plus 2,000 seeded random pairs: same point, a few
    /// metres, trail scale, continental, antipodal, poles, the date line.
    private static let pairs: [(Double, Double, Double, Double)] = {
        var out: [(Double, Double, Double, Double)] = [
            (33.333845, -112.030861, 33.333845, -112.030861),
            (33.333845, -112.030861, 33.333900, -112.030800),
            (33.3338, -112.0308, 33.3500, -112.0100),
            (33.3338, -112.0308, 61.2181, -149.9003),
            (0, 0, 0, 180),
            (89.9, 10, -89.9, -170),
            (90, 0, -90, 0),
            (51.5, 179.999, 51.5, -179.999),
            (-33.86, 151.21, 40.71, -74.01),
        ]
        var rng = SplitMix64(seed: 0x5EED_B3)
        for _ in 0..<2000 {
            let lat1 = rng.unit() * 180 - 90, lon1 = rng.unit() * 360 - 180
            // Half the pairs are near each other (trail scale), half anywhere.
            if rng.unit() < 0.5 {
                out.append((lat1, lon1, lat1 + (rng.unit() - 0.5) * 0.02, lon1 + (rng.unit() - 0.5) * 0.02))
            } else {
                out.append((lat1, lon1, rng.unit() * 180 - 90, rng.unit() * 360 - 180))
            }
        }
        return out
    }()

    private struct SplitMix64 {
        var state: UInt64
        init(seed: UInt64) { state = seed }
        mutating func next() -> UInt64 {
            state &+= 0x9E37_79B9_7F4A_7C15
            var z = state
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            return z ^ (z >> 31)
        }
        mutating func unit() -> Double { Double(next() >> 11) / Double(1 << 53) }
    }

    private static func same(_ a: Double, _ b: Double) -> Bool { a.bitPattern == b.bitPattern }

    // MARK: - Tests

    @Test func radiiAreTheOldOnes() {
        #expect(earthRadiusMiles == 3958.8)
        #expect(earthRadiusMeters == 6_371_000.0)
    }

    @Test func milesMatchTheOldCopiesBitForBit() {
        for (a, b, c, d) in Self.pairs {
            let old = Self.oldMiles(a, b, c, d)
            #expect(Self.same(haversineDistanceMi(lat1: a, lon1: b, lat2: c, lon2: d), old))
            #expect(Self.same(haversineDistance(lat1: a, lon1: b, lat2: c, lon2: d, radius: 3958.8), old))
        }
    }

    @Test func metresHelperIsStillMilesTimes1609() {
        for (a, b, c, d) in Self.pairs {
            #expect(Self.same(haversineDistanceM(lat1: a, lon1: b, lat2: c, lon2: d),
                              Self.oldMiles(a, b, c, d) * 1609.344))
        }
    }

    @Test func metreAtan2CallersMatchTheOldCopiesBitForBit() {
        for (a, b, c, d) in Self.pairs {
            #expect(Self.same(MapMath.haversineMeters(lat1: a, lon1: b, lat2: c, lon2: d),
                              Self.oldMapMathMeters(a, b, c, d)))
            #expect(Self.same(haversineDistance(lat1: a, lon1: b, lat2: c, lon2: d, radius: earthRadiusMeters),
                              Self.oldSegmentMeters(a, b, c, d)))
        }
    }

    @Test func metreAsinCallersMatchTheOldCopiesBitForBit() {
        for (a, b, c, d) in Self.pairs {
            let old = Self.oldAsinMeters(a, b, c, d)
            #expect(Self.same(Area.meters(a, b, c, d), old))
            #expect(Self.same(TrailProfile.meters(TrailProfile.Point(lat: a, lon: b),
                                                  TrailProfile.Point(lat: c, lon: d)), old))
        }
    }
}
