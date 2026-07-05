import Testing
import Foundation
@testable import SouthMountainExplorer

/// Model-level guarantees the walk feature rests on: the new optional
/// fields round-trip, old records decode unchanged, and the walk-aware
/// accessors resolve per-area credits correctly for both walks and
/// legacy single-area hikes.
struct WalkRecordingTests {

    private func makeWalk() -> SavedRecording {
        SavedRecording(
            id: "walk-1",
            areaId: "primary-area",
            startedAt: Date(timeIntervalSince1970: 1_700_000_000),
            endedAt: Date(timeIntervalSince1970: 1_700_003_600),
            distanceMi: 3.2,
            durationSeconds: 3600,
            completedTrailIds: ["p-trail-1"],
            path: [],
            trailId: nil,
            revisitedTrailIds: [],
            multiAreaCompletions: [
                "primary-area": ["p-trail-1"],
                "second-area": ["s-trail-1", "s-trail-2"],
            ],
            multiAreaRevisited: [
                "second-area": ["s-trail-3"],
            ]
        )
    }

    @Test func walkFieldsRoundTripThroughCodable() throws {
        let walk = makeWalk()
        let data = try JSONEncoder().encode([walk])
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: data)
        #expect(decoded.count == 1)
        #expect(decoded[0].isWalk)
        #expect(decoded[0].multiAreaCompletions?["second-area"]?.sorted() == ["s-trail-1", "s-trail-2"])
        #expect(decoded[0].multiAreaRevisited?["second-area"] == ["s-trail-3"])
    }

    @Test func legacyRecordWithoutWalkFieldsDecodes() throws {
        // A pre-walk record's JSON has no multi-area keys — it must
        // decode as a non-walk with nil dicts (the whole history array
        // decodes with try?, so one failure would blank History).
        let legacyJSON = """
        [{"id":"h1","areaId":"a1","startedAt":700000000,"endedAt":700003600,
          "distanceMi":1.5,"durationSeconds":3600,
          "completedTrailIds":["t1"],"path":[]}]
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode([SavedRecording].self, from: legacyJSON)
        #expect(decoded.count == 1)
        #expect(!decoded[0].isWalk)
        #expect(decoded[0].multiAreaCompletions == nil)
        #expect(decoded[0].touchedAreaIds == ["a1"])
    }

    @Test func walkAccessorsResolvePerArea() {
        let walk = makeWalk()
        #expect(walk.touchedAreaIds == ["primary-area", "second-area"])
        #expect(walk.completedTrailIds(in: "primary-area") == ["p-trail-1"])
        #expect(walk.completedTrailIds(in: "second-area").sorted() == ["s-trail-1", "s-trail-2"])
        #expect(walk.completedTrailIds(in: "unrelated-area").isEmpty)
        #expect(walk.revisitedTrailIds(in: "second-area") == ["s-trail-3"])
        #expect(walk.revisitedTrailIds(in: "primary-area").isEmpty)
    }

    @Test func legacyAccessorsFallBackToFlatArrays() {
        let hike = SavedRecording(
            id: "h2",
            areaId: "a1",
            startedAt: Date(),
            endedAt: Date(),
            distanceMi: 2,
            durationSeconds: 100,
            completedTrailIds: ["t1"],
            path: [],
            revisitedTrailIds: ["t2"]
        )
        #expect(hike.completedTrailIds(in: "a1") == ["t1"])
        #expect(hike.completedTrailIds(in: "other").isEmpty)
        #expect(hike.revisitedTrailIds(in: "a1") == ["t2"])
    }

    @Test func walkAnchorUsesPerAreaCredits() {
        let walk = makeWalk()
        // A walk credited s-trail-1 in second-area: the anchor for that
        // trail IN that area must be the walk's end date, not the
        // ProgressService fallback stamp.
        let anchor = RecordingService.latestCompletionAnchor(
            trailId: "s-trail-1",
            areaId: "second-area",
            areaHistory: [walk],
            progressStamp: "2020-01-01T00:00:00Z"
        )
        #expect(anchor == walk.endedAt)
        // Same trail queried against the primary area: the walk never
        // credited it there, so the fallback stamp wins.
        let fallback = RecordingService.latestCompletionAnchor(
            trailId: "s-trail-1",
            areaId: "primary-area",
            areaHistory: [walk],
            progressStamp: "2020-01-01T00:00:00Z"
        )
        #expect(fallback == ISO8601DateFormatter().date(from: "2020-01-01T00:00:00Z"))
    }

    @Test func walkCannotBeRetargeted() {
        let walk = ActiveRecording(
            areaId: "primary-area",
            mode: .walk,
            trailId: nil,
            startedAt: Date(),
            path: [],
            distanceMi: 0,
            priorCompleteTrailIds: [],
            nearbyAreaIds: ["primary-area", "second-area"],
            priorCompleteByArea: [:]
        )
        #expect(RecordingService.retargeted(walk, newTrailId: "any-trail") == nil)
    }
}
