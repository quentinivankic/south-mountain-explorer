import Foundation
import Testing
@testable import SouthMountainExplorer

/// Trails stored as several pieces (OSM ways in arbitrary order and direction,
/// with small gaps), e.g. South Mountain National Trail: estimates and turn
/// cues must treat them as one walkable line.
struct SplitTrailCueEstimateTests {
    typealias F = CueFixtures
    typealias H = HikeEstimatesTests

    // MARK: - Synthetic split trail (north-south line, x = 0)

    /// Physical pieces along y: A 0..1000, B 1015..2000 (15 m gap),
    /// C 2073..3000 (73 m gap), D 3178..4000 (178 m gap). Total span 4000 m.
    /// Stored shuffled and partly reversed, like real OSM data.
    static let pieceA: [(Double, Double)] = [(0, 0), (0, 500), (0, 1000)]
    static let pieceB: [(Double, Double)] = [(0, 1015), (0, 1500), (0, 2000)]
    static let pieceC: [(Double, Double)] = [(0, 2073), (0, 2500), (0, 3000)]
    static let pieceD: [(Double, Double)] = [(0, 3178), (0, 3600), (0, 4000)]

    static func hTrail(_ id: String, _ pieces: [[(Double, Double)]], profileFt: [Int]? = nil) -> Trail {
        Trail(
            id: id, name: "Split Trail", distanceMi: 2.5, difficulty: .easy,
            segments: pieces.map { $0.map { H.ll($0.0, $0.1) } },
            profileFt: profileFt
        )
    }

    static var splitTrail: Trail {
        hTrail("split", [pieceC, Array(pieceA.reversed()), pieceD, Array(pieceB.reversed())])
    }

    // MARK: Estimate

    @Test func splitTrailLengthIsPiecesPlusBridgesNotChords() throws {
        let g = try #require(TrailGeometry(trail: Self.splitTrail))
        // Pieces 3734 m plus bridges 266 m.
        #expect(abs(g.total - 4000) < 10)
        #expect(g.hasBridges)
        #expect(g.bridgeAfter.filter { $0 }.count == 3)
    }

    @Test func remainingOnSplitTrailIsTrueDistanceBothDirections() throws {
        let g = try #require(TrailGeometry(trail: Self.splitTrail))
        let route = H.Route([(0, 0), (0, 4000)])
        // Northbound from near the start to y = 1500: 2500 m left.
        var up = TrailProgress()
        up.ingest(path: H.walk(route, from: 5, to: 1500).pts, geometry: g)
        let upLeft = try #require(up.remainingMeters(in: g))
        #expect(abs(upLeft - 2500) < 125)
        // Southbound from near the far end to y = 1500: 1500 m left to the start.
        var down = TrailProgress()
        down.ingest(path: H.walk(route, from: 3995, to: 1500).pts, geometry: g)
        let downLeft = try #require(down.remainingMeters(in: g))
        #expect(abs(downLeft - 1500) < 75)
    }

    @Test func crossingTheBridgedGapsDoesNotGoOffTrail() throws {
        let g = try #require(TrailGeometry(trail: Self.splitTrail))
        let route = H.Route([(0, 0), (0, 4000)])
        var p = TrailProgress()
        p.ingest(path: H.walk(route, from: 5, to: 3500).pts, geometry: g)
        #expect(!p.everOff)
        let left = try #require(p.remainingMeters(in: g))
        #expect(abs(left - 500) < 50)
    }

    @Test func elevationFollowsPiecesAcrossBridges() throws {
        // Flat profile at 200 ft everywhere: any position, including inside a
        // bridge, must read the same elevation.
        let t = Self.hTrail(
            "split", [Self.pieceC, Array(Self.pieceA.reversed()), Self.pieceD, Array(Self.pieceB.reversed())],
            profileFt: Array(repeating: 200, count: 64)
        )
        let g = try #require(TrailGeometry(trail: t))
        for s in stride(from: 0.0, through: g.total, by: 97) {
            #expect(abs((g.elevation(at: s) ?? -1) - 200 * 0.3048) < 1e-6)
        }
    }

    @Test func singlePieceTrailEstimateGeometryIsUnchanged() throws {
        let t = Self.hTrail("single", [[(0, 0), (0, 400), (300, 700)]])
        let g = try #require(TrailGeometry(trail: t))
        let pts = TrailProfile.polyline(t.segments)
        #expect(g.cum == TrailProfile.cumulativeMeters(pts))
        #expect(g.profileArc == nil)
        #expect(!g.hasBridges)
        #expect(g.total == g.cum.last!)
    }

    @Test func alreadyOrderedContiguousPiecesKeepStoredPolyline() throws {
        let t = Self.hTrail("ordered", [[(0, 0), (0, 500)], [(0, 500), (0, 1000)]])
        let g = try #require(TrailGeometry(trail: t))
        #expect(g.cum == TrailProfile.cumulativeMeters(TrailProfile.polyline(t.segments)))
        #expect(g.profileArc == nil)
        #expect(!g.hasBridges)
    }

    // MARK: Cues

    private static func cueTrail(_ id: String, _ pieces: [[F.XY]]) -> Trail {
        F.trail(id, "Split Trail", segments: pieces)
    }

    @Test func splitTrailRouteIsOneChainWithBridges() {
        let a: [F.XY] = [(0, 0), (0, 1000)], b: [F.XY] = [(0, 1015), (0, 2000)]
        let c: [F.XY] = [(0, 2073), (0, 3000)], d: [F.XY] = [(0, 3178), (0, 4000)]
        let route = TurnCueRouteBuilder.build(
            selected: Self.cueTrail("split", [c, a.reversed(), d, b.reversed()]), others: []
        )
        #expect(route.chains.count == 1)
        #expect(route.chains[0].bridges.count == 3)
        #expect(abs(route.chains[0].length - 4000) < 1)
    }

    @Test func forkJustPastWideGapCuesAndNoFalseOffTrail() {
        let a: [F.XY] = [(0, 0), (0, 1000)], b: [F.XY] = [(0, 1015), (0, 2000)]
        let c: [F.XY] = [(0, 2073), (0, 3000)], d: [F.XY] = [(0, 3178), (0, 4000)]
        let selected = Self.cueTrail("split", [c, a.reversed(), d, b.reversed()])
        let fork = F.fork(bearing: 40, at: (0, 3189))
        // A trail crossing the middle of a bridge must not make a junction.
        let crossing = F.trail("cross", "Cross Trail", [(-100, 3100), (100, 3100)])
        var e = F.engine(selected, others: [fork, crossing])
        #expect(TurnCueRouteBuilder.build(selected: selected, others: [fork, crossing]).junctions.count == 1)
        let hits = F.run(&e, F.walk(F.Path([(0, 0), (0, 4000)]), from: 2900, to: 3400))
        let kinds = hits.map(\.event.kind)
        #expect(!kinds.contains(.offTrail))
        #expect(!kinds.contains(.backOnTrail))
        #expect(kinds.contains(.approach))
        #expect(kinds.contains(.atJunction))
        #expect(hits.filter { $0.event.kind != .gpsWeak }.allSatisfy { $0.event.symbol == .keepLeft })
    }

    @Test func singlePieceTrailCuesAreUnchanged() {
        var e = F.engine(F.main(), others: [F.fork(bearing: 320)])
        let hits = F.run(&e, F.walk(F.Path(F.mainPts), from: 10, to: 380))
        #expect(hits.map(\.event.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.event.symbol == .keepRight })
        let route = TurnCueRouteBuilder.build(selected: F.main(), others: [F.fork(bearing: 320)])
        #expect(route.chains.count == 1)
        #expect(route.chains[0].bridges.isEmpty)
    }

    // MARK: Eligibility

    /// Selected trail turns about 30 degrees right at (0,300) with another way
    /// leaving at `branchBearing`.
    private func gentle(branchBearing: Double, turn: Double = 30) -> [TurnCueHit] {
        let out = F.offset((0, 300), bearing: turn, meters: 300)
        let selected = F.trail("bend", "Bend Trail", [(0, 0), (0, 300), out])
        let other = F.fork(bearing: branchBearing, id: "other", name: "Other Trail")
        var e = F.engine(selected, others: [other])
        return F.run(&e, F.walk(F.Path([(0, 0), (0, 300), out]), from: 10, to: 360)).map(TurnCueHit.init)
    }

    struct TurnCueHit { let kind: TurnCueEvent.Kind; let symbol: TurnCueSymbol
        init(_ h: CueFixtures.Hit) { kind = h.event.kind; symbol = h.event.symbol } }

    @Test func gentleThirtyDegreeTurnWithWayAheadCues() {
        // Way ahead 55 degrees left of the trail's new heading: a plausible wrong turn.
        let hits = gentle(branchBearing: 330)
        #expect(hits.map(\.kind) == [.approach, .atJunction])
        #expect(hits.allSatisfy { $0.symbol == .turnRight })
    }

    @Test func gentleTurnWithOnlyABackBranchStaysQuiet() {
        let hits = gentle(branchBearing: 200)
        #expect(hits.isEmpty)
    }

    @Test func straightThroughCrossingStaysQuiet() {
        // Straight trail, other trail leaves at 90 degrees: no plausible wrong turn.
        var e = F.engine(F.main(), others: [F.fork(bearing: 90)])
        #expect(F.run(&e, F.walk(F.Path(F.mainPts), from: 10, to: 380)).isEmpty)
    }

    @Test func sameTrailSwitchbackNeverCues() {
        // Hairpins made of pieces of the selected trail itself: no junctions, no cues.
        let pts: [F.XY] = [(0, 0), (0, 200), (12, 200), (12, 0), (24, 0), (24, 200), (36, 200), (36, 0)]
        var e = F.engine(F.trail("sw", "Switchback Trail", pts), others: [])
        #expect(F.run(&e, F.walk(F.Path(pts), from: 5, to: 790)).isEmpty)
    }

    // MARK: - National Trail (real area data)

    private struct Geo: Decodable { let trails: [Trail] }

    private static func nationalTrail() throws -> (Trail, [Trail]) {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        let geo = try JSONDecoder().decode(Geo.self, from: Data(contentsOf: url))
        let nt = try #require(geo.trails.first { $0.id == "national-trail" })
        return (nt, geo.trails)
    }

    @Test func nationalTrailLengthIsCloseToTrueLength() throws {
        let (nt, _) = try Self.nationalTrail()
        let g = try #require(TrailGeometry(trail: nt))
        // Pieces sum to 24.37 km; with the 178, 73 and 15 m gaps bridged 24.64 km.
        // The old stored-order flattening gave 59.7 km.
        #expect(g.total > 24_300 && g.total < 24_900)
        #expect(g.bridgeAfter.filter { $0 }.count == 3)
        print("REPLAY: national trail computed length m = \(Int(g.total))")
    }

    @Test func nationalTrailOutAndBackReplayListsCuedJunctions() throws {
        let (nt, all) = try Self.nationalTrail()
        let route = TurnCueRouteBuilder.build(selected: nt, others: all)
        #expect(route.chains.count == 1)
        let chain = route.chains[0]
        let path = F.Path(chain.pts.map { (x: $0.x, y: $0.y) })
        func fixes(_ s0: Double, _ s1: Double, t0: Double) -> [TurnCueFix] {
            // Build fixes in the route's own frame.
            let m = TurnCueProjector.metersPerDegree
            let cosLat = cos(route.origin.originLat * .pi / 180)
            var out: [TurnCueFix] = []
            let dir: Double = s1 >= s0 ? 1 : -1
            var k = 0
            while true {
                let s = s0 + dir * 1.4 * Double(k)
                if (s1 - s) * dir < 0 { break }
                let p = path.position(at: s)
                out.append(TurnCueFix(
                    lat: route.origin.originLat + p.y / m,
                    lon: route.origin.originLon + p.x / (m * cosLat),
                    timestamp: t0 + Double(k), horizontalAccuracy: 5, speed: 1.4
                ))
                k += 1
            }
            return out
        }
        let len = chain.length
        var lines: [String] = []
        var results: [String: Set<Int>] = [:]
        for (label, a, b) in [("forward", 10.0, len - 10), ("backward", len - 10, 10.0)] {
            var e = TurnCueEngine(route: route, config: TurnCueConfig(units: .metric))
            let hits = F.run(&e, fixes(a, b, t0: F.t0))
            let cued = hits.filter { $0.event.kind == .atJunction }
            for h in cued {
                let j = route.junctions.first { $0.id == h.event.junctionId }
                lines.append("REPLAY: \(label) cue junction \(h.event.junctionId ?? -1) at arc \(Int(j?.arc ?? -1)) m: \(h.event.spoken)")
            }
            lines.append("REPLAY: \(label) off-trail events = \(hits.filter { $0.event.kind == .offTrail }.count)")
            results[label] = Set(cued.compactMap(\.event.junctionId))
            #expect(!hits.contains { $0.event.kind == .offTrail })
            // J18 sits just past the 178 m gap: it must get its approach cue too.
            #expect(hits.contains { $0.event.kind == .approach && $0.event.junctionId == 18 })
        }
        lines.append("REPLAY: junctions=\(route.junctions.count) chains=\(route.chains.count) length m=\(Int(len))")
        lines.forEach { print($0) }
        // Pinned from the replay above (junction ids are stable for this data file).
        #expect(results["forward"] == [0, 2, 3, 4, 7, 11, 12, 13, 14, 16, 17, 18, 20])
        #expect(results["backward"] == [18, 17, 16, 14, 13, 11, 8, 5, 4, 3, 0])
    }
}
