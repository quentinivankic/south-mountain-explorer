import Foundation

// areaId -> trailId -> coverage (0..1)
private let storageKey = StorageKeys.coverage
private let sinceCompletionStorageKey = StorageKeys.coverageSinceCompletion

@MainActor
@Observable
final class CoverageService {
    static let shared = CoverageService()

    /// Lifetime coverage. Monotonically increases (max-merge), never
    /// resets except via Reset All Progress. Drives "% complete on
    /// the lifetime hike" semantics and the area-level completion
    /// fraction.
    private(set) var state: [String: [String: Double]] = [:]

    /// Coverage accumulated *since the last completion* of each
    /// trail. Resets to 0 the moment a trail crosses the completion
    /// threshold so the next completion cycle starts fresh. Drives
    /// the "X% remaining" copy in TrailDetailSheet and the map's
    /// walked-since-completion overlay.
    ///
    /// For trails that have never completed, this mirrors lifetime
    /// coverage. The two diverge only after the first completion.
    private(set) var sinceCompletion: [String: [String: Double]] = [:]

    private init() {
        state = readLocal(key: storageKey)
        sinceCompletion = readLocal(key: sinceCompletionStorageKey)
        backfillSinceCompletionIfNeeded()
    }

    /// One-shot migration that runs the first time this build ships
    /// to a device with prior coverage state. Without it, every
    /// already-tracked trail would suddenly read as "0% remaining"
    /// in the sheet — including never-completed trails that the
    /// user had been progressing toward.
    ///
    /// Rule: for each trail with a lifetime coverage entry, seed
    /// `sinceCompletion` with that fraction *unless the trail is
    /// already complete* in ProgressService — completed trails
    /// start fresh at 0 for the next cycle.
    ///
    /// Idempotent via the empty-`sinceCompletion` gate: once the
    /// migration runs and writes anything, the dict is non-empty
    /// and skipped on future launches. A user who's truly never
    /// recorded a hike (no lifetime coverage) sails through with
    /// no work.
    private func backfillSinceCompletionIfNeeded() {
        guard sinceCompletion.isEmpty, !state.isEmpty else { return }
        let completions = ProgressService.shared.completions
        var seeded: [String: [String: Double]] = [:]
        for (areaId, areaCov) in state {
            let areaCompletions = completions[areaId] ?? [:]
            var bucket: [String: Double] = [:]
            for (tid, v) in areaCov {
                bucket[tid] = areaCompletions[tid] != nil ? 0 : v
            }
            seeded[areaId] = bucket
        }
        sinceCompletion = seeded
        saveLocal()
    }

    func coverage(for areaId: String) -> [String: Double] {
        state[areaId] ?? [:]
    }

    func trailCoverage(areaId: String, trailId: String) -> Double {
        state[areaId]?[trailId] ?? 0
    }

    /// Per-trail "covered since last completion" fraction. For
    /// never-completed trails this equals lifetime coverage.
    func coverageSinceCompletion(for areaId: String) -> [String: Double] {
        sinceCompletion[areaId] ?? [:]
    }

    func trailCoverageSinceCompletion(areaId: String, trailId: String) -> Double {
        sinceCompletion[areaId]?[trailId] ?? 0
    }

    func mergeCoverage(areaId: String, delta: [String: Double]) async {
        var area = state[areaId] ?? [:]
        for (tid, v) in delta {
            area[tid] = min(1.0, max(area[tid] ?? 0, v))
        }
        state[areaId] = area
        // The since-completion bucket is NOT mutated here. Its
        // authority is `setSinceCompletion` (called from
        // `rebuildCoverageFromHistory` with length-based 10 m math —
        // PR #125) and `resetSinceCompletion` (the completion event
        // itself). Mid-hike, the TrailDetailSheet bar no longer ticks
        // up live for uncompleted trails (it was at the wrong
        // precision anyway); it settles to correct on the next
        // AreaView reopen via the rebuildCoverageFromHistory path.
        // Completed trails still reset to 0% via the explicit
        // `resetSinceCompletion` call from RecordingService.
        saveLocal()
    }

    /// Reset the since-completion bucket for a trail to 0 — the
    /// completion event itself. Called by RecordingService.stopRecording
    /// for every trail in `newlyCompletedTrailIds`. Lifetime coverage
    /// is left untouched (it stays at ≥ 0.95 / completeThreshold).
    func resetSinceCompletion(areaId: String, trailId: String) async {
        guard var area = sinceCompletion[areaId] else { return }
        area[trailId] = 0
        sinceCompletion[areaId] = area
        saveLocal()
    }

    /// Replace the entire since-completion map for an area. Used by
    /// the migration that backfills the field on first launch after
    /// this feature ships — given history + completion timestamps,
    /// we replay post-completion hikes to derive fresh per-trail
    /// fractions.
    func setSinceCompletion(areaId: String, values: [String: Double]) async {
        sinceCompletion[areaId] = values
        saveLocal()
    }

    func resetAreaCoverage(areaId: String) async {
        state[areaId] = [:]
        sinceCompletion[areaId] = [:]
        saveLocal()
    }

    /// Rewrite every trail-id key through `transform`. Used by the
    /// build-8 migration to canonicalize legacy ids (stripping the
    /// position-counter suffix) so old recorded coverage lines up
    /// with stable post-fix ids. On collision (two old keys collapse
    /// to the same canonical key), keep the higher fraction.
    func rekeyTrailIds(_ transform: (String) -> String) {
        state = Self.rekey(state, transform: transform)
        sinceCompletion = Self.rekey(sinceCompletion, transform: transform)
        saveLocal()
    }

    /// Pure-function form of the rekey-with-collision-merge logic.
    /// Lives alongside the instance method so unit tests can
    /// exercise the merge rule without singleton / UserDefaults
    /// plumbing. The collision rule is "keep the higher fraction"
    /// — when two old keys collapse to the same canonical key, we
    /// preserve whatever the user has actually covered the most of.
    ///
    /// `nonisolated` because this is a pure function over its
    /// arguments — it touches no `CoverageService` instance state.
    /// Without this the static would inherit the enclosing class's
    /// `@MainActor` isolation and become unreachable from
    /// non-actor-isolated callers (including unit tests).
    nonisolated static func rekey(_ state: [String: [String: Double]],
                                  transform: (String) -> String) -> [String: [String: Double]] {
        var newState: [String: [String: Double]] = [:]
        for (areaId, areaCov) in state {
            var newArea: [String: Double] = [:]
            for (tid, v) in areaCov {
                let newTid = transform(tid)
                newArea[newTid] = max(newArea[newTid] ?? 0, v)
            }
            newState[areaId] = newArea
        }
        return newState
    }

    // MARK: - Local persistence

    private func readLocal(key: String) -> [String: [String: Double]] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let decoded = try? JSONDecoder().decode([String: [String: Double]].self, from: data)
        else { return [:] }
        return decoded
    }

    private func saveLocal() {
        if let data = try? JSONEncoder().encode(state) {
            UserDefaults.standard.set(data, forKey: storageKey)
        }
        if let data = try? JSONEncoder().encode(sinceCompletion) {
            UserDefaults.standard.set(data, forKey: sinceCompletionStorageKey)
        }
    }
}
