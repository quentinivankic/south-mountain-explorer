import Testing
import Foundation
@testable import SouthMountainExplorer

/// Guarantees for trail/roam multi-area completion: the persisted `mode`
/// decodes safely (a multi-area HIKE isn't mistaken for a walk, old records
/// infer their mode, and an unknown value can never throw and blank
/// History), and the pure area/path bbox-overlap test behaves.
struct MultiAreaHikeTests {

    // MARK: - Persisted mode (SavedRecording)

    @Test func multiAreaHikeIsNotAWalk() throws {
        // The whole point: a trail/roam hike that credited a neighbor area
        // carries multiAreaCompletions, but mode="trail" keeps it a HIKE.
        let json = """
        [{"id":"h1","areaId":"a1","startedAt":700000000,"endedAt":700003600,
          "distanceMi":4.0,"durationSeconds":3600,"completedTrailIds":["t1"],
          "path":[],"mode":"trail",
          "multiAreaCompletions":{"a1":["t1"],"a2":["t9"]},
          "multiAreaRevisited":{}}]
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: json)
        #expect(decoded.count == 1)
        #expect(!decoded[0].isWalk)                       // hike, not walk
        #expect(decoded[0].mode == .trail)
        // …but it still credits both areas (multiArea data intact).
        #expect(decoded[0].touchedAreaIds == ["a1", "a2"])
        #expect(decoded[0].completedTrailIds(in: "a2") == ["t9"])
    }

    @Test func explicitWalkModeDecodes() throws {
        let json = """
        [{"id":"w1","areaId":"a1","startedAt":700000000,"endedAt":700003600,
          "distanceMi":3.0,"durationSeconds":3600,"completedTrailIds":["t1"],
          "path":[],"mode":"walk","multiAreaCompletions":{"a1":["t1"]}}]
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: json)
        #expect(decoded[0].isWalk)
        #expect(decoded[0].mode == .walk)
    }

    @Test func legacyWalkWithoutModeInfersWalk() throws {
        // Old walk record (no `mode` key) still reads as a walk via the
        // multiAreaCompletions-presence fallback — preserving prior behavior.
        let json = """
        [{"id":"w0","areaId":"a1","startedAt":700000000,"endedAt":700003600,
          "distanceMi":3.0,"durationSeconds":3600,"completedTrailIds":["t1"],
          "path":[],"multiAreaCompletions":{"a1":["t1"]}}]
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: json)
        #expect(decoded[0].isWalk)
        #expect(decoded[0].mode == .walk)
    }

    @Test func legacyHikeAndRoamWithoutModeInfer() throws {
        // No mode, no multiArea: a trailId → trail; none → roam.
        let json = """
        [{"id":"h","areaId":"a1","startedAt":700000000,"endedAt":700003600,
          "distanceMi":1.0,"durationSeconds":600,"completedTrailIds":[],
          "path":[],"trailId":"t1"},
         {"id":"r","areaId":"a1","startedAt":700000000,"endedAt":700003600,
          "distanceMi":1.0,"durationSeconds":600,"completedTrailIds":[],
          "path":[]}]
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: json)
        #expect(decoded[0].mode == .trail)
        #expect(decoded[1].mode == .roam)
        #expect(!decoded[0].isWalk && !decoded[1].isWalk)
    }

    @Test func unknownModeValueFallsBackWithoutThrowing() throws {
        // A future/unknown mode string must NOT throw — the whole history
        // array decodes with try?, so one throwing record would blank it.
        let json = """
        [{"id":"x","areaId":"a1","startedAt":700000000,"endedAt":700003600,
          "distanceMi":1.0,"durationSeconds":600,"completedTrailIds":[],
          "path":[],"mode":"skitour","trailId":"t1"}]
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: json)
        #expect(decoded.count == 1)               // decoded, did not throw
        #expect(decoded[0].mode == .trail)        // fell back via trailId
    }

    @Test func modeRoundTripsThroughCodable() throws {
        let hike = SavedRecording(
            id: "h", areaId: "a1",
            startedAt: Date(timeIntervalSince1970: 1_700_000_000),
            endedAt: Date(timeIntervalSince1970: 1_700_003_600),
            distanceMi: 2.0, durationSeconds: 3600,
            completedTrailIds: ["t1"], path: [], trailId: "t1",
            revisitedTrailIds: [],
            multiAreaCompletions: ["a1": ["t1"], "a2": ["t9"]],
            multiAreaRevisited: nil,
            mode: .trail
        )
        let data = try JSONEncoder().encode([hike])
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: data)
        #expect(decoded[0].mode == .trail)
        #expect(!decoded[0].isWalk)
        #expect(decoded[0].touchedAreaIds == ["a1", "a2"])
    }

    // MARK: - Pure path/bbox entry gate

    @Test func pathEntersBBoxDetectsPointInside() {
        // area bbox [minLon, minLat, maxLon, maxLat]; one path point inside.
        let path: [GpsPoint] = [[33.10, -112.50], [33.45, -112.15], [33.05, -112.60]]
        #expect(RecordingService.pathEntersBBox(path, areaBBox: [-112.30, 33.30, -112.10, 33.50]))
    }

    @Test func pathEntersBBoxRejectsWhenAllOutside() {
        let path: [GpsPoint] = [[33.10, -112.50], [33.05, -112.60]]
        #expect(!RecordingService.pathEntersBBox(path, areaBBox: [-112.30, 33.30, -112.10, 33.50]))
    }

    @Test func pathEntersBBoxNilIsFalse() {
        #expect(!RecordingService.pathEntersBBox([[33.4, -112.2]], areaBBox: nil))
    }
}
