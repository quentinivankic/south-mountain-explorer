import Foundation
@testable import SouthMountainExplorer

/// Synthetic geometry for turn-cue tests. Coordinates are local metres
/// (x east, y north) anchored at (33.3, -112.0), the same point the engine
/// uses as origin when a test trail starts at (0, 0).
enum CueFixtures {
    static let originLat = 33.3
    static let originLon = -112.0
    static let t0 = 1_800_000_000.0

    typealias XY = (x: Double, y: Double)

    static func ll(_ p: XY) -> [Double] {
        let mPerDeg = TurnCueProjector.metersPerDegree
        return [
            originLat + p.y / mPerDeg,
            originLon + p.x / (mPerDeg * cos(originLat * .pi / 180)),
        ]
    }

    static func trail(_ id: String, _ name: String, _ pts: [XY]) -> Trail {
        trail(id, name, segments: [pts])
    }

    static func trail(_ id: String, _ name: String, segments: [[XY]]) -> Trail {
        Trail(
            id: id, name: name, distanceMi: 0.5, difficulty: .easy,
            segments: segments.map { $0.map(ll) }
        )
    }

    /// Straight-line polar offset: `bearing` is compass degrees.
    static func offset(_ from: XY, bearing: Double, meters: Double) -> XY {
        let r = bearing * .pi / 180
        return (from.x + sin(r) * meters, from.y + cos(r) * meters)
    }

    struct SplitMix: RandomNumberGenerator {
        var state: UInt64
        init(seed: UInt64) { state = seed &+ 0x9E37_79B9_7F4A_7C15 }
        mutating func next() -> UInt64 {
            state &+= 0x9E37_79B9_7F4A_7C15
            var z = state
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            return z ^ (z >> 31)
        }
    }

    struct Path {
        let pts: [XY]
        let cum: [Double]
        init(_ pts: [XY]) {
            self.pts = pts
            var c = [0.0]
            for i in 1..<pts.count {
                c.append(c[i - 1] + hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y))
            }
            cum = c
        }
        var length: Double { cum.last! }
        func position(at s: Double) -> XY {
            let s = max(0, min(length, s))
            var i = 0
            while i < pts.count - 2, cum[i + 1] < s { i += 1 }
            let seg = cum[i + 1] - cum[i]
            let t = seg > 0 ? (s - cum[i]) / seg : 0
            return (pts[i].x + (pts[i + 1].x - pts[i].x) * t, pts[i].y + (pts[i + 1].y - pts[i].y) * t)
        }
    }

    static func fix(_ p: XY, t: Double, acc: Double = 5, speed: Double? = 1.4) -> TurnCueFix {
        let c = ll(p)
        return TurnCueFix(lat: c[0], lon: c[1], timestamp: t, horizontalAccuracy: acc, speed: speed)
    }

    /// 1 Hz fixes walking `path` from arc `s0` to `s1`. Optional uniform
    /// noise (+/- `noise` m on each axis) from a seeded generator.
    static func walk(
        _ path: Path, from s0: Double, to s1: Double, speed: Double = 1.4, acc: Double = 5,
        startT: Double = t0, noise: Double = 0, seed: UInt64 = 1
    ) -> [TurnCueFix] {
        var rng = SplitMix(seed: seed)
        var out: [TurnCueFix] = []
        var k = 0
        while true {
            let s = s0 + speed * Double(k)
            if s > s1 { break }
            var p = path.position(at: s)
            if noise > 0 {
                p.x += Double.random(in: -noise...noise, using: &rng)
                p.y += Double.random(in: -noise...noise, using: &rng)
            }
            out.append(fix(p, t: startT + Double(k), acc: acc, speed: speed))
            k += 1
        }
        return out
    }

    struct Hit {
        let index: Int
        let event: TurnCueEvent
    }

    @discardableResult
    static func run(_ engine: inout TurnCueEngine, _ fixes: [TurnCueFix]) -> [Hit] {
        var hits: [Hit] = []
        for (i, f) in fixes.enumerated() {
            for e in engine.ingest(f, now: f.timestamp) { hits.append(Hit(index: i, event: e)) }
        }
        return hits
    }

    static func engine(_ selected: Trail, others: [Trail], units: UnitsPreference = .metric) -> TurnCueEngine {
        let route = TurnCueRouteBuilder.build(selected: selected, others: others)
        return TurnCueEngine(route: route, config: TurnCueConfig(units: units))
    }

    /// Selected trail runs north 600 m from (0,0); Junction at (0,300).
    static let mainPts: [XY] = [(0, 0), (0, 600)]
    static func main(_ pts: [XY] = mainPts) -> Trail { trail("main", "Main Trail", pts) }

    /// Another trail leaving (0,300) at `bearing` for 120 m.
    static func fork(bearing: Double, at j: XY = (0, 300), id: String = "fork", name: String = "Fork Trail") -> Trail {
        trail(id, name, [j, offset(j, bearing: bearing, meters: 120)])
    }
}
