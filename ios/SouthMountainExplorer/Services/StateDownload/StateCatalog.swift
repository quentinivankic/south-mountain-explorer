import Foundation

/// One downloadable group: a US state (or DC) or a non-US country.
struct StateScope: Identifiable, Equatable, Sendable {
    enum Kind: Equatable, Sendable { case usState, country }

    /// The index `state` string, e.g. "California".
    let id: String
    var displayName: String { id }
    let kind: Kind
    /// Visible (non-alias) area ids, sorted.
    let areaIDs: [String]
    /// Per-area trail counts for size estimation (missing = unknown).
    let trailCounts: [String: Int]
}

enum StateCatalog {
    static let usStateNames: Set<String> = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
        "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
        "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York",
        "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
        "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
    ]

    /// Groups areas by their index state. Hidden alias areas and blank states
    /// are skipped. Result is sorted by name.
    static func build(
        summaries: [AreaSummary],
        isHiddenAlias: (String) -> Bool
    ) -> [StateScope] {
        var ids: [String: [String]] = [:]
        var counts: [String: [String: Int]] = [:]
        var seen = Set<String>()
        for summary in summaries {
            let state = summary.subtitle.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !state.isEmpty,
                  !isHiddenAlias(summary.id),
                  seen.insert(summary.id).inserted
            else { continue }
            ids[state, default: []].append(summary.id)
            if let trails = summary.trailCount {
                counts[state, default: [:]][summary.id] = trails
            }
        }
        return ids.keys.sorted().map { state in
            StateScope(
                id: state,
                kind: usStateNames.contains(state) ? .usState : .country,
                areaIDs: ids[state, default: []].sorted(),
                trailCounts: counts[state, default: [:]]
            )
        }
    }

    /// Ids that are safe to use in a URL path and a file name.
    static func isSafeAreaID(_ id: String) -> Bool {
        !id.isEmpty && !id.contains("/") && !id.contains("..") && !id.contains("\\")
            && id.utf8.count <= 200
    }

    /// Stable file-name slug for a scope key.
    static func slug(_ stateKey: String) -> String {
        let mapped = stateKey.lowercased().map { $0.isLetter || $0.isNumber ? $0 : "-" }
        return String(mapped)
    }
}
