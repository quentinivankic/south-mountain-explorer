import Testing
@testable import SouthMountainExplorer

struct TrailSelectionResolverTests {
    private func candidate(_ id: String, _ name: String) -> TrailSelectionCandidate {
        TrailSelectionCandidate(id: id, name: name)
    }

    @Test func exactIdAndNameMatchWins() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "exit-3",
            requestedTrailName: "Exit 3",
            candidates: [
                candidate("exit-3", "Exit 3"),
                candidate("exit-west", "Exit 3")
            ]
        )

        #expect(resolved == "exit-3")
    }

    @Test func canonicalizedIdMismatchResolvesByUniqueNumericName() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "exit-3",
            requestedTrailName: "Exit 3",
            candidates: [
                candidate("exit", "Exit 3"),
                candidate("national-trail", "National Trail")
            ]
        )

        #expect(resolved == "exit")
    }

    @Test func duplicateExactNameWithoutExactPairFailsClosed() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "legacy-exit-3",
            requestedTrailName: "Exit 3",
            candidates: [
                candidate("exit-east", "Exit 3"),
                candidate("exit-west", "Exit 3")
            ]
        )

        #expect(resolved == nil)
    }

    @Test func wrongExactIdDoesNotOverrideUniqueExactName() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "exit-3",
            requestedTrailName: "Exit 3",
            candidates: [
                candidate("exit-3", "Exit 2"),
                candidate("exit", "Exit 3")
            ]
        )

        #expect(resolved == "exit")
    }

    @Test func suppliedNameNeverFallsBackToWrongExactId() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "exit-3",
            requestedTrailName: "Exit 3",
            candidates: [candidate("exit-3", "Exit 2")]
        )

        #expect(resolved == nil)
    }

    @Test func missingNameAllowsUniqueExactIdFallback() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "national-trail",
            requestedTrailName: nil,
            candidates: [
                candidate("national-trail", "National Trail"),
                candidate("mormon-trail", "Mormon Trail")
            ]
        )

        #expect(resolved == "national-trail")
    }

    @Test func duplicateLoadedIdFailsClosedEvenWhenNameIsUnique() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "legacy-exit-3",
            requestedTrailName: "Exit 3",
            candidates: [
                candidate("exit", "Exit 2"),
                candidate("exit", "Exit 3")
            ]
        )

        #expect(resolved == nil)
    }

    @Test func idOnlyFallbackFailsClosedOnDuplicateId() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "exit",
            requestedTrailName: nil,
            candidates: [
                candidate("exit", "Exit 2"),
                candidate("exit", "Exit 3")
            ]
        )

        #expect(resolved == nil)
    }

    @Test func exactPairStillFailsWhenLoadedIdCannotIdentifyOneTrail() {
        let resolved = TrailSelectionResolver.resolve(
            requestedRawTrailId: "exit",
            requestedTrailName: "Exit 3",
            candidates: [
                candidate("exit", "Exit 3"),
                candidate("exit", "Exit 2")
            ]
        )

        #expect(resolved == nil)
    }
}
