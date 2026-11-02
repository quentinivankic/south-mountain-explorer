import Foundation
import Testing
@testable import SouthMountainExplorer

/// Merging trails from several areas into one network input ("Plan a Hike", Task 7).
struct MultiAreaTrailsTests {
    typealias Key = TrailNetwork.TrailKey
    private static func t(_ id: String, _ pts: [(Double, Double)]) -> Trail { PlannerKit.trail(id, pts) }
    private static func set(_ id: String, _ trails: [Trail]) -> AreaTrailSet { AreaTrailSet(areaId: id, trails: trails) }
    private static func keys(_ m: MergedTrails) -> [Key] { m.inputs.map(\.key) }

    @Test func sharedTrailAcrossAreasBecomesOneAndEitherCopyCounts() {
        let shared = Self.t("loop", [(0, 0), (500, 0), (500, 500)])
        let a = Self.set("a", [shared, Self.t("only-a", [(0, 0), (0, 300)])])
        let b = Self.set("b", [Trail(id: "other-id", name: "Same path", distanceMi: 0, difficulty: .easy,
                                      segments: shared.segments), Self.t("only-b", [(900, 0), (1200, 0)])])
        let m = MultiAreaTrails.merge(areas: [a, b])
        #expect(Self.keys(m) == [Key(areaId: "a", trailId: "loop"), Key(areaId: "a", trailId: "only-a"),
                                 Key(areaId: "b", trailId: "only-b")])
        #expect(m.allInputs.count == 4)
        let kept = Key(areaId: "a", trailId: "loop")
        #expect(m.copies[kept] == [Key(areaId: "b", trailId: "other-id")])
        // Complete only in the dropped copy's own area: counts.
        let viaCopy = m.completion(TrailCompletionLookup(completed: [Key(areaId: "b", trailId: "other-id")]))
        #expect(viaCopy.isComplete(kept))
        // Complete only in the kept copy's area: counts.
        let viaKept = m.completion(TrailCompletionLookup(completed: [kept]))
        #expect(viaKept.isComplete(kept))
        // Unrelated trails are not credited.
        #expect(!viaCopy.isComplete(Key(areaId: "a", trailId: "only-a")))
        #expect(!viaCopy.isComplete(Key(areaId: "b", trailId: "only-b")))
    }

    @Test func primaryAreasCopyIsTheOneKept() {
        let shared = Self.t("x", [(0, 0), (400, 0)])
        let m = MultiAreaTrails.merge(areas: [Self.set("z-primary", [shared]), Self.set("a-other", [shared])])
        #expect(Self.keys(m) == [Key(areaId: "z-primary", trailId: "x")])
    }

    @Test func aliasTwinIsNeverMergedWithItsCanonical() {
        let route = Self.t("r", [(0, 0), (400, 0)])
        let canon = Self.set("canon", [route, Self.t("extra", [(0, 0), (0, 400)])])
        let twin = Self.set("twin", [route])
        let m = MultiAreaTrails.merge(areas: [twin, canon], canonical: { $0 == "twin" ? "canon" : $0 })
        #expect(Set(Self.keys(m).map(\.areaId)) == ["canon"])
        #expect(m.droppedAliasAreas == ["twin"])
        // Completion recorded under the twin still counts for the canonical copy.
        let c = m.completion(TrailCompletionLookup(completed: [Key(areaId: "twin", trailId: "r")]))
        #expect(c.isComplete(Key(areaId: "canon", trailId: "r")))
        #expect(!c.isComplete(Key(areaId: "canon", trailId: "extra")))
    }

    @Test func aliasTwinStaysWhenItsCanonicalIsAbsent() {
        let m = MultiAreaTrails.merge(areas: [Self.set("twin", [Self.t("r", [(0, 0), (400, 0)])])],
                                      canonical: { $0 == "twin" ? "canon" : $0 })
        #expect(Self.keys(m) == [Key(areaId: "twin", trailId: "r")])
        #expect(m.droppedAliasAreas.isEmpty)
    }

    @Test func sameIdDifferentGeometryStaysDistinct() {
        let a = Self.set("a", [Self.t("ice-age-trail", [(0, 0), (400, 0)])])
        let b = Self.set("b", [Self.t("ice-age-trail", [(2000, 0), (2400, 0)])])
        let m = MultiAreaTrails.merge(areas: [a, b])
        #expect(Self.keys(m) == [Key(areaId: "a", trailId: "ice-age-trail"), Key(areaId: "b", trailId: "ice-age-trail")])
        #expect(m.copies.isEmpty)
        let c = m.completion(TrailCompletionLookup(completed: [Key(areaId: "a", trailId: "ice-age-trail")]))
        #expect(c.isComplete(Key(areaId: "a", trailId: "ice-age-trail")))
        #expect(!c.isComplete(Key(areaId: "b", trailId: "ice-age-trail")))
        // The network keeps both.
        #expect(TrailNetwork(trails: m.inputs).trailKeys.count == 2)
    }

    @Test func twoTrailsInOneAreaWithSameGeometryAreNotDeduped() {
        let g = Self.t("p", [(0, 0), (400, 0)])
        let q = Trail(id: "q", name: "q", distanceMi: 0, difficulty: .easy, segments: g.segments)
        let m = MultiAreaTrails.merge(areas: [Self.set("a", [g, q])])
        #expect(m.inputs.count == 2)
    }

    @Test func duplicateAreaIdAndEmptyGeometryAreHandled() {
        let empty = Trail(id: "e", name: "e", distanceMi: 0, difficulty: .easy, segments: [])
        let a = Self.set("a", [empty, Self.t("p", [(0, 0), (400, 0)])])
        let m = MultiAreaTrails.merge(areas: [a, a, Self.set("b", [empty])])
        #expect(m.inputs.count == 3)   // a/e, a/p, b/e (empty never deduped)
    }

    @Test func deterministicAndOrderOfNonPrimaryIsPreserved() {
        let a = Self.set("a", [Self.t("1", [(0, 0), (400, 0)])])
        let b = Self.set("b", [Self.t("2", [(500, 0), (900, 0)])])
        let c = Self.set("c", [Self.t("3", [(1000, 0), (1400, 0)])])
        let one = MultiAreaTrails.merge(areas: [a, b, c])
        let two = MultiAreaTrails.merge(areas: [a, b, c])
        #expect(Self.keys(one) == Self.keys(two))
        #expect(Self.keys(one).map(\.areaId) == ["a", "b", "c"])
    }

    // MARK: - Cross-area planning on a synthetic two-area scene

    @Test func routeCrossesIntoTheNeighborOnlyWhenItIsIncluded() throws {
        // Area a: 1 km east of the start. Area b: the next 1 km, touching a's end.
        let a = Self.set("a", [Self.t("near", [(0, 0), (1000, 0)])])
        let b = Self.set("b", [Self.t("far", [(1000, 0), (2000, 0)])])
        func plan(_ areas: [AreaTrailSet]) throws -> RoutePlan {
            let m = MultiAreaTrails.merge(areas: areas)
            let net = TrailNetwork(trails: m.inputs)
            let view = try TrailNetworkSnapper(network: net).insert(start: PlannerKit.pin(0, 0))
            let req = PlanOutcomeRequest(network: view, trails: m.inputs, completion: m.completion(.none),
                                         timeLimitSeconds: 3600)
            guard case .planned(let best, _) = RoutePlanningService.planOutcome(req) else {
                Issue.record("expected a plan"); throw CancellationError()
            }
            return best
        }
        let alone = try plan([a])
        let both = try plan([a, b])
        #expect(alone.finishedTrailKeys == [Key(areaId: "a", trailId: "near")])
        #expect(Set(both.finishedTrailKeys) == [Key(areaId: "a", trailId: "near"), Key(areaId: "b", trailId: "far")])
    }

    @Test func completionPerAreaDecidesWhatTheCrossAreaPlanSkips() throws {
        let a = Self.set("a", [Self.t("near", [(0, 0), (1000, 0)])])
        let b = Self.set("b", [Self.t("far", [(1000, 0), (2000, 0)])])
        let m = MultiAreaTrails.merge(areas: [a, b])
        let done = m.completion(TrailCompletionLookup(completed: [Key(areaId: "b", trailId: "far")]))
        let view = try TrailNetworkSnapper(network: TrailNetwork(trails: m.inputs)).insert(start: PlannerKit.pin(0, 0))
        let req = PlanOutcomeRequest(network: view, trails: m.inputs, completion: done, timeLimitSeconds: 3600)
        guard case .planned(let best, _) = RoutePlanningService.planOutcome(req) else {
            Issue.record("expected a plan"); return
        }
        #expect(best.finishedTrailKeys == [Key(areaId: "a", trailId: "near")])
    }
}
