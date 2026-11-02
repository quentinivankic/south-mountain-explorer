import Foundation
import Testing
@testable import SouthMountainExplorer

/// Trail eligibility for "Plan a Hike" (Task 4). Synthetic scenes are laid out
/// in local metres (x east, y north) on flat ground, so 1 km is about 720 s.
struct TrailEligibilityTests {
    typealias Key = TrailNetwork.TrailKey
    typealias Reason = TrailEligibility.Reason

    // MARK: - Helpers
    private static let lat0 = 33.34
    private static let lon0 = -112.0
    private static func ll(_ x: Double, _ y: Double) -> [Double] {
        let mpd = TurnCueProjector.metersPerDegree
        return [lat0 + y / mpd, lon0 + x / (cos(lat0 * .pi / 180) * mpd)]
    }
    private static func line(_ pts: [(Double, Double)], step: Double = 20) -> [[Double]] {
        var out: [[Double]] = []
        for (a, b) in zip(pts, pts.dropFirst()) {
            let n = max(1, Int((hypot(b.0 - a.0, b.1 - a.1) / step).rounded()))
            for i in 0..<n {
                let t = Double(i) / Double(n)
                out.append(ll(a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t))
            }
        }
        out.append(ll(pts.last!.0, pts.last!.1))
        return out
    }
    private static func trail(_ id: String, _ pieces: [[(Double, Double)]], distanceMi: Double = 0) -> Trail {
        Trail(id: id, name: "Trail \(id)", distanceMi: distanceMi, difficulty: .easy,
              segments: pieces.map { line($0) }, gainFt: nil, profileFt: nil)
    }
    private static func inputs(_ trails: [Trail]) -> [TrailNetwork.Input] {
        trails.map { .init(areaId: "a", trail: $0) }
    }
    private static func key(_ id: String) -> Key { Key(areaId: "a", trailId: id) }
    private static func pin(_ x: Double, _ y: Double) -> StartPoint {
        let c = ll(x, y)
        return .pin(lat: c[0], lon: c[1])
    }

    /// The scene most tests share. Start is at (0,0), the west end of A.
    ///   A: (0,0)-(500,0)        B: (500,0)-(500,500)     C: (0,0)-(0,-300)
    ///   K: (0,-300)-(400,-300)  Q: two pieces off K, ~200 m apart, joined only through K
    ///   S: two pieces 600 m apart (split far)   P: two pieces 100 m apart, no path
    ///   Z: far island    L: (500,500)-(500,5500), 5 km beyond B
    private static func scene() -> [Trail] {
        [
            trail("A", [[(0, 0), (500, 0)]], distanceMi: 99),
            trail("B", [[(500, 0), (500, 500)]]),
            trail("C", [[(0, 0), (0, -300)]]),
            trail("K", [[(0, -300), (400, -300)]]),
            trail("Q", [[(100, -300), (100, -350)], [(300, -300), (300, -350)]]),
            trail("S", [[(1000, 1000), (1100, 1000)], [(1700, 1000), (1800, 1000)]]),
            trail("P", [[(2000, 0), (2100, 0)], [(2200, 0), (2300, 0)]]),
            trail("Z", [[(3000, 3000), (3500, 3000)]]),
            trail("L", [[(500, 500), (500, 5500)]]),
        ]
    }

    private static func view(_ trails: [Trail], start: (Double, Double) = (0, 0),
                             end: (Double, Double)? = nil) throws -> TrailNetworkWithEndpoints {
        let net = TrailNetwork(trails: inputs(trails))
        return try TrailNetworkSnapper(network: net).insert(
            start: pin(start.0, start.1), end: end.map { pin($0.0, $0.1) })
    }

    private static func run(
        _ trails: [Trail], completed: Set<String> = [], budget: Double = 3600,
        trip: TrailEligibility.Trip = .loop, pace: Double = 1.0,
        end: (Double, Double)? = nil
    ) throws -> TrailEligibility {
        TrailEligibility.evaluate(
            network: try view(trails, end: end), trails: inputs(trails),
            completion: TrailCompletionLookup(completed: Set(completed.map(key))),
            options: .init(budgetSeconds: budget, paceFactor: pace, trip: trip))
    }

    private func reason(_ r: TrailEligibility, _ id: String) -> Reason? {
        r.excluded.first { $0.key == Self.key(id) }?.reason
    }
    private func candidate(_ r: TrailEligibility, _ id: String) -> TrailEligibility.Candidate? {
        r.candidates.first { $0.key == Self.key(id) }
    }

    // MARK: - Exclusions

    @Test func completedTrailIsExcluded() throws {
        let r = try Self.run(Self.scene(), completed: ["B"])
        #expect(reason(r, "B") == .completed)
        #expect(candidate(r, "B") == nil)
    }

    @Test func uncompletedTrailIsIncludedWithNetworkLength() throws {
        let r = try Self.run(Self.scene())
        let a = try #require(candidate(r, "A"))
        #expect(abs(a.requiredMeters - 500) < 2)
        #expect(a.isReachable)
        #expect(a.name == "Trail A")
        #expect(a.exitNodeId != nil)
        // Network length, not the (wrong) distanceMi of 99 mi.
        #expect(a.requiredMeters < 1_000)
        #expect(!a.edgeIds.isEmpty)
        #expect(a.edgeIds == a.edgeIds.sorted())
    }

    @Test func splitFarTrailIsExcludedWithItsGap() throws {
        let r = try Self.run(Self.scene())
        guard case .splitTooFar(let gap)? = reason(r, "S") else {
            Issue.record("expected splitTooFar, got \(String(describing: reason(r, "S")))"); return
        }
        #expect(gap > 590 && gap < 610)
        #expect(Reason.splitTooFar(maxGapMeters: gap).label == "can't be finished in one route")
    }

    @Test func splitGapBoundaryIsQuarterMile() throws {
        // 402 m apart is allowed through the split rule (then fails on
        // connectivity); 403 m is split-too-far.
        func gapReason(_ gap: Double) throws -> Reason? {
            let t = Self.trail("G", [[(0, 3000), (100, 3000)], [(100 + gap, 3000), (200 + gap, 3000)]])
            return try Self.run([Self.trail("A", [[(0, 0), (500, 0)]]), t]).excluded
                .first { $0.key == Self.key("G") }?.reason
        }
        if case .piecesNotConnected? = try gapReason(402) {} else { Issue.record("402 m should not be splitTooFar") }
        if case .splitTooFar? = try gapReason(404) {} else { Issue.record("404 m should be splitTooFar") }
    }

    @Test func piecesNotConnectedIsExcludedWithGap() throws {
        let r = try Self.run(Self.scene())
        guard case .piecesNotConnected(let gap)? = reason(r, "P") else {
            Issue.record("expected piecesNotConnected, got \(String(describing: reason(r, "P")))"); return
        }
        #expect(gap > 95 && gap < 105)
    }

    @Test func piecesJoinedThroughOtherTrailsAreAllowed() throws {
        let trails = Self.scene()
        let net = TrailNetwork(trails: Self.inputs(trails))
        let gap = try #require(net.maxPieceGapMeters(of: Self.key("Q")))
        #expect(gap > TrailNetwork.Options().bridgeMeters)   // not bridged...
        let r = try Self.run(trails, budget: 7200)
        #expect(candidate(r, "Q") != nil)                     // ...but connected via K
    }

    @Test func otherComponentIsDisconnected() throws {
        let r = try Self.run(Self.scene())
        #expect(reason(r, "Z") == .disconnected)
    }

    @Test func farTrailIsOutOfReachWithEstimateOverBudget() throws {
        let r = try Self.run(Self.scene(), budget: 3600)
        guard case .outOfReach(let secs)? = reason(r, "L") else {
            Issue.record("expected outOfReach, got \(String(describing: reason(r, "L")))"); return
        }
        #expect(secs > 3600)
        // Within a long enough budget it is a candidate.
        let long = try Self.run(Self.scene(), budget: 6 * 3600)
        #expect(candidate(long, "L") != nil)
    }

    @Test func exactEstimateBeyondTheBudgetIsOutOfReachToo() throws {
        // B is near (passes the prefilter) but entry + cover + exit is over.
        let tight = try Self.run(Self.scene(), budget: 700)
        if case .outOfReach(let s)? = reason(tight, "B") { #expect(s > 700) }
        else { Issue.record("expected outOfReach for B, got \(String(describing: reason(tight, "B")))") }
        #expect(candidate(tight, "A") != nil)   // 2 x 360 s + 0 entry fits
    }

    @Test func trailWithNoEdgesIsExcluded() throws {
        var trails = Self.scene()
        let ghost = Self.trail("ghost", [[(0, 0), (1, 0)]])
        let net = TrailNetwork(trails: Self.inputs(trails))
        trails.append(ghost)   // named, but not in the network
        let v = try TrailNetworkSnapper(network: net).insert(start: Self.pin(0, 0))
        let r = TrailEligibility.evaluate(
            network: v, trails: Self.inputs(trails), completion: .none, options: .init(budgetSeconds: 3600))
        #expect(reason(r, "ghost") == .noEdges)
    }

    @Test func everyTrailIsClassifiedExactlyOnce() throws {
        let r = try Self.run(Self.scene(), completed: ["C"])
        let all = r.candidates.map(\.key) + r.excluded.map(\.key)
        #expect(all.count == Self.scene().count)
        #expect(Set(all).count == all.count)
    }

    // MARK: - Completed trails and shared edges

    @Test func completedTrailsAreWalkedThroughAsConnectors() throws {
        // B starts at the far end of A. With A done, B is still reachable, and
        // its entry walk still crosses A.
        let open = try Self.run(Self.scene())
        let withA = try Self.run(Self.scene(), completed: ["A"])
        #expect(reason(withA, "A") == .completed)
        let before = try #require(candidate(open, "B"))
        let after = try #require(candidate(withA, "B"))
        #expect(abs(before.estimatedSeconds - after.estimatedSeconds) < 1e-6)
        // 500 m entry + 500 m cover + 500 m back at Tobler flat is about 1,080 s.
        #expect(after.estimatedSeconds > 1_000 && after.estimatedSeconds < 1_200)
    }

    @Test func sharedEdgeBetweenCompletedAndUncompletedTrail() throws {
        // A2 runs along all of A and then 200 m north. A is complete.
        let trails = [
            Self.trail("A", [[(0, 0), (500, 0)]]),
            Self.trail("A2", [[(0, 0), (500, 0), (500, 200)]]),
        ]
        let net = TrailNetwork(trails: Self.inputs(trails))
        #expect(!net.sharedEdges.isEmpty)
        let r = try Self.run(trails, completed: ["A"])
        #expect(reason(r, "A") == .completed)
        let a2 = try #require(candidate(r, "A2"))
        // The whole of A2, shared stretch included, is required.
        #expect(abs(a2.requiredMeters - 700) < 2)
        let shared = Set(net.sharedEdges.map(\.id))
        #expect(!shared.isDisjoint(with: Set(a2.edgeIds)))
    }

    // MARK: - Trip shape, pace, determinism

    @Test func oneWayWithEndIsCheaperThanLoop() throws {
        // A one-way hike ending at B's far end skips the walk home.
        let loop = try Self.run(Self.scene(), trip: .loop)
        let oneWay = try Self.run(Self.scene(), trip: .oneWay, end: (500, 500))
        let l = try #require(candidate(loop, "B")).estimatedSeconds
        let o = try #require(candidate(oneWay, "B")).estimatedSeconds
        #expect(o < l)
        // 500 m to B + 500 m of B, ending at its north end: about 720 s.
        #expect(o > 650 && o < 800)
    }

    @Test func oneWayReachRadiusIsTheWholeBudget() throws {
        // L begins 1,000 m (720 s) away: beyond a 1,000 s loop radius, inside
        // a one-way radius, but the 5 km cover keeps it out either way.
        let r = try Self.run(Self.scene(), budget: 1_000, trip: .oneWay, end: (500, 500))
        if case .outOfReach? = reason(r, "L") {} else { Issue.record("L should be out of reach") }
        let b = try Self.run(Self.scene(), budget: 1_000, trip: .loop)
        if case .outOfReach? = reason(b, "B") {} else { Issue.record("B loop should be out of reach") }
        #expect(candidate(try Self.run(Self.scene(), budget: 1_000, trip: .oneWay, end: (500, 500)), "B") != nil)
    }

    @Test func slowerPaceRaisesTheEstimate() throws {
        let normal = try #require(candidate(try Self.run(Self.scene()), "A")).estimatedSeconds
        let slow = try #require(candidate(try Self.run(Self.scene(), pace: 0.5), "A")).estimatedSeconds
        #expect(abs(slow / normal - 2) < 0.05)
    }

    @Test func resultsAreSortedAndDeterministic() throws {
        let r1 = try Self.run(Self.scene())
        let r2 = try Self.run(Self.scene())
        let r3 = try Self.run(Self.scene().reversed())
        #expect(r1.candidates == r2.candidates)
        #expect(r1.excluded == r2.excluded)
        #expect(r1.candidates.map(\.key) == r3.candidates.map(\.key))
        #expect(r1.excluded.map(\.key) == r3.excluded.map(\.key))
        let secs = r1.candidates.map(\.estimatedSeconds)
        #expect(secs == secs.sorted())
        #expect(r1.excluded.map(\.key) == r1.excluded.map(\.key).sorted())
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

    private static func tag(_ r: Reason) -> String {
        switch r {
        case .completed: return "completed"
        case .noEdges: return "noEdges"
        case .splitTooFar: return "splitTooFar"
        case .piecesNotConnected: return "piecesNotConnected"
        case .disconnected: return "disconnected"
        case .outOfReach: return "outOfReach"
        }
    }

    private static func report(_ title: String, _ r: TrailEligibility) {
        var counts: [String: Int] = [:]
        for e in r.excluded { counts[tag(e.reason), default: 0] += 1 }
        let summary = counts.keys.sorted().map { "\($0)=\(counts[$0]!)" }.joined(separator: " ")
        print("T4TABLE \(title): candidates=\(r.candidates.count) excluded=\(r.excluded.count) [\(summary)]")
        for c in r.candidates.prefix(10) {
            print("T4TABLE   \(c.name): \(Int(c.estimatedSeconds / 60)) min, \(Int(c.requiredMeters)) m")
        }
    }

    @Test func southMountainFromBursera() throws {
        let geom = try Self.loadSouthMountain()
        let lots = try #require(geom.parking)
        let bursera = try #require(lots.first { $0.name == "Bursera Trailhead" })
        let inputs = geom.trails.map { TrailNetwork.Input(areaId: geom.id, trail: $0) }
        let net = TrailNetwork(trails: inputs)
        let view = try TrailNetworkSnapper(network: net).insert(start: .parking(bursera))

        var cheapest: [Key] = []
        for hours in [1.0, 2.0, 4.0] {
            let r = TrailEligibility.evaluate(
                network: view, trails: inputs, completion: .none,
                options: .init(budgetSeconds: hours * 3600))
            Self.report("none done, \(Int(hours)) h", r)
            #expect(r.candidates.count + r.excluded.count == geom.trails.count)
            for c in r.candidates {
                #expect(c.estimatedSeconds <= hours * 3600)
                #expect(c.requiredMeters > 0)
            }
            if hours == 4 { cheapest = r.candidates.prefix(10).map(\.key) }
            // Structural exclusions do not depend on the budget.
            for id in ["guadalupe-perimeter", "bajada-trail", "las-lomitas-trail", "alta-trail"] {
                let e = try #require(r.excluded.first(where: { $0.key.trailId == id }))
                if case .splitTooFar = e.reason {} else { Issue.record("\(id): \(e.reason)") }
            }
            let nat = try #require(r.excluded.first(where: { $0.key.trailId == "national-trail" }))
            if case .piecesNotConnected(let gap) = nat.reason { #expect(gap > 150 && gap < 200) }
            else { Issue.record("national-trail: \(nat.reason)") }
        }
        // Walk-through-completed: mark the 10 cheapest done.
        #expect(cheapest.count == 10)
        let done = TrailCompletionLookup(completed: Set(cheapest))
        for hours in [1.0, 2.0, 4.0] {
            let r = TrailEligibility.evaluate(
                network: view, trails: inputs, completion: done,
                options: .init(budgetSeconds: hours * 3600))
            Self.report("10 nearest done, \(Int(hours)) h", r)
            #expect(r.excluded.filter { $0.reason == .completed }.count == 10)
        }
    }
}
