import Foundation
import Testing
@testable import SouthMountainExplorer

/// Drawing a planned route and the Start hike rules ("Plan a Hike", Task 10).
struct PlannedRouteTests {
    typealias K = PlannerKit

    private func route(_ segs: [(PlannedRoute.Kind, Double)]) -> PlannedRoute {
        let segments = segs.enumerated().map { i, s in
            PlannedRoute.Segment(
                kind: s.0,
                coords: [.init(lat: 33 + Double(i), lon: -112), .init(lat: 33.001 + Double(i), lon: -112)],
                lengthMeters: s.1)
        }
        return PlannedRoute(segments: segments, tags: [], start: segments.first?.coords.first, end: nil)
    }

    /// Hand-made plan over real edges: kinds come from the edges.
    private func plan(_ view: TrailNetworkWithEndpoints, _ steps: [RouteStep], shape: RoutePlanRequest.Shape = .loop) throws -> RoutePlan {
        let m = try #require(RouteMetrics(network: view.network, steps: []))
        return RoutePlan(
            steps: steps,
            stepInfo: steps.map {
                .init(trailKey: nil, kind: view.network.edges[$0.edgeId].kind, isGoalCover: false)
            },
            finished: [], metrics: m, shape: shape, startNodeId: 0, endNodeId: 0, evaluations: 1)
    }

    private func near(_ a: PlannedRoute.Coord, _ lat: Double, _ lon: Double) -> Bool {
        Area.meters(a.lat, a.lon, lat, lon) < 0.5
    }

    // MARK: - Walk order and direction

    @Test func directionIsRespected() throws {
        let v = try K.view([K.trail("A", [(0, 0), (200, 0)])], start: (0, 0))
        let edge = try #require(v.network.edges.first { $0.kind == .trail })
        let first = edge.coordinates.first!, last = edge.coordinates.last!
        let fwd = PlannedRouteBuilder.build(plan: try plan(v, [.init(edgeId: edge.id, direction: .forward)]), edges: v.network.edges)
        let back = PlannedRouteBuilder.build(plan: try plan(v, [.init(edgeId: edge.id, direction: .backward)]), edges: v.network.edges)
        #expect(near(try #require(fwd.start), first.lat, first.lon))
        #expect(near(try #require(fwd.segments.last?.coords.last), last.lat, last.lon))
        #expect(near(try #require(back.start), last.lat, last.lon))
        #expect(near(try #require(back.segments.last?.coords.last), first.lat, first.lon))
    }

    @Test func consecutiveTrailStepsMergeWithoutDuplicateJoins() throws {
        // Two trails cross, so each is cut into two edges.
        let o = try K.run(
            [K.trail("A", [(0, 0), (400, 0)]), K.trail("B", [(200, -200), (200, 200)])],
            start: (0, 0), limit: 7200)
        let p = try #require(o.plan)
        let r = PlannedRouteBuilder.build(plan: p, edges: o.net.edges)
        #expect(p.steps.count > 1)
        #expect(r.segments.count == 1 && r.segments[0].kind == .trail)
        let c = r.segments[0].coords
        for (a, b) in zip(c, c.dropFirst()) { #expect(a != b) }
        // Walk order: the drawn line is continuous (no jumps over 25 m).
        for (a, b) in zip(c, c.dropFirst()) { #expect(Area.meters(a.lat, a.lon, b.lat, b.lon) < 25) }
    }

    // MARK: - Connectors, bridges, tags

    @Test func connectorIsSplitOutAndTaggedOnce() throws {
        // Start 40 m off the trail: a connector out and the same one back.
        let o = try K.run([K.trail("A", [(0, 0), (400, 0)])], start: (100, -40), limit: 7200)
        let p = try #require(o.plan)
        let r = PlannedRouteBuilder.build(plan: p, edges: o.net.edges)
        let kinds = r.segments.map(\.kind)
        #expect(kinds.first == .connector && kinds.last == .connector)
        #expect(kinds.contains(.trail))
        let offTrail = r.tags.filter { $0.kind == .connector }
        #expect(offTrail.count == 1)
        #expect(offTrail.first?.text == "Off-trail")
        // The tag sits mid-connector: about 20 m from both ends.
        let seg = try #require(r.segments.first)
        let tag = try #require(offTrail.first).coord
        let d0 = Area.meters(tag.lat, tag.lon, seg.coords.first!.lat, seg.coords.first!.lon)
        let d1 = Area.meters(tag.lat, tag.lon, seg.coords.last!.lat, seg.coords.last!.lon)
        #expect(abs(d0 - d1) < 1)
        #expect(r.start == seg.coords.first)
    }

    @Test func bridgeIsTaggedUnverified() throws {
        // 20 m gap between two trails.
        let o = try K.run(
            [K.trail("A", [(0, 0), (300, 0)]), K.trail("B", [(320, 0), (600, 0)])],
            start: (0, 0), end: (600, 0), limit: 7200, shape: .oneWay)
        let p = try #require(o.plan)
        let r = PlannedRouteBuilder.build(plan: p, edges: o.net.edges)
        let bridges = r.segments.filter { $0.kind == .bridge }
        #expect(bridges.count == 1)
        let tags = r.tags.filter { $0.kind == .bridge }
        #expect(tags.map(\.text) == ["Unverified connection"])
        #expect(bridges[0].lengthMeters >= 10)
    }

    @Test func shortConnectionsGetNoTag() {
        let short = PlannedRoute.Segment(
            kind: .bridge, coords: [.init(lat: 33, lon: -112), .init(lat: 33.00005, lon: -112)], lengthMeters: 5.5)
        let exactly = PlannedRoute.Segment(
            kind: .connector, coords: [.init(lat: 33, lon: -112), .init(lat: 33.0001, lon: -112)], lengthMeters: 10)
        #expect(PlannedRouteBuilder.tags(for: [short]).isEmpty)
        #expect(PlannedRouteBuilder.tags(for: [exactly]).count == 1)
        let trail = PlannedRoute.Segment(
            kind: .trail, coords: [.init(lat: 33, lon: -112), .init(lat: 33.01, lon: -112)], lengthMeters: 1100)
        #expect(PlannedRouteBuilder.tags(for: [trail]).isEmpty)
    }

    // MARK: - Markers

    @Test func loopHasStartOnlyAndOneWayHasBoth() throws {
        let loop = try K.run([K.trail("A", [(0, 0), (300, 0)])], start: (0, 0), limit: 7200)
        let rl = PlannedRouteBuilder.build(plan: try #require(loop.plan), edges: loop.net.edges)
        #expect(rl.start != nil && rl.end == nil)

        let one = try K.run([K.trail("A", [(0, 0), (300, 0)])], start: (0, 0), end: (300, 0), limit: 7200, shape: .oneWay)
        let ro = PlannedRouteBuilder.build(plan: try #require(one.plan), edges: one.net.edges)
        #expect(ro.start != nil)
        #expect(ro.end == ro.segments.last?.coords.last)
        #expect(ro.start != ro.end)
    }

    @Test func emptyPlanDrawsNothing() throws {
        let v = try K.view([K.trail("A", [(0, 0), (200, 0)])], start: (0, 0))
        let r = PlannedRouteBuilder.build(plan: try plan(v, []), edges: v.network.edges)
        #expect(r.isEmpty && r.start == nil && r.end == nil && r.allCoords.isEmpty)
    }

    // MARK: - Alternatives and clear

    @Test func alternativesSwitchTheDrawnRoute() {
        let best = route([(.trail, 500)])
        let short = route([(.trail, 300)])
        let opts = PlanPreview.options(best: best, alternatives: [("Shortest", short), ("Least climb", route([(.trail, 400)])), ("Empty", .empty)])
        #expect(opts.map(\.id) == ["best", "shortest", "least-climb"])
        var p = PlanPreview()
        p.load(opts)
        #expect(!p.isShowing && p.canShow && p.selected == nil)
        p.show()
        #expect(p.selected?.route == best)
        let switched = p.select(id: "shortest")
        #expect(switched)
        #expect(p.selected?.route == short)
        let unknown = p.select(id: "nope")
        #expect(!unknown)
        #expect(p.selected?.id == "shortest")
        p.clear()
        #expect(!p.isShowing && p.selected == nil)
        // A cleared preview is not selectable until shown again.
        let hidden = p.select(id: "best")
        #expect(!hidden)
        p.show()
        #expect(p.selected?.id == "best")
    }

    @Test func showWithoutRoutesDoesNothing() {
        var p = PlanPreview()
        p.load(PlanPreview.options(best: .empty, alternatives: []))
        p.show()
        #expect(!p.isShowing && !p.canShow)
    }

    @Test func loadingANewResultHidesTheOldRoute() {
        var p = PlanPreview()
        p.load(PlanPreview.options(best: route([(.trail, 1)]), alternatives: []))
        p.show()
        p.load(PlanPreview.options(best: route([(.trail, 2)]), alternatives: []))
        #expect(!p.isShowing)
    }

    @MainActor
    @Test func controllerClearAndFitTicks() {
        let c = PlanHikeController()
        c.showOnMap()
        #expect(c.fitTick == 0 && !c.preview.isShowing)
        c.markHikeStarted()
        #expect(c.hikeStarted)
        c.clearRoute()
        #expect(!c.hikeStarted && !c.preview.isShowing)
    }

    @MainActor
    @Test func showOnMapRefitsOnceAfterSettling() async throws {
        let c = PlanHikeController()
        c.settleDelay = .milliseconds(20)
        c.loadPreviewForTesting(PlanPreview.options(best: route([(.trail, 2)]), alternatives: []))
        c.showOnMap()
        #expect(c.preview.isShowing && c.fitTick == 1)
        try await Task.sleep(for: .milliseconds(300))
        #expect(c.fitTick == 2)
        // Clearing before the delay ends cancels the second fit.
        c.showOnMap()
        #expect(c.fitTick == 3)
        c.clearRoute()
        try await Task.sleep(for: .milliseconds(300))
        #expect(c.fitTick == 3)
    }

    // MARK: - Off-main planning inputs
    @Test func completionSnapshotMatchesIdAndFingerprintRules() {
        let a = K.trail("A", [(0, 0), (200, 0)])
        let b = K.trail("B", [(0, 300), (200, 300)])
        let c = K.trail("C", [(0, 600), (200, 600)])
        let inputs = [a, b, c].map { TrailNetwork.Input(areaId: "x", trail: $0) }
        let byId = TrailCompletionLookup.snapshot(
            completions: ["x": [a.id: "2026-01-01"]], fingerprints: [], trails: inputs)
        #expect(byId.isComplete(inputs[0].key) && !byId.isComplete(inputs[1].key))
        let byFp = TrailCompletionLookup.snapshot(
            completions: [:], fingerprints: [b.completionFingerprint], trails: inputs)
        #expect(byFp.isComplete(inputs[1].key) && !byFp.isComplete(inputs[0].key) && !byFp.isComplete(inputs[2].key))
    }

    @Test func areasReadOffMainActorFromDisk() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("PlanLoaderTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let area = Area(
            id: "nb", name: "Nb", subtitle: "AZ", centerLat: 33.3, centerLon: -112, zoom: 13,
            bbox: [-112.1, 33.2, -111.9, 33.4],
            trails: [Trail(id: "t", name: "T", distanceMi: 1, difficulty: .easy,
                           segments: [[[33.3, -112], [33.31, -112.01]]])],
            trailCount: 1, totalMi: 1, cachedAt: nil)
        _ = AreaCacheStore(cacheDirectory: dir).store(area)
        let got = await Task.detached {
            PlanningNetworkLoader.readAreas(ids: ["nb", "missing", "nb"], directory: dir)
        }.value
        #expect(got.keys.sorted() == ["nb"])
        #expect(got["nb"]?.bbox == [-112.1, 33.2, -111.9, 33.4])
        #expect(PlanningNetworkLoader.readAreas(ids: [], directory: dir).isEmpty)
    }

    // MARK: - Start gating

    @Test func startIsGatedByRecordingAndRecovery() {
        #expect(PlanStartGate.evaluate(hasActiveRecording: false, hasRecoveryIssue: false) == .allowed)
        let rec = PlanStartGate.evaluate(hasActiveRecording: true, hasRecoveryIssue: false)
        let issue = PlanStartGate.evaluate(hasActiveRecording: false, hasRecoveryIssue: true)
        let both = PlanStartGate.evaluate(hasActiveRecording: true, hasRecoveryIssue: true)
        #expect(!rec.isAllowed && !issue.isAllowed && !both.isAllowed)
        #expect(rec != issue)
        #expect(both == issue)
        #expect(PlanStartGate.guidanceNote.hasPrefix("Route guidance (turn cues, Remaining)"))
    }
}
