import Testing
@testable import SouthMountainExplorer

/// Tests for the `String.canonicalTrailId` extension in `Trail.swift`.
/// This helper is the iOS-side guard against legacy build-script
/// trail ids that carried an unstable trailing position counter
/// (`{slug}-{N}` where N was the trail's index in the
/// alphabetically-sorted build list). The fix in build 8 strips
/// that counter at every load boundary so old persisted hike
/// history lines up with new CDN payloads.
///
/// The threshold for "is this a position counter or a real wayid":
/// trailing `-<digits>` with 1-3 digits = strip; ≥4 digits = leave
/// alone (OSM way ids are always 7+ digits in practice).
struct CanonicalTrailIdTests {

    // MARK: - Already-canonical ids stay unchanged (idempotent)

    @Test func plainSlugUnchanged() {
        #expect("alta".canonicalTrailId == "alta")
        #expect("desert-classic-trail".canonicalTrailId == "desert-classic-trail")
        #expect("pima-canyon-loop-trail".canonicalTrailId == "pima-canyon-loop-trail")
    }

    @Test func unnamedWayidNotStripped() {
        // The 9-digit wayid is the entire ID body for unnamed trails.
        // This is the regression that broke build-7's migration:
        // build 8 must NOT collapse "unnamed-494466239" → "unnamed".
        #expect("unnamed-494466239".canonicalTrailId == "unnamed-494466239")
        #expect("unnamed-1474825928".canonicalTrailId == "unnamed-1474825928")
        #expect("unnamed-977459640".canonicalTrailId == "unnamed-977459640")
    }

    @Test func idempotency() {
        // Running the canonicalizer twice produces the same result
        // as running it once. This matters because the migration
        // and the load-time canonicalizer both call it on the same
        // data path — the second call must be a no-op.
        let inputs = [
            "alta",
            "alta-0",
            "unnamed-494466239",
            "unnamed-494466239-43",
            "pima-canyon-loop-trail-1",
        ]
        for s in inputs {
            let once = s.canonicalTrailId
            let twice = once.canonicalTrailId
            #expect(once == twice, "Not idempotent for input: \(s)")
        }
    }

    // MARK: - Legacy position counters get stripped

    @Test func shortPositionCounterStripped() {
        // Counters from the old build script for areas <1000 trails
        // are 1-3 digits. All should strip.
        #expect("alta-0".canonicalTrailId == "alta")
        #expect("alta-5".canonicalTrailId == "alta")
        #expect("alta-42".canonicalTrailId == "alta")
        #expect("alta-999".canonicalTrailId == "alta")
    }

    @Test func multiHyphenSlugWithCounterStripped() {
        // The common case for named trails — slug has many parts,
        // last part is the counter.
        #expect("pima-canyon-loop-trail-0".canonicalTrailId == "pima-canyon-loop-trail")
        #expect("desert-classic-trail-42".canonicalTrailId == "desert-classic-trail")
    }

    @Test func unnamedWithLegacyCounterStripped() {
        // The bug trigger: unnamed slugs in legacy format carried
        // BOTH the wayid AND a position counter. Build 8 strips the
        // trailing counter and keeps the wayid in place so the
        // result matches the new CDN format.
        #expect("unnamed-494466239-43".canonicalTrailId == "unnamed-494466239")
        #expect("unnamed-494466239-29".canonicalTrailId == "unnamed-494466239")
        #expect("unnamed-1474825928-0".canonicalTrailId == "unnamed-1474825928")
    }

    // MARK: - Edge cases

    @Test func emptyString() {
        #expect("".canonicalTrailId == "")
    }

    @Test func trailingDashLeftAlone() {
        // A dash with no digits after it isn't a counter — leave it
        // alone rather than try to interpret it.
        #expect("alta-".canonicalTrailId == "alta-")
    }

    @Test func nonDigitTailLeftAlone() {
        // A dash followed by non-digits is just part of the slug.
        #expect("alta-trail".canonicalTrailId == "alta-trail")
        #expect("abc-def".canonicalTrailId == "abc-def")
    }

    @Test func fourPlusDigitTailLeftAlone() {
        // 4+ trailing digits is too long for a position counter
        // (no area has 1000+ trails) and matches the wayid signature.
        // Conservative call: don't strip.
        #expect("alta-1234".canonicalTrailId == "alta-1234")
        #expect("trail-12345".canonicalTrailId == "trail-12345")
        #expect("foo-494466239".canonicalTrailId == "foo-494466239")
    }
}
