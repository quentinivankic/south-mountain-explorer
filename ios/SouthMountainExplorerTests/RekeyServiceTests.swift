import Testing
@testable import SouthMountainExplorer

/// Tests for `CoverageService.rekey` and `ProgressService.rekey` —
/// the pure-function forms of the build-8 migration's trail-id
/// remap. The migration walks every stored coverage fraction /
/// completion timestamp through `canonicalTrailId`, and when two
/// old keys collapse to the same canonical key it merges them with
/// different rules per service (max-fraction for coverage,
/// earliest-timestamp for completions).
struct RekeyServiceTests {

    // MARK: - CoverageService.rekey

    @Test func coverageRekey_noCollisions_passesThrough() {
        let input: [String: [String: Double]] = [
            "south-mountain": [
                "alta-0": 0.5,
                "pima-canyon-loop-trail-1": 0.8,
            ]
        ]
        let out = CoverageService.rekey(input, transform: { $0.canonicalTrailId })
        #expect(out == [
            "south-mountain": [
                "alta": 0.5,
                "pima-canyon-loop-trail": 0.8,
            ]
        ])
    }

    @Test func coverageRekey_collisionKeepsMax() {
        // Two legacy ids that canonicalize to the same key. The
        // user has more coverage on one than the other (e.g. they
        // walked the trail before the area rebuild AND after, and
        // the post-rebuild walk hit a different decimated node
        // count). We keep the higher fraction.
        let input: [String: [String: Double]] = [
            "south-mountain": [
                "unnamed-494466239-29": 0.3,
                "unnamed-494466239-43": 0.9,
            ]
        ]
        let out = CoverageService.rekey(input, transform: { $0.canonicalTrailId })
        #expect(out["south-mountain"]?["unnamed-494466239"] == 0.9)
        #expect(out["south-mountain"]?.count == 1)
    }

    @Test func coverageRekey_multipleAreasIndependent() {
        // Per-area dicts merge independently — a collision in one
        // area must not affect another area's keys.
        let input: [String: [String: Double]] = [
            "south-mountain": ["alta-0": 0.5],
            "white-tank":      ["alta-0": 0.7],
        ]
        let out = CoverageService.rekey(input, transform: { $0.canonicalTrailId })
        #expect(out["south-mountain"]?["alta"] == 0.5)
        #expect(out["white-tank"]?["alta"] == 0.7)
    }

    @Test func coverageRekey_empty() {
        let out = CoverageService.rekey([:], transform: { $0.canonicalTrailId })
        #expect(out.isEmpty)
    }

    @Test func coverageRekey_idempotency() {
        // Running the rekey twice on already-canonical data must be
        // a no-op. Important because the migration runs once per
        // schema bump but the same canonicalizer is also applied
        // at every load boundary.
        let canonical: [String: [String: Double]] = [
            "south-mountain": [
                "alta": 0.5,
                "unnamed-494466239": 0.9,
            ]
        ]
        let once = CoverageService.rekey(canonical, transform: { $0.canonicalTrailId })
        let twice = CoverageService.rekey(once, transform: { $0.canonicalTrailId })
        #expect(once == canonical)
        #expect(twice == canonical)
    }

    // MARK: - ProgressService.rekey

    @Test func progressRekey_noCollisions_passesThrough() {
        let input: [String: [String: String]] = [
            "south-mountain": [
                "alta-0": "2025-05-10T12:00:00Z",
                "pima-canyon-loop-trail-1": "2025-05-11T08:30:00Z",
            ]
        ]
        let out = ProgressService.rekey(input, transform: { $0.canonicalTrailId })
        #expect(out == [
            "south-mountain": [
                "alta": "2025-05-10T12:00:00Z",
                "pima-canyon-loop-trail": "2025-05-11T08:30:00Z",
            ]
        ])
    }

    @Test func progressRekey_collisionKeepsEarliestTimestamp() {
        // Two legacy ids canonicalize to the same key, with
        // different completion timestamps. The earlier one wins —
        // the user marked it complete first, even if the second
        // entry post-dated it.
        let input: [String: [String: String]] = [
            "south-mountain": [
                "unnamed-494466239-29": "2025-05-10T15:00:00Z",
                "unnamed-494466239-43": "2025-05-11T09:00:00Z",
            ]
        ]
        let out = ProgressService.rekey(input, transform: { $0.canonicalTrailId })
        #expect(out["south-mountain"]?["unnamed-494466239"] == "2025-05-10T15:00:00Z")
        #expect(out["south-mountain"]?.count == 1)
    }

    @Test func progressRekey_collisionWithReversedTimestampOrder() {
        // Same test as above but with the entries in the opposite
        // order — the algorithm's collision rule must not depend on
        // dictionary iteration order (which Swift doesn't
        // guarantee). Earlier still wins.
        let input: [String: [String: String]] = [
            "south-mountain": [
                "unnamed-494466239-43": "2025-05-11T09:00:00Z",
                "unnamed-494466239-29": "2025-05-10T15:00:00Z",
            ]
        ]
        let out = ProgressService.rekey(input, transform: { $0.canonicalTrailId })
        #expect(out["south-mountain"]?["unnamed-494466239"] == "2025-05-10T15:00:00Z")
    }

    @Test func progressRekey_empty() {
        let out = ProgressService.rekey([:], transform: { $0.canonicalTrailId })
        #expect(out.isEmpty)
    }

    @Test func progressRekey_idempotency() {
        let canonical: [String: [String: String]] = [
            "south-mountain": [
                "alta": "2025-05-10T12:00:00Z",
                "unnamed-494466239": "2025-05-11T09:00:00Z",
            ]
        ]
        let once = ProgressService.rekey(canonical, transform: { $0.canonicalTrailId })
        let twice = ProgressService.rekey(once, transform: { $0.canonicalTrailId })
        #expect(once == canonical)
        #expect(twice == canonical)
    }
}
