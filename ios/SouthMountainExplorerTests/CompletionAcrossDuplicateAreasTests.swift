import Testing
import Foundation
@testable import SouthMountainExplorer

/// Completion follows the PHYSICAL trail across duplicate areas.
///
/// Measured 2026-07-25: 903 areas (10%) have an identical-trail-set twin
/// (`south-mountain-preserve` vs `-park-and-preserve`), and completion stored
/// under one twin was invisible under the other. Matching is by GEOMETRY, not
/// id, because 5,205 ids collide across unrelated areas (`ice-age-trail` spans
/// 47 areas, a different segment each) — id-equality would over-credit.
@MainActor
struct CompletionAcrossDuplicateAreasTests {

    private func trail(_ id: String, _ segs: [[[Double]]]) -> Trail {
        Trail(id: id, name: id, distanceMi: 1, difficulty: .moderate, segments: segs)
    }
    private let geomA: [[[Double]]] = [[[33.30, -112.10], [33.31, -112.09], [33.32, -112.08]]]
    private let geomB: [[[Double]]] = [[[40.00, -105.00], [40.01, -105.01]]]  // different place

    @Test func identicalGeometry_sameFingerprint_differentGeometry_differs() {
        let a = trail("x", geomA)
        let aDup = trail("x", geomA)            // same id + geometry (the twin case)
        let collide = trail("x", geomB)          // same id, different trail (ice-age case)
        #expect(a.completionFingerprint == aDup.completionFingerprint)
        #expect(a.completionFingerprint != collide.completionFingerprint)
    }

    @Test func completionInOneAreaCreditsTheDuplicateTwin() async {
        ProgressService.shared.resetAll()
        let park = "south-mountain-park-and-preserve-az"
        let preserve = "south-mountain-preserve-az"     // identical geometry twin
        let inPark = trail("national-trail", geomA)
        let inPreserve = trail("national-trail", geomA)  // same trail, other container

        await ProgressService.shared.markComplete(areaId: park, trailId: "national-trail")
        // Backfill the fingerprint index from the park's geometry (as area load does).
        ProgressService.shared.indexArea(areaId: park, trails: [inPark])

        #expect(ProgressService.shared.isComplete(inPreserve, areaId: preserve),
                "progress under the park twin must show under the preserve twin")
    }

    @Test func doesNotCreditAGenuinelyDifferentTrailSharingAnId() async {
        ProgressService.shared.resetAll()
        let areaA = "area-a"
        let areaB = "area-b"
        await ProgressService.shared.markComplete(areaId: areaA, trailId: "loop-trail")
        ProgressService.shared.indexArea(areaId: areaA, trails: [trail("loop-trail", geomA)])
        // Same id, DIFFERENT geometry (a different park's loop-trail) — must NOT credit.
        #expect(ProgressService.shared.isComplete(trail("loop-trail", geomB), areaId: areaB) == false,
                "an id collision with different geometry must not over-credit")
    }

    @Test func mapColoringAndCountCreditTheTwin() async {
        ProgressService.shared.resetAll()
        let park = "south-mountain-park-and-preserve-az"
        let preserve = "south-mountain-preserve-az"
        let done = trail("national-trail", geomA)
        let notDone = trail("other-trail", geomB)

        await ProgressService.shared.markComplete(areaId: park, trailId: "national-trail")
        ProgressService.shared.indexArea(areaId: park, trails: [done])

        // The set every map surface (TrailMapView/DexView/WalkView) now uses.
        let colored = ProgressService.shared.completedTrailIds(
            in: preserve, among: [done, notDone])
        #expect(colored == ["national-trail"],
                "map must draw the twin-completed trail cyan and nothing else")
        // The count the home card now uses.
        #expect(ProgressService.shared.completionCount(
            in: preserve, trails: [done, notDone]) == 1)
    }

    @Test func resetClearsTheFingerprintIndex() async {
        ProgressService.shared.resetAll()
        await ProgressService.shared.markComplete(areaId: "a", trailId: "t")
        ProgressService.shared.indexArea(areaId: "a", trails: [trail("t", geomA)])
        ProgressService.shared.resetAll()
        #expect(ProgressService.shared.isComplete(trail("t", geomA), areaId: "b") == false)
    }
}
