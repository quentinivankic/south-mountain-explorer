import Foundation
import Testing
@testable import SouthMountainExplorer

/// Start / end point snapping for "Plan a Hike" (Task 2). Synthetic scenes are
/// laid out in local metres (x east, y north) around a fixed origin; the
/// real-fixture test reads the repo's South Mountain geom via `#filePath`.
struct TrailNetworkStartTests {
    typealias Key = TrailNetwork.TrailKey

    // MARK: - Helpers

    private static let lat0 = 33.34
    private static let lon0 = -112.0

    private static func ll(_ x: Double, _ y: Double) -> [Double] {
        let mpd = TurnCueProjector.metersPerDegree
        return [lat0 + y / mpd, lon0 + x / (cos(lat0 * .pi / 180) * mpd)]
    }

    private static func line(_ x0: Double, _ y0: Double, _ x1: Double, _ y1: Double,
                             step: Double = 20) -> [[Double]] {
        let n = max(1, Int((hypot(x1 - x0, y1 - y0) / step).rounded()))
        return (0...n).map { i in
            let t = Double(i) / Double(n)
            return ll(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
        }
    }

    private static func trail(_ id: String, _ segments: [[[Double]]], profile: [Int]? = nil) -> Trail {
        Trail(id: id, name: id, distanceMi: 0, difficulty: .easy,
              segments: segments, gainFt: nil, profileFt: profile)
    }

    private static func network(_ trails: [Trail]) -> TrailNetwork {
        TrailNetwork(trails: trails.map { .init(areaId: "a", trail: $0) })
    }

    private static func pin(_ x: Double, _ y: Double) -> StartPoint {
        let c = ll(x, y)
        return .pin(lat: c[0], lon: c[1])
    }

    private static func key(_ id: String) -> Key { Key(areaId: "a", trailId: id) }

    /// One 400 m east-west trail from (0,0) to (400,0), 100 ft linear climb.
    private static func straight400() -> TrailNetwork {
        let profile = (0...10).map { $0 * 10 }
        return network([trail("A", [line(0, 0, 400, 0)], profile: profile)])
    }

    /// Graph invariants that must hold for the base AND every spliced view.
    private func expectConsistent(_ n: TrailNetwork, sourceLocation: SourceLocation = #_sourceLocation) {
        for (i, e) in n.edges.enumerated() {
            #expect(e.id == i, "edge id", sourceLocation: sourceLocation)
            #expect(n.nodes[e.from].edgeIds.contains(e.id), "from node lists edge", sourceLocation: sourceLocation)
            #expect(n.nodes[e.to].edgeIds.contains(e.id), "to node lists edge", sourceLocation: sourceLocation)
            #expect(e.polyline.count == e.coordinates.count, sourceLocation: sourceLocation)
            #expect(e.polyline.count >= 2, sourceLocation: sourceLocation)
            let poly = zip(e.polyline, e.polyline.dropFirst()).reduce(0.0) {
                $0 + TurnCueGeometry.distance($1.0, $1.1)
            }
            #expect(abs(poly - e.lengthMeters) < 0.01, "length matches polyline", sourceLocation: sourceLocation)
            let a = e.polyline.first!, b = e.polyline.last!
            #expect(TurnCueGeometry.distance(a, n.nodes[e.from].point) < 0.01, sourceLocation: sourceLocation)
            #expect(TurnCueGeometry.distance(b, n.nodes[e.to].point) < 0.01, sourceLocation: sourceLocation)
        }
        for node in n.nodes {
            #expect(node.edgeIds == node.edgeIds.sorted(), sourceLocation: sourceLocation)
            for id in node.edgeIds {
                let e = n.edges[id]
                #expect(e.from == node.id || e.to == node.id, "node lists only its edges", sourceLocation: sourceLocation)
            }
        }
        for key in n.trailKeys {
            for id in n.edgeIds(of: key) {
                #expect(n.edges[id].trailKeys.contains(key), sourceLocation: sourceLocation)
                #expect(n.edges[id].kind == .trail, sourceLocation: sourceLocation)
            }
        }
    }

    /// A comparable fingerprint of a whole network.
    private func fingerprint(_ n: TrailNetwork) -> [Double] {
        var out: [Double] = [Double(n.nodes.count), Double(n.edges.count)]
        for node in n.nodes {
            out += [node.point.x, node.point.y, Double(node.edgeIds.count)] + node.edgeIds.map(Double.init)
        }
        for e in n.edges {
            out += [Double(e.id), Double(e.from), Double(e.to), e.lengthMeters, e.gainForwardFt,
                    e.gainBackwardFt, Double(e.polyline.count), Double(e.trailKeys.count)]
        }
        for key in n.trailKeys { out += n.edgeIds(of: key).map(Double.init) + [-1] }
        return out
    }

    // MARK: - On / off trail

    @Test func pinOnTrailAddsNoConnector() throws {
        let base = Self.straight400()
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(100, 3))   // 3 m off, inside the 10 m tolerance
        #expect(r.start.isOffTrail == false)
        #expect(r.start.connectorEdgeId == nil)
        #expect(r.start.connectorMeters == 0)
        #expect(r.start.nodeId == r.start.trailNodeId)
        #expect(r.network.edges.allSatisfy { $0.kind != .connector })
        #expect(abs(r.start.snap.distanceMeters - 3) < 0.05)
        #expect(abs(r.start.snap.arcMeters - 100) < 0.05)
        // The edge was split in two at the snap point.
        #expect(base.edges.count == 1 && r.network.edges.count == 2)
        #expect(r.network.nodes.count == base.nodes.count + 1)
        expectConsistent(r.network)
    }

    @Test func toleranceEdgeIsTenMetres() throws {
        let snapper = TrailNetworkSnapper(network: Self.straight400())
        #expect(try snapper.insert(start: Self.pin(100, 9.9)).start.isOffTrail == false)
        #expect(try snapper.insert(start: Self.pin(100, 10.5)).start.isOffTrail == true)
    }

    @Test func pinTwoHundredMetresOffGetsFlatFlaggedConnector() throws {
        let base = Self.straight400()
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(100, 200))
        #expect(r.start.isOffTrail)
        let id = try #require(r.start.connectorEdgeId)
        let c = r.network.edges[id]
        #expect(c.kind == .connector)
        #expect(c.isOffTrail)
        #expect(c.isUnverifiedConnection == false)
        #expect(abs(c.lengthMeters - 200) < 0.2)
        #expect(abs(r.start.connectorMeters - c.lengthMeters) < 1e-9)
        #expect(c.gainForwardFt == 0 && c.gainBackwardFt == 0)
        #expect(c.elevationKnown == false)
        #expect(c.trailKeys.isEmpty)
        #expect(c.from == r.start.nodeId && c.to == r.start.trailNodeId)
        #expect(r.start.nodeId != r.start.trailNodeId)
        #expect(c.polyline.count == 2)
        // The connector's far end sits on the trail at x = 100.
        let end = c.polyline[1]
        #expect(abs(end.y) < 0.05)
        #expect(abs(abs(end.x - c.polyline[0].x)) < 0.05)
        // Connectors are never part of a trail.
        for key in r.network.trailKeys { #expect(!r.network.edgeIds(of: key).contains(id)) }
        #expect(r.network.bridgeEdges.isEmpty)
        expectConsistent(r.network)
    }

    @Test func pinExactlyAtANodeDoesNotSplit() throws {
        let base = Self.straight400()
        let snapper = TrailNetworkSnapper(network: base)
        let node = base.nodes[0]
        let r = try snapper.insert(start: .pin(lat: node.coordinate.lat, lon: node.coordinate.lon))
        #expect(r.network.edges.count == base.edges.count)
        #expect(r.network.nodes.count == base.nodes.count)
        #expect(r.start.nodeId == node.id)
        #expect(r.start.isOffTrail == false)
        expectConsistent(r.network)
        // 0.6 m from the end node still merges into it (no sliver edge).
        let near = Self.pin(0.6, 0)
        let r2 = try snapper.insert(start: near)
        #expect(r2.network.edges.count == base.edges.count)
    }

    @Test func offTrailPinNextToANodeConnectsToTheNodeWithoutSplitting() throws {
        let base = Self.straight400()
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(-50, 0))   // beyond the west end
        #expect(r.start.isOffTrail)
        #expect(abs(r.start.connectorMeters - 50) < 0.2)
        #expect(r.network.edges.count == base.edges.count + 1)   // connector only
        #expect(r.network.nodes.count == base.nodes.count + 1)   // start node only
        expectConsistent(r.network)
    }

    // MARK: - Splitting

    @Test func splitPreservesLengthKeysKindAndGain() throws {
        let base = Self.straight400()
        let original = base.edges[0]
        #expect(original.elevationKnown)
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(100, 3))
        let n = r.network
        #expect(n.edges.count == 2)
        let halves = n.edges.filter { $0.kind == .trail }
        #expect(halves.count == 2)
        // Lengths sum to the original; the cut is where the pin projects.
        #expect(abs(halves.reduce(0) { $0 + $1.lengthMeters } - original.lengthMeters) < 1e-6)
        let mid = n.nodes[r.start.trailNodeId]
        let first = try #require(halves.first { $0.to == mid.id || $0.from == mid.id })
        _ = first
        let lengths = halves.map(\.lengthMeters).sorted()
        #expect(abs(lengths[0] - 100) < 0.1 && abs(lengths[1] - 300) < 0.1)
        for h in halves {
            #expect(h.kind == .trail)
            #expect(h.trailKeys == original.trailKeys)
            #expect(h.elevationKnown)
            // Linear profile: gain is proportional to arc.
            let frac = h.lengthMeters / original.lengthMeters
            #expect(abs(h.gainForwardFt - original.gainForwardFt * frac) < 1e-6)
            #expect(abs(h.gainBackwardFt - original.gainBackwardFt * frac) < 1e-6)
            // Both halves touch the split node.
            #expect(h.from == mid.id || h.to == mid.id)
        }
        #expect(abs(halves.reduce(0) { $0 + $1.gainForwardFt } - original.gainForwardFt) < 1e-6)
        #expect(abs(halves.reduce(0) { $0 + $1.gainBackwardFt } - original.gainBackwardFt) < 1e-6)
        // The trail still owns both halves.
        #expect(Set(n.edgeIds(of: Self.key("A"))) == Set(halves.map(\.id)))
        #expect(mid.edgeIds.count == 2)
        expectConsistent(n)
    }

    @Test func splitKeepsSharedEdgeTrailKeys() throws {
        // A and B run together from x=100 to 300; pin lands inside the shared stretch.
        let a = Self.trail("A", [Self.line(0, 0, 400, 0, step: 25)])
        let b = Self.trail("B", [Self.line(100, 0, 300, 0, step: 25)])
        let base = Self.network([a, b])
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(200, 2))
        let n = r.network
        #expect(n.sharedEdges.count == 2)
        #expect(n.sharedEdges.allSatisfy { $0.trailKeys == [Self.key("A"), Self.key("B")] })
        #expect(n.sharedEdges.reduce(0) { $0 + $1.lengthMeters } > 199.9)
        #expect(n.edgeIds(of: Self.key("B")).count == 2)
        #expect(n.edgeIds(of: Self.key("A")).count == 4)
        expectConsistent(n)
    }

    @Test func splitMultiVertexEdgeKeepsGeometry() throws {
        // A bent trail: the pin projects onto the second leg.
        let pts = Self.line(0, 0, 200, 0) + Self.line(200, 0, 200, 200).dropFirst()
        let base = Self.network([Self.trail("L", [Array(pts)])])
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(203, 120))
        #expect(abs(r.start.snap.arcMeters - 320) < 0.1)
        let halves = r.network.edges.filter { $0.kind == .trail }.sorted { $0.lengthMeters < $1.lengthMeters }
        #expect(abs(halves[0].lengthMeters - 80) < 0.1 && abs(halves[1].lengthMeters - 320) < 0.1)
        // The 320 m half still contains the corner vertex.
        let corner = Self.ll(200, 0)
        #expect(halves[1].coordinates.contains {
            abs($0.lat - corner[0]) < 1e-6 && abs($0.lon - corner[1]) < 1e-6
        })
        #expect(halves[1].polyline.count > 5)
        expectConsistent(r.network)
    }

    @Test func splitClosedLoopKeepsNodeBookkeeping() throws {
        // A square loop that closes on itself: a single edge with from == to.
        let loop = [Self.ll(0, 0), Self.ll(100, 0), Self.ll(100, 100), Self.ll(0, 100), Self.ll(0, 0)]
        let base = Self.network([Self.trail("O", [loop])])
        #expect(base.edges.count == 1 && base.edges[0].from == base.edges[0].to)
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(50, 2))
        #expect(r.network.edges.count == 2)
        expectConsistent(r.network)
        #expect(abs(r.network.edges.reduce(0) { $0 + $1.lengthMeters } - 400) < 0.01)
    }

    // MARK: - Cap

    @Test func beyondTheCapIsAnHonestError() throws {
        let snapper = TrailNetworkSnapper(network: Self.straight400())
        do {
            _ = try snapper.insert(start: Self.pin(200, 2_000))
            Issue.record("expected tooFarFromTrails")
        } catch let TrailNetworkStartError.tooFarFromTrails(d) {
            #expect(abs(d - 2_000) < 15, "reported \(d)")
        }
        // Just inside a mile is allowed.
        let ok = try snapper.insert(start: Self.pin(200, 1_600))
        #expect(abs(ok.start.connectorMeters - 1_600) < 0.5)
        // Just outside is not.
        #expect(throws: TrailNetworkStartError.self) { try snapper.insert(start: Self.pin(200, 1_620)) }
        // A pin on the other side of the planet reports a huge, finite distance.
        do {
            _ = try snapper.snap(lat: -33.0, lon: 70.0)
            Issue.record("expected tooFarFromTrails")
        } catch let TrailNetworkStartError.tooFarFromTrails(d) {
            #expect(d > 1_000_000 && d.isFinite)
        }
    }

    @Test func invalidInputsThrow() throws {
        let snapper = TrailNetworkSnapper(network: Self.straight400())
        #expect(throws: TrailNetworkStartError.invalidCoordinate) { try snapper.snap(lat: .nan, lon: 0) }
        #expect(throws: TrailNetworkStartError.invalidCoordinate) { try snapper.snap(lat: 91, lon: 0) }
        let empty = TrailNetworkSnapper(network: TrailNetwork(trails: []))
        #expect(throws: TrailNetworkStartError.noTrails) { try empty.snap(lat: 33.3, lon: -112) }
    }

    // MARK: - Immutability and determinism

    @Test func baseNetworkIsUnchangedByInsertion() throws {
        let a = Self.trail("A", [Self.line(0, 0, 400, 0)], profile: (0...10).map { $0 * 10 })
        let b = Self.trail("B", [Self.line(200, 0, 200, 200)])
        let base = Self.network([a, b])
        let before = fingerprint(base)
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(100, 150), end: Self.pin(300, -80))
        #expect(r.network.edges.count > base.edges.count)
        #expect(fingerprint(base) == before)
        #expect(fingerprint(snapper.network) == before)
        // Reusing the same snapper for many plans: results are independent.
        let r2 = try snapper.insert(start: Self.pin(100, 150), end: Self.pin(300, -80))
        #expect(fingerprint(r.network) == fingerprint(r2.network))
        _ = try snapper.insert(start: Self.pin(350, 1))
        #expect(fingerprint(base) == before)
    }

    @Test func insertionIsDeterministic() throws {
        let a = Self.trail("A", [Self.line(0, 0, 400, 0)])
        let b = Self.trail("B", [Self.line(200, 0, 200, 200)])
        let one = TrailNetworkSnapper(network: Self.network([a, b]))
        let two = TrailNetworkSnapper(network: Self.network([b, a]))
        let r1 = try one.insert(start: Self.pin(120, 77), end: Self.pin(330, 40))
        let r2 = try two.insert(start: Self.pin(120, 77), end: Self.pin(330, 40))
        #expect(fingerprint(r1.network) == fingerprint(r2.network))
        #expect(r1.start.snap == r2.start.snap)
        #expect(r1.end?.snap == r2.end?.snap)
    }

    // MARK: - End points

    @Test func endPointSnapsLikeAStartPoint() throws {
        let snapper = TrailNetworkSnapper(network: Self.straight400())
        let p = Self.pin(250, 60)
        #expect(try snapper.snapEnd(p) == snapper.snap(p))
    }

    @Test func startAndEndOnTheSameEdgeSplitTogether() throws {
        let base = Self.straight400()
        let snapper = TrailNetworkSnapper(network: base)
        let r = try snapper.insert(start: Self.pin(300, 0), end: Self.pin(100, 50))
        let n = r.network
        let trailEdges = n.edges.filter { $0.kind == .trail }
        #expect(trailEdges.count == 3)
        #expect(abs(trailEdges.reduce(0) { $0 + $1.lengthMeters } - 400) < 1e-6)
        #expect(abs(trailEdges.reduce(0) { $0 + $1.lengthMeters } - base.edges[0].lengthMeters) < 1e-6)
        let end = try #require(r.end)
        #expect(end.isOffTrail && !r.start.isOffTrail)
        #expect(abs(end.connectorMeters - 50) < 0.1)
        #expect(Set([r.start.trailNodeId, end.trailNodeId]).count == 2)
        #expect(n.edgeIds(of: Self.key("A")).count == 3)
        expectConsistent(n)
    }

    @Test func startAndEndAtTheSameSpotShareTheTrailNode() throws {
        let snapper = TrailNetworkSnapper(network: Self.straight400())
        let r = try snapper.insert(start: Self.pin(150, 40), end: Self.pin(150.4, 40))
        let end = try #require(r.end)
        #expect(r.start.trailNodeId == end.trailNodeId)
        #expect(r.network.edges.filter { $0.kind == .trail }.count == 2)   // one cut, not two
        #expect(r.start.nodeId != end.nodeId)
        expectConsistent(r.network)
    }

    // MARK: - Targets and components

    @Test func bridgesAreNeverSnapTargets() throws {
        // A ends at x=100, B starts at x=120: Task 1 bridges the 20 m gap.
        let a = Self.trail("A", [Self.line(0, 0, 100, 0)])
        let b = Self.trail("B", [Self.line(120, 0, 220, 0)])
        let base = Self.network([a, b])
        #expect(base.bridgeEdges.count == 1)
        let snapper = TrailNetworkSnapper(network: base)
        let snap = try snapper.snap(Self.pin(110, 3))
        #expect(base.edges[snap.edgeId].kind == .trail)
        #expect(abs(snap.distanceMeters - hypot(10, 3)) < 0.1)
    }

    @Test func reportsComponentAndDoesNotRetargetToBiggerPiece() throws {
        // Big piece: a cross of two 500 m trails. Small piece: a 60 m stub 120 m away.
        let big1 = Self.trail("big1", [Self.line(0, 0, 500, 0)])
        let big2 = Self.trail("big2", [Self.line(250, -250, 250, 250)])
        let stub = Self.trail("stub", [Self.line(0, 120, 60, 120)])
        let base = Self.network([big1, big2, stub])
        let snapper = TrailNetworkSnapper(network: base)
        #expect(snapper.components.count == 2)
        #expect(snapper.components[0].id == 0 && snapper.components[0].isLargest)
        #expect(snapper.components[0].nodeCount > snapper.components[1].nodeCount)
        #expect(snapper.components[1].isLargest == false)

        // Pin 15 m from the stub, 105 m from the big piece: nearest wins.
        let nearStub = try snapper.snap(Self.pin(30, 135))
        #expect(nearStub.trailKeys == [Self.key("stub")])
        #expect(nearStub.component.id == 1 && nearStub.component.isLargest == false)
        #expect(nearStub.component.nodeCount == snapper.components[1].nodeCount)
        #expect(abs(nearStub.component.lengthMeters - 60) < 0.5)

        let nearBig = try snapper.snap(Self.pin(100, -20))
        #expect(nearBig.component.id == 0 && nearBig.component.isLargest)
    }

    // MARK: - Start point kinds

    @Test func startPointKindsExposeCoordinateAndName() {
        let lot = ParkingLot(lat: 33.1, lon: -112.2, name: "Bursera Trailhead", fee: nil, trailhead: nil, source: nil)
        let unnamed = ParkingLot(lat: 33.1, lon: -112.2, name: nil, fee: nil, trailhead: nil, source: nil)
        #expect(StartPoint.parking(lot).displayName == "Bursera Trailhead")
        #expect(StartPoint.parking(unnamed).displayName == "Parking")
        #expect(StartPoint.trailheadLot(lot).displayName == "Bursera Trailhead")
        #expect(StartPoint.trailheadLot(unnamed).displayName == "Trailhead")
        #expect(StartPoint.trailhead(name: "TH", lat: 1, lon: 2).lon == 2)
        #expect(StartPoint.pin(lat: 3, lon: 4).displayName == "Dropped pin")
        #expect(StartPoint.pin(lat: 3, lon: 4, name: "Camp").displayName == "Camp")
        #expect(StartPoint.pin(lat: 3, lon: 4).coordinate == TrailNetwork.Coordinate(lat: 3, lon: 4))
        #expect(StartPoint.parking(lot).lat == 33.1)
    }

    // MARK: - Real fixture: South Mountain

    private struct GeomFile: Decodable {
        let id: String
        let trails: [Trail]
        let parking: [ParkingLot]?
    }

    private static func loadSouthMountain() throws -> GeomFile {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // SouthMountainExplorerTests
            .deletingLastPathComponent()   // ios
            .deletingLastPathComponent()   // repo root
        let url = repo.appendingPathComponent("public/areas/geom/south-mountain-park-and-preserve-az.json")
        return try JSONDecoder().decode(GeomFile.self, from: Data(contentsOf: url))
    }

    /// Independent reference: brute force over the RAW trail segments.
    private static func rawNearest(_ lat: Double, _ lon: Double, _ geom: GeomFile,
                                   _ proj: TurnCueProjector) -> (meters: Double, trail: String) {
        let p = proj.local(lat: lat, lon: lon)
        var best = (Double.infinity, "")
        for t in geom.trails {
            for seg in t.segments {
                let pts = seg.filter { $0.count >= 2 }.map { proj.local(lat: $0[0], lon: $0[1]) }
                for (a, b) in zip(pts, pts.dropFirst()) {
                    let dx = b.x - a.x, dy = b.y - a.y
                    let l2 = dx * dx + dy * dy
                    let tt = l2 > 0 ? min(1, max(0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2)) : 0
                    let d = hypot(p.x - a.x - tt * dx, p.y - a.y - tt * dy)
                    if d < best.0 { best = (d, t.id) }
                }
            }
        }
        return best
    }

    @Test func southMountainParkingLotsSnapToTheNearestRealTrail() throws {
        let geom = try Self.loadSouthMountain()
        let lots = try #require(geom.parking)
        #expect(lots.count >= 20)
        let net = TrailNetwork(trails: geom.trails.map { .init(areaId: geom.id, trail: $0) })
        let snapper = TrailNetworkSnapper(network: net)
        let before = fingerprint(net)
        #expect(snapper.components.count == 4)   // with bridges (Task 1: 6 without)
        let names = Dictionary(uniqueKeysWithValues: geom.trails.map { ($0.id, $0.name) })
        var rows: [String] = []
        for lot in lots {
            let r = try snapper.insert(start: .parking(lot))
            let ref = Self.rawNearest(lot.lat, lot.lon, geom, net.projector)
            // The network's nodes are snapped up to 6 m, so the network
            // distance may differ slightly from the raw geometry.
            #expect(abs(r.start.snap.distanceMeters - ref.meters) < 8,
                    "\(lot.name ?? "lot") network \(r.start.snap.distanceMeters) vs raw \(ref.meters)")
            // The snapped edge carries the nearest raw trail unless two trails
            // are almost equally close.
            let onEdge = r.start.snap.trailKeys.map(\.trailId)
            if !onEdge.contains(ref.trail) {
                let alt = Self.rawNearest(lot.lat, lot.lon, geom, net.projector)
                print("STARTSNAP note: \(lot.name ?? "lot") raw nearest \(alt.trail) not on snapped edge \(onEdge)")
            }
            #expect(r.start.isOffTrail == (r.start.snap.distanceMeters > 10))
            Self.checkSouthMountainView(r.network)
            let trailName = r.start.snap.trailKeys.first.flatMap { names[$0.trailId] } ?? "?"
            rows.append(String(format: "STARTSNAP | %@ | %.5f,%.5f | %@ | %.1f m | %@ | comp %d (%d nodes, %.1f km)",
                               lot.name ?? "(unnamed)", lot.lat, lot.lon, trailName,
                               r.start.connectorMeters, r.start.isOffTrail ? "connector" : "on trail",
                               r.start.snap.component.id, r.start.snap.component.nodeCount,
                               r.start.snap.component.lengthMeters / 1000))
        }
        rows.forEach { print($0) }
        #expect(fingerprint(net) == before)   // 29 insertions left the base alone

        let bursera = try #require(lots.first { $0.name == "Bursera Trailhead" })
        let b = try snapper.snap(.parking(bursera))
        #expect(b.trailKeys.contains { $0.trailId == "bursera-trail" })
        #expect(b.distanceMeters > 20 && b.distanceMeters < 35)
    }

    private static func checkSouthMountainView(_ n: TrailNetwork) {
        // Every edge id matches its slot and every node lists real incident edges.
        for (i, e) in n.edges.enumerated() {
            precondition(e.id == i)
            precondition(n.nodes[e.from].edgeIds.contains(i) && n.nodes[e.to].edgeIds.contains(i))
        }
    }

    @Test func southMountainPinsFarFromEverythingAreRefusedAndNearOnesAreNot() throws {
        let geom = try Self.loadSouthMountain()
        let net = TrailNetwork(trails: geom.trails.map { .init(areaId: geom.id, trail: $0) })
        let snapper = TrailNetworkSnapper(network: net)
        // Downtown Phoenix is ~10 km from the preserve's trails.
        #expect(throws: TrailNetworkStartError.self) { try snapper.snap(lat: 33.4484, lon: -112.0740) }
        // A dropped pin in the preserve interior snaps.
        let s = try snapper.snap(lat: 33.3400, lon: -112.0800)
        #expect(s.distanceMeters < 1_609.344)
    }
}
