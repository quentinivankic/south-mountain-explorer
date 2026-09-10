/// The minimum loaded-area identity needed to resolve a trail selection.
///
/// Keeping this independent of `Trail` makes the resolver deterministic,
/// side-effect free, and usable from a standalone Swift harness.
struct TrailSelectionCandidate: Equatable, Sendable {
    let id: String
    let name: String
}

/// Resolves a raw search/notification identity against the trails that the
/// client actually loaded. IDs and names are opaque, exact strings: this code
/// never canonicalizes either one.
enum TrailSelectionResolver {
    static func resolve(
        requestedRawTrailId: String,
        requestedTrailName: String?,
        candidates: [TrailSelectionCandidate]
    ) -> String? {
        if let requestedTrailName {
            let exactPairMatches = candidates.filter {
                $0.id == requestedRawTrailId && $0.name == requestedTrailName
            }
            if exactPairMatches.count == 1 {
                return uniquelySelectableId(exactPairMatches[0].id, in: candidates)
            }
            guard exactPairMatches.isEmpty else { return nil }

            // The normal compatibility path for the current loader: it may
            // have canonicalized a numeric-suffix ID (for example exit-3 ->
            // exit), while the exact trail name remains unchanged.
            let exactNameMatches = candidates.filter { $0.name == requestedTrailName }
            guard exactNameMatches.count == 1 else { return nil }
            return uniquelySelectableId(exactNameMatches[0].id, in: candidates)
        }

        // Legacy/local callers that have no name may select only when the raw
        // ID identifies exactly one loaded trail.
        let exactIdMatches = candidates.filter { $0.id == requestedRawTrailId }
        guard exactIdMatches.count == 1 else { return nil }
        return exactIdMatches[0].id
    }

    /// AreaView and its map/list children represent selection as a trail ID.
    /// A candidate whose loaded ID is duplicated cannot be represented safely,
    /// even when its name is unique, because an ID-only consumer could pick the
    /// other trail. Fail closed instead.
    private static func uniquelySelectableId(
        _ id: String,
        in candidates: [TrailSelectionCandidate]
    ) -> String? {
        var matchCount = 0
        for candidate in candidates where candidate.id == id {
            matchCount += 1
            if matchCount > 1 { return nil }
        }
        return matchCount == 1 ? id : nil
    }
}
