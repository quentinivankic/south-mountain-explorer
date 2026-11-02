import Foundation
import Testing
@testable import SouthMountainExplorer

/// Synthetic scenarios are laid out in local metres (x east, y north) around a
/// fixed origin, then converted to lat/lon so the builder's own projection is
/// exercised. The real-fixture tests read the repo's South Mountain geom
/// directly from `public/areas/geom/` via `#filePath`; the test bundle does not
/// copy it.
struct TrailNetworkTests {
    typealias Key = TrailNetwork.TrailKey

    // MARK: - Helpers

    private static let lat0 = 33.34
    private static let lon0 = -112.0

    private static func ll(_ x: Double, _ y: Double) -> [Double] {
        let mpd = TurnCueProjector.metersPerDegree
        return [lat0 + y / mpd, lon0 + x / (cos(lat0 * .pi / 180) * mpd)]
    }

    /// Straight polyline with a vertex about every `step` metres.
    private static func line(_ x0: Double, _ y0: Double, _ x1: Double, _ y1: Double,
                             step: Double = 20) -> [[Double]] {
        let n = max(1, Int((hypot(x1 - x0, y1 - y0) / step).rounded()))
        return (0...n).map { i in
            let t = Double(i) / Double(n)
            return ll(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
        }
    }

    private static func trail(_ id: String, _ segments: [[[Double]]],
                              profile: [Int]? = nil) -> Trail {
        Trail(id: id, name: id, distanceMi: 0, difficulty: .easy,
              segments: segments, gainFt: nil, profileFt: profile)
    }

    private static func network(_ trails: [Trail]) -> TrailNetwork {
        TrailNetwork(trails: trails.map { .init(areaId: "a", trail: $0) })
    }

    private static func key(_ id: String) -> Key { Key(areaId: "a", trailId: id) }

    private func totalLength(_ n: TrailNetwork) -> Double {
        n.edges.filter { $0.kind == .trail }.reduce(0) { $0 + $1.lengthMeters }
    }

    // MARK: - Junctions

    @Test func tJunctionSplitsHostMidEdge() {
        // B stops 4 m short of A's interior (no shared vertex).
        let a = Self.trail("A", [Self.line(0, 0, 200, 0)])
        let b = Self.trail("B", [Self.line(100, 100, 100, 4)])
        let n = Self.network([a, b])
        #expect(n.edges.count == 3)
        #expect(n.nodes.count == 4)
        #expect(n.bridgeEdges.isEmpty)
        let aEdges = n.edgeIds(of: Self.key("A")).map { n.edges[$0] }
        #expect(aEdges.count == 2)
        for e in aEdges { #expect(abs(e.lengthMeters - 100) < 1) }
        let bEdges = n.edgeIds(of: Self.key("B"))
        #expect(bEdges.count == 1)
        #expect(abs(n.edges[bEdges[0]].lengthMeters - 100) < 1)
        #expect(n.nodes.filter { $0.edgeIds.count == 3 }.count == 1)
    }

    @Test func tJunctionAtSharedVertex() {
        let a = Self.trail("A", [Self.line(0, 0, 200, 0)])
        let b = Self.trail("B", [Self.line(100, 100, 100, 0)])
        let n = Self.network([a, b])
        #expect(n.edges.count == 3)
        #expect(n.nodes.count == 4)
        #expect(n.bridgeEdges.isEmpty)
        #expect(n.edgeIds(of: Self.key("A")).count == 2)
        #expect(n.edgeIds(of: Self.key("B")).count == 1)
    }

    @Test func xCrossingSplitsBothTrails() {
        let a = Self.trail("A", [[Self.ll(0, -100), Self.ll(0, 100)]])
        let b = Self.trail("B", [[Self.ll(-100, 0), Self.ll(100, 0)]])
        let n = Self.network([a, b])
        #expect(n.edges.count == 4)
        #expect(n.nodes.count == 5)
        #expect(n.edgeIds(of: Self.key("A")).count == 2)
        #expect(n.edgeIds(of: Self.key("B")).count == 2)
        #expect(n.nodes.filter { $0.edgeIds.count == 4 }.count == 1)
        for e in n.edges { #expect(abs(e.lengthMeters - 100) < 0.5) }
    }

    // MARK: - Co-aligned stretches

    @Test func coAlignedStretchIsOneEdgeWithBothTrails() {
        let a = Self.trail("A", [Self.line(0, 0, 300, 0, step: 25)])
        let b = Self.trail("B", [Self.line(100, 0, 200, 0, step: 25)])
        let n = Self.network([a, b])
        #expect(n.sharedEdges.count == 1)
        let shared = n.sharedEdges[0]
        #expect(shared.trailKeys == [Self.key("A"), Self.key("B")])
        #expect(abs(shared.lengthMeters - 100) < 0.5)
        #expect(n.edges.count == 3)
        // 300 m of path, not 400: the shared 100 m is counted once.
        #expect(abs(totalLength(n) - 300) < 0.5)
        #expect(n.edgeIds(of: Self.key("B")) == [shared.id])
        #expect(n.edgeIds(of: Self.key("A")).count == 3)
    }

    @Test func coAlignedWithDifferentVertices() {
        // B runs the same path but with its own vertices (offset 0.4 m,
        // different spacing), so nothing is shared exactly.
        let a = Self.trail("A", [Self.line(0, 0, 300, 0, step: 25)])
        let b = Self.trail("B", [Self.line(100, 0.4, 200, 0.4, step: 33)])
        let n = Self.network([a, b])
        #expect(n.sharedEdges.count == 1)
        #expect(abs(totalLength(n) - 300) < 1)
    }

    // MARK: - Gaps and bridges

    @Test func twentyMetreGapIsBridgedAndFlagged() {
        let a = Self.trail("A", [Self.line(0, 0, 100, 0)])
        let b = Self.trail("B", [Self.line(120, 0, 220, 0)])
        let n = Self.network([a, b])
        #expect(n.edges.count == 3)
        #expect(n.bridgeEdges.count == 1)
        let bridge = n.bridgeEdges[0]
        #expect(bridge.isUnverifiedConnection)
        #expect(abs(bridge.lengthMeters - 20) < 0.5)
        #expect(bridge.trailKeys == [Self.key("A"), Self.key("B")])
        // A bridge is not part of either trail.
        #expect(n.edgeIds(of: Self.key("A")).count == 1)
        #expect(n.edgeIds(of: Self.key("B")).count == 1)
    }

    @Test(arguments: [(29.0, true), (31.0, false), (40.0, false)])
    func bridgeThreshold(gap: Double, bridged: Bool) {
        let a = Self.trail("A", [Self.line(0, 0, 100, 0)])
        let b = Self.trail("B", [Self.line(100 + gap, 0, 200 + gap, 0)])
        let n = Self.network([a, b])
        #expect(n.bridgeEdges.count == (bridged ? 1 : 0))
        #expect(n.edges.count == (bridged ? 3 : 2))
        #expect(n.nodes.count == 4)
    }

    @Test func gapBetweenPiecesOfOneTrailFollowsTheSameRule() {
        let near = Self.trail("N", [Self.line(0, 0, 100, 0), Self.line(112, 0, 200, 0)])
        let far = Self.trail("F", [Self.line(0, 500, 100, 500), Self.line(140, 500, 200, 500)])
        let n = Self.network([near, far])
        #expect(n.bridgeEdges.count == 1)
        #expect(n.bridgeEdges[0].trailKeys == [Self.key("N")])
        #expect(abs((n.maxPieceGapMeters(of: Self.key("N")) ?? -1) - 12) < 0.5)
        #expect(abs((n.maxPieceGapMeters(of: Self.key("F")) ?? -1) - 40) < 0.5)
        #expect(n.maxPieceGapMeters(of: Self.key("nope")) == nil)
    }

    @Test func singlePieceTrailHasNoGap() {
        let n = Self.network([Self.trail("A", [Self.line(0, 0, 100, 0)])])
        #expect(n.maxPieceGapMeters(of: Self.key("A")) == 0)
    }

    // MARK: - Multi-segment trails

    @Test func shuffledPiecesStitchBackIntoOneTrail() {
        // Stored out of order and with a reversed piece; ends coincide.
        let m = Self.trail("M", [
            Self.line(200, 0, 300, 0),
            Self.line(100, 0, 0, 0),
            Self.line(100, 0, 200, 0),
        ])
        let n = Self.network([m])
        #expect(n.edges.count == 1)
        #expect(abs(n.edges[0].lengthMeters - 300) < 0.5)
        #expect(n.edgeIds(of: Self.key("M")) == [0])
        #expect(n.maxPieceGapMeters(of: Self.key("M")) == 0)
    }

    @Test func multiSegmentEdgesMapBackToTheirTrail() {
        let m = Self.trail("M", [
            Self.line(200, 0, 300, 0),
            Self.line(100, 0, 0, 0),
            Self.line(100, 0, 200, 0),
        ])
        // Crosses M in the middle of its third piece.
        let x = Self.trail("X", [[Self.ll(150, 50), Self.ll(150, -50)]])
        let n = Self.network([m, x])
        let mEdges = n.edgeIds(of: Self.key("M")).map { n.edges[$0] }
        #expect(mEdges.count == 2)
        #expect(abs(mEdges.reduce(0) { $0 + $1.lengthMeters } - 300) < 0.5)
        for e in mEdges { #expect(abs(e.lengthMeters - 150) < 0.5) }
        #expect(n.edgeIds(of: Self.key("X")).count == 2)
        #expect(n.edges.allSatisfy { $0.kind == .trail })
    }

    @Test func sameTrailIdInTwoAreasStaysSeparate() {
        let t = Self.trail("ice-age-trail", [Self.line(0, 0, 100, 0)])
        let u = Self.trail("ice-age-trail", [Self.line(0, 1000, 100, 1000)])
        let n = TrailNetwork(trails: [.init(areaId: "one", trail: t), .init(areaId: "two", trail: u)])
        #expect(n.edges.count == 2)
        #expect(n.edgeIds(of: Key(areaId: "one", trailId: "ice-age-trail")).count == 1)
        #expect(n.edgeIds(of: Key(areaId: "two", trailId: "ice-age-trail")).count == 1)
    }

    // MARK: - Elevation

    /// Eastbound gain of an edge, whichever way it is stored.
    private func eastGain(_ e: TrailNetwork.Edge) -> (up: Double, down: Double) {
        let eastward = e.polyline.first!.x < e.polyline.last!.x
        return eastward ? (e.gainForwardFt, e.gainBackwardFt) : (e.gainBackwardFt, e.gainForwardFt)
    }

    @Test func gainForwardAndBackwardOnASlopedTrail() {
        // 1000 ft to 1500 ft, evenly across 1000 m, cut in half by a crossing.
        let profile = Array(stride(from: 1000, through: 1500, by: 25))
        let a = Self.trail("A", [Self.line(0, 0, 1000, 0, step: 50)], profile: profile)
        let x = Self.trail("X", [[Self.ll(500, -50), Self.ll(500, 50)]])
        let n = Self.network([a, x])
        let aEdges = n.edgeIds(of: Self.key("A")).map { n.edges[$0] }
        #expect(aEdges.count == 2)
        for e in aEdges {
            #expect(e.elevationKnown)
            let g = eastGain(e)
            #expect(abs(g.up - 250) < 2)
            #expect(g.down == 0)
        }
        // The crossing trail has no profile.
        for id in n.edgeIds(of: Self.key("X")) {
            #expect(!n.edges[id].elevationKnown)
            #expect(n.edges[id].gainForwardFt == 0 && n.edges[id].gainBackwardFt == 0)
        }
    }

    @Test func upThenDownHasGainInBothDirections() {
        let a = Self.trail("A", [Self.line(0, 0, 1000, 0, step: 50)], profile: [0, 100, 0])
        let n = Self.network([a])
        #expect(n.edges.count == 1)
        #expect(abs(n.edges[0].gainForwardFt - 100) < 1)
        #expect(abs(n.edges[0].gainBackwardFt - 100) < 1)
    }

    @Test func missingProfileMeansZeroGainAndUnknown() {
        let n = Self.network([Self.trail("A", [Self.line(0, 0, 500, 0)])])
        #expect(n.edges[0].elevationKnown == false)
        #expect(n.edges[0].gainForwardFt == 0)
        #expect(n.edges[0].gainBackwardFt == 0)
    }

    @Test func profileFollowsThePipelineChainNotStoredOrder() {
        // Stored [east half, west half]. The elevation pipeline chains them
        // east to west (first zero-gap chain it tries), so profile index 0 is
        // x = 1000 and the profile rises as x DECREASES. Reading it in stored
        // order would invert the west half.
        let profile = Array(stride(from: 0, through: 1000, by: 100))
        let m = Self.trail("M", [Self.line(500, 0, 1000, 0, step: 50),
                                 Self.line(0, 0, 500, 0, step: 50)], profile: profile)
        let x = Self.trail("X", [[Self.ll(500, -50), Self.ll(500, 50)]])
        let n = Self.network([m, x])
        // The origin is the bbox centre, so find the west edge by position.
        func meanX(_ e: TrailNetwork.Edge) -> Double {
            e.polyline.reduce(0.0) { $0 + $1.x } / Double(e.polyline.count)
        }
        let mEdges = n.edgeIds(of: Self.key("M")).map { n.edges[$0] }
        let west = mEdges.min { meanX($0) < meanX($1) }!
        let g = eastGain(west)
        #expect(mEdges.count == 2)
        #expect(abs(g.up - 0) < 2)
        #expect(abs(g.down - 500) < 2)
    }

    // MARK: - Determinism

    @Test func inputOrderDoesNotChangeTheNetwork() {
        let a = Self.trail("A", [Self.line(0, 0, 200, 0)])
        let b = Self.trail("B", [Self.line(100, 100, 100, 4)])
        let c = Self.trail("C", [[Self.ll(50, -50), Self.ll(50, 50)]])
        let one = Self.network([a, b, c])
        let two = Self.network([c, b, a])
        #expect(one.edges.map(\.lengthMeters) == two.edges.map(\.lengthMeters))
        #expect(one.edges.map(\.trailKeys) == two.edges.map(\.trailKeys))
        #expect(one.nodes.map(\.point) == two.nodes.map(\.point))
    }

    // MARK: - Real fixture: South Mountain

    private struct GeomFile: Decodable { let id: String; let trails: [Trail] }

    private static func loadSouthMountain() throws -> GeomFile {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // SouthMountainExplorerTests
            .deletingLastPathComponent()   // ios
            .deletingLastPathComponent()   // repo root
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        return try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
    }

    private static func localLength(_ t: Trail, _ proj: TurnCueProjector) -> Double {
        var sum = 0.0
        for seg in t.segments {
            let pts = seg.filter { $0.count >= 2 }.map { proj.local(lat: $0[0], lon: $0[1]) }
            if pts.count > 1 { sum += TurnCueGeometry.cumulative(pts).last! }
        }
        return sum
    }

    /// Trail length with exact retraced segments (same 5-dp endpoints, either
    /// direction) counted once.
    private static func uniqueLength(_ t: Trail, _ proj: TurnCueProjector) -> Double {
        var seen = Set<String>()
        var sum = 0.0
        for seg in t.segments {
            let pts = seg.filter { $0.count >= 2 }
            for (a, b) in zip(pts, pts.dropFirst()) {
                let ka = String(format: "%.5f,%.5f", a[0], a[1])
                let kb = String(format: "%.5f,%.5f", b[0], b[1])
                guard seen.insert(ka < kb ? ka + ";" + kb : kb + ";" + ka).inserted else { continue }
                sum += TurnCueGeometry.distance(proj.local(lat: a[0], lon: a[1]),
                                                proj.local(lat: b[0], lon: b[1]))
            }
        }
        return sum
    }

    @Test func southMountainBuildsAndIsConsistent() throws {
        let geom = try Self.loadSouthMountain()
        #expect(geom.trails.count == 77)
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }

        let clock = ContinuousClock()
        var net: TrailNetwork!
        let elapsed = clock.measure { net = TrailNetwork(trails: inputs) }
        let ms = Double(elapsed.components.seconds) * 1000
            + Double(elapsed.components.attoseconds) / 1e15

        // Every trail maps to at least one edge.
        for t in geom.trails {
            #expect(!net.edgeIds(of: TrailNetwork.TrailKey(areaId: geom.id, trailId: t.id)).isEmpty,
                    "no edge for \(t.id)")
        }

        // Length bookkeeping. Some trails list the same path twice inside one
        // trail (Desert Classic's second piece retraces 6.3 km of its first,
        // and `distanceMi` counts both), so a trail's expected length is its
        // UNIQUE stretch: segments deduplicated on 5-dp endpoints. The network
        // must also count each cross-trail shared stretch once. Remaining
        // differences are endpoint snaps (<= 6 m each) and dropped slivers.
        var trailSum = 0.0       // raw summed segment length
        var uniqueSum = 0.0      // per-trail length without self-retracing
        for t in geom.trails {
            trailSum += Self.localLength(t, net.projector)
            uniqueSum += Self.uniqueLength(t, net.projector)
        }
        let selfRetrace = trailSum - uniqueSum
        let perTrail = net.edges.filter { $0.kind == .trail }
            .reduce(0.0) { $0 + $1.lengthMeters * Double($1.trailKeys.count) }
        let unique = net.edges.filter { $0.kind == .trail }.reduce(0.0) { $0 + $1.lengthMeters }
        let shared = net.sharedEdges.reduce(0.0) { $0 + $1.lengthMeters * Double($1.trailKeys.count - 1) }
        // Tolerance: 0.5% of total trail length (~1 km of ~197 km).
        let drift = abs(perTrail - uniqueSum) / uniqueSum
        #expect(drift < 0.005, "per-trail edge length drifts \(drift * 100)% from unique trail length")
        #expect(abs(unique - (uniqueSum - shared)) / uniqueSum < 0.005)
        #expect(unique < uniqueSum)   // there IS sharing in this area
        for t in geom.trails {
            let key = TrailNetwork.TrailKey(areaId: geom.id, trailId: t.id)
            let want = Self.uniqueLength(t, net.projector)
            let got = net.edgeIds(of: key).reduce(0.0) { $0 + net.edges[$1].lengthMeters }
            if abs(want - got) > max(25, want * 0.02) {
                print("TRAILNETWORK DIAG \(t.id) uniqueLen=\(Int(want)) edgeLen=\(Int(got))")
            }
        }
        let bridgeLen = net.bridgeEdges.reduce(0.0) { $0 + $1.lengthMeters }
        #expect(net.bridgeEdges.allSatisfy { $0.lengthMeters <= 30.001 })
        #expect(ms < 2000, "build took \(ms) ms")

        print("TRAILNETWORK stats: nodes=\(net.nodes.count) edges=\(net.edges.count) "
              + "trailEdges=\(net.edges.count - net.bridgeEdges.count) bridges=\(net.bridgeEdges.count) "
              + "shared=\(net.sharedEdges.count) buildMs=\(String(format: "%.1f", ms))")
        print("TRAILNETWORK lengths: trailSumM=\(String(format: "%.1f", trailSum)) "
              + "selfRetraceM=\(String(format: "%.1f", selfRetrace)) uniqueTrailSumM=\(String(format: "%.1f", uniqueSum)) "
              + "perTrailEdgeSumM=\(String(format: "%.1f", perTrail)) uniqueM=\(String(format: "%.1f", unique)) "
              + "sharedM=\(String(format: "%.1f", shared)) bridgeM=\(String(format: "%.1f", bridgeLen)) "
              + "driftPct=\(String(format: "%.3f", drift * 100)) "
              + "distanceMiSumM=\(String(format: "%.1f", geom.trails.reduce(0) { $0 + $1.distanceMi } * 1609.344))")
        let gaps = geom.trails.compactMap { t in
            net.maxPieceGapMeters(of: TrailNetwork.TrailKey(areaId: geom.id, trailId: t.id)).map { (t.id, $0) }
        }.filter { $0.1 > 0 }.sorted { $0.1 > $1.1 }
        print("TRAILNETWORK splitTrails: \(gaps.prefix(8).map { "\($0.0)=\(Int($0.1))m" })")
        func components(includeBridges: Bool) -> Int {
            var parent = Array(0..<net.nodes.count)
            func find(_ x: Int) -> Int {
                var r = x
                while parent[r] != r { r = parent[r] }
                return r
            }
            for e in net.edges where includeBridges || e.kind == .trail {
                parent[find(e.from)] = find(e.to)
            }
            return Set(net.nodes.indices.map(find)).count
        }
        let lengths = net.edges.map(\.lengthMeters).sorted()
        let degree = Dictionary(grouping: net.nodes, by: { $0.edgeIds.count }).mapValues(\.count)
        print("TRAILNETWORK shape: components(withBridges)=\(components(includeBridges: true)) "
              + "components(noBridges)=\(components(includeBridges: false)) "
              + "edgeLenMin=\(Int(lengths.first ?? 0)) median=\(Int(lengths[lengths.count / 2])) max=\(Int(lengths.last ?? 0)) "
              + "edgesUnder20m=\(lengths.filter { $0 < 20 }.count) degreeHistogram=\(degree.sorted { $0.key < $1.key })")
        print("TRAILNETWORK bridges: \(net.bridgeEdges.map { "\(Int($0.lengthMeters.rounded()))m" })")
        let unknown = net.edges.filter { $0.kind == .trail && !$0.elevationKnown }.count
        print("TRAILNETWORK elevationUnknownEdges=\(unknown)")
    }

    @Test func southMountainJunctionsOfNamedTrails() throws {
        let geom = try Self.loadSouthMountain()
        let net = TrailNetwork(trails: geom.trails.map { .init(areaId: geom.id, trail: $0) })
        func k(_ id: String) -> Key { Key(areaId: geom.id, trailId: id) }
        func junctions(_ a: String, _ b: String) -> [String] {
            net.nodes.filter { node in
                let keys = Set(node.edgeIds.flatMap { net.edges[$0].trailKeys })
                return keys.contains(k(a)) && keys.contains(k(b))
            }.map { String(format: "(%.5f, %.5f)", $0.coordinate.lat, $0.coordinate.lon) }
        }
        let pairs = [("national-trail", "mormon-trail"), ("national-trail", "kiwanis-trail"),
                     ("mormon-trail", "kiwanis-trail"), ("national-trail", "desert-classic-trail"),
                     ("mormon-trail", "mormon-loop-trail"), ("hidden-valley-trail", "national-trail")]
        for (a, b) in pairs {
            print("TRAILNETWORK junction \(a) x \(b): \(junctions(a, b))")
        }
        // National Trail meets Mormon Trail and Kiwanis Trail somewhere.
        #expect(!junctions("national-trail", "mormon-trail").isEmpty)
        #expect(!junctions("national-trail", "kiwanis-trail").isEmpty)
    }

    @Test func southMountainIsInputOrderIndependent() throws {
        let geom = try Self.loadSouthMountain()
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        let a = TrailNetwork(trails: inputs)
        let b = TrailNetwork(trails: inputs.reversed())
        #expect(a.nodes.count == b.nodes.count)
        #expect(a.edges.map(\.lengthMeters) == b.edges.map(\.lengthMeters))
        #expect(a.edges.map(\.trailKeys) == b.edges.map(\.trailKeys))
    }
}
