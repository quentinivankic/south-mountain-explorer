import Foundation
import CoreLocation
import OSLog

/// Logger for `RecordingService` lifecycle events — start, stop,
/// discard, retarget, coverage merges. Surfaced in the Send
/// Diagnostics bundle so a field bug report carries the actual
/// sequence of events that led to it, not just the user's
/// reconstruction. Subsystem matches `DiagnosticsService`'s
/// filter so the entries land in the bundle.
private let log = Logger(subsystem: "com.trekdex.app", category: "recording")

/// Fraction-of-trail-nodes-covered required for a trail to count
/// as complete. Bumped from 0.90 → 0.95 in build 13 after device
/// testing showed completions celebrating noticeably before the
/// user reached the actual end of the trail. With the build-8
/// raw-geometry split, fraction now reflects dense-node coverage
/// accurately, so 0.95 is achievable on real walks without being
/// frustrating.
private let completeThreshold = 0.95
private let bufferMeters = 30.0
/// Tighter buffer used by the `sinceCompletion` measurement and
/// the matching orange overlay on the map. Lifetime coverage
/// (and the area-level completion fraction) use the looser
/// `bufferMeters = 30` so a "near a trail" GPS sample still
/// credits the trail node. The post-completion measurement needs
/// to distinguish "actually walked here" from "passed near while
/// on a different trail" — a 10m buffer matches the completion
/// gate's precision and gives the orange overlay realistic
/// "I drifted across this trail" semantics rather than "this
/// trail crossed mine, count it all."
private let sinceCompletionBufferMeters = 10.0
private let jitterMeters = 3.0
private let badFixMeters = 200.0

/// Interval between samples of `LocationService.liveLocation` while
/// a recording is active. 2 s is a comfortable hiking cadence —
/// frequent enough that GPS jitter is averaged out by the
/// `jitterMeters` filter, infrequent enough to keep battery use
/// reasonable on a multi-hour hike.
private let gpsPollingInterval: Duration = .seconds(2)

/// Closest haversine distance in meters from any sample in `path`
/// to `(lat, lon)`. Returns `.infinity` for an empty path. Used by
/// the completion-gate diagnostic logs to capture how close the
/// user actually got to each trail endpoint when a gate fired.
private func closestPathDistanceMeters(path: [GpsPoint], lat: Double, lon: Double) -> Double {
    var best = Double.infinity
    for p in path where p.count >= 2 {
        let d = haversineDistanceM(lat1: lat, lon1: lon, lat2: p[0], lon2: p[1])
        if d < best { best = d }
    }
    return best
}

/// Distance in meters from the GPS samples in `path` to a trail's
/// first and last polyline node. Returns `(-1, -1)` when the trail
/// isn't in `trails` or its first/last segment is empty — those
/// sentinels are surfaced in the log so an unexpected -1 reads as
/// "lookup failed" rather than "user was 0 m away." Shared by the
/// three completion-gate diagnostic call sites (trailComplete,
/// trailRevisit, trailRetroComplete) so they all describe endpoint
/// distance the same way.
private func trailEndpointDistances(
    trailId: String,
    trails: [Trail],
    path: [GpsPoint]
) -> (startDist: Double, endDist: Double) {
    guard let trail = trails.first(where: { $0.id == trailId }) else {
        return (-1, -1)
    }
    let startDist: Double = {
        guard let p = trail.segments.first?.first, p.count >= 2 else { return -1 }
        return closestPathDistanceMeters(path: path, lat: p[0], lon: p[1])
    }()
    let endDist: Double = {
        guard let p = trail.segments.last?.last, p.count >= 2 else { return -1 }
        return closestPathDistanceMeters(path: path, lat: p[0], lon: p[1])
    }()
    return (startDist, endDist)
}

@MainActor
@Observable
final class RecordingService {
    static let shared = RecordingService()

    private(set) var activeRecording: ActiveRecording? = nil
    private(set) var errorMessage: String? = nil

    private let locationService = LocationService.shared
    private var locationObserver: Task<Void, Never>? = nil

    private let persistKey = StorageKeys.activeRecording

    private static var historyFileURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("hike-history.json")
    }

    private init() {
        migrateHistoryClassificationIfNeeded()
        restoreActiveRecording()
    }

    /// Restore an in-progress recording from UserDefaults on launch.
    /// Public TestFlight users will occasionally have iOS kill the
    /// app mid-hike (memory pressure, force-quit, watchdog) — without
    /// this restore path they lose every GPS sample since the start
    /// of the hike.
    ///
    /// Drops the persisted recording silently if:
    ///   - decode fails (corrupted file) — bail clean, start fresh.
    ///   - `startedAt` is more than `maxResumeAge` ago — the user
    ///     forgot they had a recording running and wouldn't expect
    ///     it to come back to life. 12h covers a full hiking day +
    ///     overnight; longer than that is almost certainly stale.
    ///
    /// On a clean restore, ALSO restarts background location updates
    /// (`startBackgroundTracking`) — without this the activeRecording
    /// is back in memory but no new GPS samples flow into it.
    private func restoreActiveRecording() {
        guard let data = UserDefaults.standard.data(forKey: persistKey) else { return }
        guard let restored = try? JSONDecoder().decode(ActiveRecording.self, from: data) else {
            // Decode failure — leave a breadcrumb and clear the bad
            // blob so we don't try again on every cold launch.
            log.error("restoreActiveRecording: decode failed, dropping persisted state")
            UserDefaults.standard.removeObject(forKey: persistKey)
            return
        }
        let age = Date().timeIntervalSince(restored.startedAt)
        let maxResumeAge: TimeInterval = 12 * 60 * 60
        guard age < maxResumeAge else {
            log.notice("restoreActiveRecording: skipping stale recording age=\(Int(age))s areaId=\(restored.areaId, privacy: .public)")
            UserDefaults.standard.removeObject(forKey: persistKey)
            return
        }
        activeRecording = restored
        log.notice("restoreActiveRecording: resumed areaId=\(restored.areaId, privacy: .public) trailId=\(restored.trailId ?? "nil", privacy: .public) pathPoints=\(restored.path.count) age=\(Int(age))s")
        ActivityLogService.shared.log(
            category: "recording",
            action: "resume",
            context: [
                "areaId": restored.areaId,
                "trailId": restored.trailId ?? "nil",
                "pathPoints": String(restored.path.count),
                "ageSeconds": String(Int(age)),
            ]
        )
        // Re-arm background GPS so new samples actually flow into the
        // restored path. Without this call the recording sits in
        // memory but goes nowhere — the user would tap Stop and save
        // a hike that ended at the moment of the app kill.
        locationService.startBackgroundTracking()
        beginObservingLocation()
    }

    // MARK: - History migrations

    /// One-shot backfill, currently at schema v2.
    ///
    /// **v1** (build 7): fix the History "previously completed" bug
    /// where pre-build-6 `stopRecording` read its own intra-session
    /// `applyLiveCoverage` writes back from `CoverageService` and
    /// shuffled newly-completed trails into `revisitedTrailIds`. The
    /// union per record is correct (the bug never drops trails), so
    /// we rebuild the classification by walking history
    /// chronologically with a running "ever-complete-before-this-hike"
    /// set and re-splitting each hike's union against it.
    ///
    /// **v2** (build 8): canonicalize every stored trail id through
    /// `String.canonicalTrailId`. Legacy build-trail-counts.py emitted
    /// `{slug}-{position}` ids where `position` was unstable across
    /// area rebuilds, so the same physical trail received different
    /// ids on different days and broke dedup. v2 strips the suffix
    /// from `SavedRecording.completedTrailIds` /
    /// `revisitedTrailIds`, then re-runs the v1 chronological
    /// reclassification (now with stable ids), then rekeys
    /// `CoverageService.state` and `ProgressService.completions`
    /// through the same canonicalizer (with collision-merge by
    /// max-fraction / earliest-stamp respectively).
    ///
    /// Both passes are idempotent. Version marker prevents re-walking
    /// the file on every cold launch.
    private func migrateHistoryClassificationIfNeeded() {
        let ud = UserDefaults.standard
        let key = StorageKeys.hikeHistoryMigrationVersion
        let currentVersion = ud.integer(forKey: key)
        guard currentVersion < 2 else { return }

        let history = loadHistorySync()
        if !history.isEmpty {
            let sorted = history.sorted { $0.startedAt < $1.startedAt }
            var everComplete: Set<String> = []
            var rebuilt: [SavedRecording] = []
            rebuilt.reserveCapacity(sorted.count)
            for hike in sorted {
                // Canonicalize first, then take the union. Two
                // legacy ids that differ only by position counter
                // collapse to the same canonical id and naturally
                // de-dupe in the Set.
                let canonCompleted = hike.completedTrailIds.map(\.canonicalTrailId)
                let canonRevisited = hike.revisitedTrailIds.map(\.canonicalTrailId)
                let union = Set(canonCompleted).union(canonRevisited)
                let newly = union.subtracting(everComplete)
                let revisited = union.intersection(everComplete)
                rebuilt.append(SavedRecording(
                    id: hike.id,
                    areaId: hike.areaId,
                    startedAt: hike.startedAt,
                    endedAt: hike.endedAt,
                    distanceMi: hike.distanceMi,
                    durationSeconds: hike.durationSeconds,
                    completedTrailIds: Array(newly),
                    path: hike.path,
                    trailId: hike.trailId,
                    revisitedTrailIds: Array(revisited),
                    // Walk fields pass through untouched — walks can only
                    // exist on builds that are already at migration v2+
                    // (their ids are canonical from birth), but this
                    // rebuild must never strip fields it doesn't know.
                    multiAreaCompletions: hike.multiAreaCompletions,
                    multiAreaRevisited: hike.multiAreaRevisited,
                    // Carry mode through: this rebuild preserves the walk
                    // fields, so it must preserve walk-ness too (isWalk now
                    // reads `mode`, not the presence of multiAreaCompletions).
                    mode: hike.mode
                ))
                everComplete.formUnion(union)
            }
            if let data = try? JSONEncoder().encode(rebuilt) {
                try? data.write(to: Self.historyFileURL)
            }
        }

        // Rekey the persisted-and-in-memory coverage + progress
        // dicts. Touching `.shared` triggers init if those services
        // haven't been used yet; rekey then mutates state in place
        // and persists. Subsequent reads see canonical keys.
        CoverageService.shared.rekeyTrailIds { $0.canonicalTrailId }
        ProgressService.shared.rekeyTrailIds { $0.canonicalTrailId }

        ud.set(2, forKey: key)
    }

    // MARK: - Start / Stop

    func startRecording(areaId: String, mode: RecordingMode, trailId: String? = nil) {
        // Snapshot which trails are ALREADY complete in this area at
        // recording-start. Used by stopRecording to classify each
        // covered trail as "newly completed" (not in snapshot) vs
        // "revisited" (in snapshot) — independent of the intra-session
        // CoverageService writes that applyLiveCoverage performs.
        // OFFICIALLY-completed trails only — NOT bare coverage fraction. A trail
        // can sit at fraction >= completeThreshold from an earlier recording that
        // incidentally covered it (mergeCoverage credits every trail a path
        // touches, not just the target) while never reaching its endpoints — so
        // it never actually completed or celebrated. Using the fraction here put
        // such a trail in the baseline, so the recording that FINALLY completed
        // it (endpoints reached) classified it "revisited" and the celebration
        // never fired. Real bug, build 247: woodlandtrail (Wild Basin) completed
        // with newlyCompleted=0. ProgressService is the authoritative,
        // endpoint-gated record of what has actually completed.
        let priorComplete = Array(ProgressService.shared.completedTrails(in: areaId).keys)
        activeRecording = ActiveRecording(
            areaId: areaId,
            mode: mode,
            trailId: mode == .trail ? trailId : nil,
            startedAt: Date(),
            path: [],
            distanceMi: 0,
            priorCompleteTrailIds: Set(priorComplete)
        )
        errorMessage = nil
        persist()
        log.notice("startRecording mode=\(mode.rawValue, privacy: .public) areaId=\(areaId, privacy: .public) trailId=\(trailId ?? "nil", privacy: .public) priorComplete=\(priorComplete.count)")
        ActivityLogService.shared.log(
            category: "recording",
            action: "start",
            context: [
                "areaId": areaId,
                "trailId": trailId ?? "nil",
                "mode": mode.rawValue,
            ]
        )
        AnalyticsService.shared.capture(.hikeStarted(areaId: areaId, mode: mode.rawValue))
        locationService.startBackgroundTracking()
        beginObservingLocation()
        // Lazy-prompt for notifications now that the user has actually
        // started a hike. The OS only asks once per install, so the
        // request is a no-op on subsequent calls.
        Task { await NotificationService.shared.ensurePermission() }
    }

    /// Discard the in-progress recording AND every saved hike. Called
    /// by Settings → Reset All Progress. Goes further than
    /// `discardRecording` by also deleting `hike-history.json` from
    /// Documents/ — without this delete, History would repopulate
    /// from disk on the next `loadHistory()` call and the rebuild-
    /// from-history path would re-credit all the trail completions
    /// we just wiped in ProgressService.
    func resetAll() {
        discardRecording()
        try? FileManager.default.removeItem(at: Self.historyFileURL)
        log.notice("resetAll: cleared activeRecording and removed hike-history.json")
    }

    /// Re-read the persisted active recording from UserDefaults.
    /// Called after Settings → Import — the import may have restored
    /// an in-progress recording from the backup, and without this
    /// call the banner wouldn't reappear until next launch. Saved
    /// hike history doesn't need an explicit reload because
    /// `loadHistory()` reads from disk every call (no in-memory
    /// cache to invalidate).
    func reload() {
        restoreActiveRecording()
    }

    #if DEBUG
    /// Set an active recording directly for App Store screenshot UI
    /// tests, WITHOUT starting background location tracking. The normal
    /// restore path calls `startBackgroundTracking()`, which triggers a
    /// location-permission system alert that freezes the UI test. This
    /// demo path is in-memory only (no persistence, no GPS) — the seeded
    /// path already carries the samples the recording panel renders.
    func injectDemoActiveRecording(_ recording: ActiveRecording) {
        activeRecording = recording
    }
    #endif

    func discardRecording() {
        let prev = activeRecording
        locationObserver?.cancel()
        locationObserver = nil
        locationService.stopBackgroundTracking()
        activeRecording = nil
        errorMessage = nil
        UserDefaults.standard.removeObject(forKey: persistKey)
        log.notice("discardRecording areaId=\(prev?.areaId ?? "nil", privacy: .public) duration=\(prev.map { Date().timeIntervalSince($0.startedAt) } ?? 0)s pathPoints=\(prev?.path.count ?? 0)")
        ActivityLogService.shared.log(
            category: "recording",
            action: "discard",
            context: [
                "areaId": prev?.areaId ?? "nil",
                "pathPoints": String(prev?.path.count ?? 0),
            ]
        )
        AnalyticsService.shared.capture(.hikeDiscarded(areaId: prev?.areaId ?? "unknown"))
    }

    /// Switch which trail the active recording is targeted at,
    /// without stopping the recording. Used by two flows:
    ///
    /// - The "Switch active trail" retarget banner that appears
    ///   when a user manually taps a different trail mid-hike on
    ///   a trail-mode recording.
    /// - The build-12 suggestion banner that proactively offers
    ///   a nearby incomplete trail. The suggestion banner can fire
    ///   on roam-mode recordings too, which is why this method
    ///   also handles the roam → trail conversion case.
    ///
    /// Coverage continues to accumulate against every trail the
    /// GPS path crosses — that's already how CoverageService
    /// works, and we don't touch it here. The retarget only
    /// changes the *classification* that stopRecording uses for
    /// the end-of-trail celebration and history-entry trailId.
    ///
    /// No-op when there's no active recording, or when the new
    /// id matches the current one (a same-id "retarget" would be
    /// pointless and the suggestion engine's filter rules already
    /// exclude that case).
    func retargetTrail(_ newTrailId: String) {
        guard let rec = activeRecording,
              let updated = Self.retargeted(rec, newTrailId: newTrailId)
        else {
            log.debug("retargetTrail no-op newTrailId=\(newTrailId, privacy: .public) current=\(self.activeRecording?.trailId ?? "nil", privacy: .public)")
            return
        }
        log.notice("retargetTrail oldMode=\(rec.mode.rawValue, privacy: .public) oldTrailId=\(rec.trailId ?? "nil", privacy: .public) newTrailId=\(newTrailId, privacy: .public)")
        activeRecording = updated
        persist()
    }

    /// Pure-function form of `retargetTrail` so tests can exercise
    /// the gating + struct-rebuild without instantiating the
    /// `@MainActor` singleton. Returns the rebuilt `ActiveRecording`
    /// when the retarget is valid, or `nil` to indicate "no-op."
    ///
    /// Roam → trail conversion: when called on a `.roam` recording,
    /// the result has `mode == .trail` and `trailId == newTrailId`.
    /// This is the path the build-12 suggestion banner uses —
    /// "you've been wandering, but you're 30 m from finishing
    /// Bajada, want to make this a Bajada recording?"
    nonisolated static func retargeted(_ rec: ActiveRecording, newTrailId: String) -> ActiveRecording? {
        // Walks can't be retargeted at a trail: the suggestion banner
        // and switch-trail flows are single-area concepts, and a
        // walk-to-trail conversion would silently shrink the walk's
        // multi-area credit scope at stop time.
        if rec.mode == .walk { return nil }
        // Same-id retarget on a trail-mode recording is the only
        // other no-op case. Roam-mode recordings never have a matching
        // trailId (it's nil), so they always fall through to the
        // rebuild branch.
        if rec.mode == .trail, rec.trailId == newTrailId { return nil }
        return ActiveRecording(
            areaId: rec.areaId,
            mode: .trail,
            trailId: newTrailId,
            startedAt: rec.startedAt,
            path: rec.path,
            distanceMi: rec.distanceMi,
            priorCompleteTrailIds: rec.priorCompleteTrailIds
        )
    }

    func stopRecording(trails: [Trail]) async -> FinishedRecording? {
        guard let rec = activeRecording else { return nil }
        locationObserver?.cancel()
        locationObserver = nil
        locationService.stopBackgroundTracking()

        let endedAt = Date()
        // Coverage is computed against the UNION of every prior hike's GPS
        // path in this area + the path that just finished. Per-hike-fraction
        // merging would lose progress when two hikes cover different halves
        // of the same trail (yesterday west half, today east half → each
        // hike reads 0.5, max(0.5, 0.5) = 0.5, never crosses the completion
        // threshold). With the union, the spatial grid in `measureCoverage`
        // sees every node that has EVER been visited and reports the true
        // cumulative fraction. We still compute the per-hike delta
        // separately for the post-stop "Trails with new partial coverage
        // from this hike" summary in `RecordingPanel`.
        let combinedPath = combinedPathForArea(rec.areaId, currentPath: rec.path)
        let sessionCoverage = measureCoverage(path: combinedPath, trails: trails, bufferMeters: bufferMeters)
        // `perHikeDelta` feeds the Stop & Save summary's "Made
        // Progress" list. Use length-based at 10m (same math as the
        // post-completion overlay since PR #125) so the percentage
        // shown for each trail matches the user's intuition of "how
        // much of this trail did I actually walk." The looser 30m
        // node-count via `measureCoverage` inflates the number with
        // drift / proximity credits (e.g. "53% of Beacon Hill" from
        // a hike that never touched it, just paralleled it within
        // 30m at a junction).
        let perHikeDelta = measureCoverageByLength(
            path: rec.path,
            trails: trails,
            bufferMeters: sinceCompletionBufferMeters
        )
        let (mergeNew, mergeRevisited, _) = await mergeCoverage(
            areaId: rec.areaId,
            sessionCoverage: sessionCoverage,
            trails: trails,
            combinedPath: combinedPath
        )
        // mergeCoverage's intra-session writes mean its returned
        // newly/revisited split is racy at stop time — a trail
        // completed mid-hike lands in `mergeRevisited` because by the
        // time the final mergeCoverage runs, prior coverage already
        // shows it complete. Reclassify against the snapshot taken at
        // startRecording so the FinishedRecording fields reflect
        // "did this recording bring the trail to completion?" not
        // "was the trail complete a moment ago?".
        let priorSnapshot = rec.priorCompleteTrailIds ?? []
        let allCompleted = Set(mergeNew + mergeRevisited)
        var newlyCompleted: [String] = []
        var revisited: [String] = []
        for tid in allCompleted {
            if priorSnapshot.contains(tid) {
                revisited.append(tid)
            } else {
                newlyCompleted.append(tid)
            }
        }

        // Symmetric-with-initial-completion revisit check. For each
        // trail the user has previously completed (per
        // ProgressService), look at the UNION of GPS paths since that
        // trail's last "full coverage" event — the later of the
        // initial completion date and any subsequent hike that
        // classified the trail as newly-completed or revisited. If
        // that union covers the trail to >= 0.95 with both endpoints,
        // the user has fully re-walked the trail since the last time
        // it was credited.
        //
        // Why this shape: the previous strict `sessionComplete` gate
        // on the all-hikes union missed two real cases —
        //
        // (a) Trails completed under build 8's pre-endpoint logic sit
        //     at fraction 1.0 in CoverageService without proof that
        //     endpoints were ever physically reached. The all-hikes
        //     union inherits that absence and the gate filters them.
        // (b) Multi-day revisits: half today + half tomorrow should
        //     credit on tomorrow's stop, mirroring how initial
        //     completions work after the union-of-paths fix in PR #84.
        //
        // Both fall out of "look at GPS samples since the anchor."
        let alreadyClassified = Set(newlyCompleted).union(revisited)
        let pendingRevisits = computeRevisits(
            areaId: rec.areaId,
            currentPath: rec.path,
            trails: trails,
            alreadyClassified: alreadyClassified
        )
        revisited.append(contentsOf: pendingRevisits)

        // --- Multi-area completion (trail + roam) ---
        // Credit neighbor areas whose trails this hike's GPS path crossed,
        // using the SAME per-area coverage merge as the primary area above.
        // mergeCoverage writes each neighbor's coverage/completions to the
        // live services immediately AND we record them in the dicts below so
        // a cold-launch history replay re-derives them. Only areas actually
        // covered end up credited; when none are crossed the dicts stay empty
        // and this saves as a plain single-area record, byte-identical to
        // before. Walk mode has its own multi-area path (stopWalk).
        var multiCompleted: [String: [String]] = [:]
        var multiRevisited: [String: [String]] = [:]
        if rec.mode != .walk {
            for (aid, ntrails) in await neighborAreasCrossed(by: rec.path, excluding: rec.areaId) {
                // "Was it complete BEFORE this hike?" — snapshot before the
                // per-area mergeCoverage mutates CoverageService.
                // Officially-completed neighbours before this hike — not bare
                // fraction (see the startRecording note for why).
                let priorComplete = Set(ProgressService.shared.completedTrails(in: aid).keys)
                let nCombined = combinedPathForArea(aid, currentPath: rec.path)
                let nSession = measureCoverage(path: nCombined, trails: ntrails, bufferMeters: bufferMeters)
                let (nMergeNew, nMergeRev, _) = await mergeCoverage(
                    areaId: aid, sessionCoverage: nSession, trails: ntrails, combinedPath: nCombined
                )
                var neighborNewly: [String] = []
                var neighborRevisited: [String] = []
                for tid in Set(nMergeNew + nMergeRev) {
                    if priorComplete.contains(tid) { neighborRevisited.append(tid) }
                    else { neighborNewly.append(tid) }
                }
                neighborRevisited.append(contentsOf: computeRevisits(
                    areaId: aid, currentPath: rec.path, trails: ntrails,
                    alreadyClassified: Set(neighborNewly).union(neighborRevisited)
                ))
                // Record the touch as a key even with no completions, so
                // this neighbor lands in touchedAreaIds and a cold-launch /
                // post-reset history replay re-derives its PARTIAL coverage
                // (the cross-park accumulation this whole feature enables).
                // neighborAreasCrossed already touch-gated, so every entry
                // here genuinely had trail coverage from this hike.
                multiCompleted[aid] = neighborNewly
                if !neighborRevisited.isEmpty { multiRevisited[aid] = neighborRevisited }
            }
        }
        let hasMultiArea = !multiCompleted.isEmpty || !multiRevisited.isEmpty
        if hasMultiArea {
            // Fold the primary area in so multi-area consumers read one
            // uniform dict (completedTrailIds(in:) reads multiAreaCompletions
            // when present, ignoring the flat arrays).
            multiCompleted[rec.areaId] = newlyCompleted
            multiRevisited[rec.areaId] = revisited
        }

        let finished = FinishedRecording(
            areaId: rec.areaId,
            mode: rec.mode,
            trailId: rec.trailId,
            startedAt: rec.startedAt,
            endedAt: endedAt,
            durationSeconds: Int(endedAt.timeIntervalSince(rec.startedAt)),
            path: rec.path,
            distanceMi: rec.distanceMi,
            newlyCompletedTrailIds: newlyCompleted,
            revisitedTrailIds: revisited,
            coverageDelta: perHikeDelta,
            multiAreaCompletions: hasMultiArea ? multiCompleted : nil,
            multiAreaRevisited: hasMultiArea ? multiRevisited : nil
        )

        saveToHistory(finished)
        log.notice("stopRecording areaId=\(rec.areaId, privacy: .public) trailId=\(rec.trailId ?? "nil", privacy: .public) duration=\(finished.durationSeconds)s distanceMi=\(rec.distanceMi) newlyCompleted=\(newlyCompleted.count) revisited=\(revisited.count)")
        ActivityLogService.shared.log(
            category: "recording",
            action: "stop",
            context: [
                "areaId": rec.areaId,
                "trailId": rec.trailId ?? "nil",
                "mode": rec.mode.rawValue,
                "distanceMi": String(format: "%.2f", rec.distanceMi),
                "durationSeconds": String(finished.durationSeconds),
                "newlyCompleted": String(newlyCompleted.count),
                "revisited": String(revisited.count),
            ]
        )
        AnalyticsService.shared.capture(.hikeSaved(
            areaId: rec.areaId,
            distanceMi: rec.distanceMi,
            durationSeconds: finished.durationSeconds,
            mode: rec.mode.rawValue))

        activeRecording = nil
        UserDefaults.standard.removeObject(forKey: persistKey)
        return finished
    }

    // MARK: - Walk mode (area-less, multi-area)

    /// Start an area-less WALK: records GPS anywhere and, at stop time,
    /// credits trail coverage/completions to every nearby area the path
    /// touched. `primaryAreaId` (the nearest area at start) is where the
    /// walk files in history; `nearbyAreaIds` fixes the credit scope now
    /// so stop-time classification is stable wherever the user roams.
    /// Prior-complete snapshots are taken per area, for the same
    /// mid-session-write race `startRecording`'s single-area snapshot
    /// exists to defuse.
    func startWalk(primaryAreaId: String, nearbyAreaIds: [String]) {
        var priorByArea: [String: Set<String>] = [:]
        for aid in nearbyAreaIds {
            // Officially-completed only (see the startRecording note) — not
            // bare fraction, which misclassifies a first real completion as a
            // revisit and swallows the celebration.
            let prior = Array(ProgressService.shared.completedTrails(in: aid).keys)
            priorByArea[aid] = Set(prior)
        }
        activeRecording = ActiveRecording(
            areaId: primaryAreaId,
            mode: .walk,
            trailId: nil,
            startedAt: Date(),
            path: [],
            distanceMi: 0,
            priorCompleteTrailIds: priorByArea[primaryAreaId] ?? [],
            nearbyAreaIds: nearbyAreaIds,
            priorCompleteByArea: priorByArea
        )
        errorMessage = nil
        persist()
        log.notice("startWalk primaryAreaId=\(primaryAreaId, privacy: .public) nearbyAreas=\(nearbyAreaIds.count)")
        ActivityLogService.shared.log(
            category: "recording",
            action: "start",
            context: [
                "areaId": primaryAreaId,
                "trailId": "nil",
                "mode": RecordingMode.walk.rawValue,
                "nearbyAreas": String(nearbyAreaIds.count),
            ]
        )
        AnalyticsService.shared.capture(.hikeStarted(areaId: primaryAreaId, mode: RecordingMode.walk.rawValue))
        locationService.startBackgroundTracking()
        beginObservingLocation()
        Task { await NotificationService.shared.ensurePermission() }
    }

    /// Stop & save a WALK: the same measure → merge → classify → revisit
    /// sequence as `stopRecording`, run once per nearby area, saving ONE
    /// history record under the primary area with the per-area credits
    /// in `multiAreaCompletions` / `multiAreaRevisited` (primary
    /// included; the flat arrays mirror the primary's for legacy
    /// consumers).
    func stopWalk(trailsByArea: [String: [Trail]]) async -> FinishedRecording? {
        guard let rec = activeRecording, rec.mode == .walk else { return nil }
        locationObserver?.cancel()
        locationObserver = nil
        locationService.stopBackgroundTracking()
        let endedAt = Date()

        // Decode history once and share it across the per-area passes —
        // combinedPathForArea/computeRevisits would otherwise re-decode
        // the whole file roughly twice per area.
        let history = loadHistorySync()
        let priorByArea = rec.priorCompleteByArea ?? [:]
        var multiCompleted: [String: [String]] = [:]
        var multiRevisited: [String: [String]] = [:]
        var primaryDelta: [String: Double] = [:]

        for (areaId, trails) in trailsByArea {
            guard !trails.isEmpty else { continue }
            let combinedPath = combinedPathForArea(areaId, currentPath: rec.path, history: history)
            let sessionCoverage = measureCoverage(path: combinedPath, trails: trails, bufferMeters: bufferMeters)
            let (mergeNew, mergeRevisited, _) = await mergeCoverage(
                areaId: areaId,
                sessionCoverage: sessionCoverage,
                trails: trails,
                combinedPath: combinedPath
            )
            // Snapshot reclassification, per area — same rationale as
            // stopRecording's (mergeCoverage's split is racy vs its own
            // intra-session writes).
            let priorSnapshot = priorByArea[areaId] ?? []
            var newly: [String] = []
            var revisited: [String] = []
            for tid in Set(mergeNew + mergeRevisited) {
                if priorSnapshot.contains(tid) {
                    revisited.append(tid)
                } else {
                    newly.append(tid)
                }
            }
            let pending = computeRevisits(
                areaId: areaId,
                currentPath: rec.path,
                trails: trails,
                alreadyClassified: Set(newly).union(revisited),
                fullHistory: history
            )
            revisited.append(contentsOf: pending)
            if !newly.isEmpty { multiCompleted[areaId] = newly }
            if !revisited.isEmpty { multiRevisited[areaId] = revisited }
            if areaId == rec.areaId {
                primaryDelta = measureCoverageByLength(
                    path: rec.path,
                    trails: trails,
                    bufferMeters: sinceCompletionBufferMeters
                )
            }
        }
        // The primary key must exist even when empty — a non-nil
        // multiAreaCompletions is what marks the saved record as a walk.
        if multiCompleted[rec.areaId] == nil { multiCompleted[rec.areaId] = [] }

        let finished = FinishedRecording(
            areaId: rec.areaId,
            mode: .walk,
            trailId: nil,
            startedAt: rec.startedAt,
            endedAt: endedAt,
            durationSeconds: Int(endedAt.timeIntervalSince(rec.startedAt)),
            path: rec.path,
            distanceMi: rec.distanceMi,
            newlyCompletedTrailIds: multiCompleted[rec.areaId] ?? [],
            revisitedTrailIds: multiRevisited[rec.areaId] ?? [],
            coverageDelta: primaryDelta,
            multiAreaCompletions: multiCompleted,
            multiAreaRevisited: multiRevisited
        )

        saveToHistory(finished)
        let totalNew = multiCompleted.values.map(\.count).reduce(0, +)
        let totalRev = multiRevisited.values.map(\.count).reduce(0, +)
        log.notice("stopWalk primaryAreaId=\(rec.areaId, privacy: .public) areas=\(trailsByArea.count) duration=\(finished.durationSeconds)s distanceMi=\(rec.distanceMi) newlyCompleted=\(totalNew) revisited=\(totalRev)")
        ActivityLogService.shared.log(
            category: "recording",
            action: "stop",
            context: [
                "areaId": rec.areaId,
                "mode": RecordingMode.walk.rawValue,
                "areas": String(trailsByArea.count),
                "distanceMi": String(format: "%.2f", rec.distanceMi),
                "durationSeconds": String(finished.durationSeconds),
                "newlyCompleted": String(totalNew),
                "revisited": String(totalRev),
            ]
        )
        AnalyticsService.shared.capture(.hikeSaved(
            areaId: rec.areaId,
            distanceMi: rec.distanceMi,
            durationSeconds: finished.durationSeconds,
            mode: RecordingMode.walk.rawValue))

        activeRecording = nil
        UserDefaults.standard.removeObject(forKey: persistKey)
        return finished
    }

    /// Recompute coverage from the in-progress path and merge any deltas into
    /// CoverageService / ProgressService. Trails crossing the completion
    /// threshold get marked complete (with the standard haptic) live, mid-hike.
    /// Safe to call repeatedly — `mergeCoverage` is monotonic, so a no-op walk
    /// produces no new completions.
    func applyLiveCoverage(trails: [Trail]) async {
        guard let rec = activeRecording else { return }
        // Walk mode defers ALL coverage/completion work to stopWalk.
        // The live tick is fed one area's trails by whichever AreaView
        // is open, but merges under rec.areaId — for a multi-area walk
        // that mismatch could write one area's coverage under another's
        // key. One-shot at stop is correct and cheap.
        guard rec.mode != .walk else { return }
        // Same union-of-paths reasoning as `stopRecording` — combine the
        // in-progress recording's path with every prior hike's path in the
        // area so live completions fire even when this session is
        // completing the half of a trail that was already partly walked
        // on a previous day.
        let combinedPath = combinedPathForArea(rec.areaId, currentPath: rec.path)
        let sessionCoverage = measureCoverage(path: combinedPath, trails: trails, bufferMeters: bufferMeters)
        _ = await mergeCoverage(
            areaId: rec.areaId,
            sessionCoverage: sessionCoverage,
            trails: trails,
            combinedPath: combinedPath
        )
    }

    /// Smoothed pace in meters per second from the active
    /// recording's recent path samples. `nil` when there isn't
    /// enough data — caller (`TrailETA`, recording-panel ETA pill)
    /// should render the absence as "—" rather than 0.
    ///
    /// Walks the path's tail backwards collecting samples whose
    /// timestamp is within `windowSeconds` of the latest sample,
    /// sums the haversine distance between adjacent samples, and
    /// divides by the elapsed time across that span. Naturally
    /// adapts to whatever GPS rate the device is producing (1-2 Hz
    /// while moving, less when stationary) without us having to
    /// resample to a fixed cadence.
    ///
    /// `bufferMeters / 2` minimum sample count to defuse the early-
    /// recording case where 2-3 GPS points produce a wildly noisy
    /// pace. ~5 samples spanning >30 s is a sane floor for a
    /// hiker (which is what this app is for) — a runner would want
    /// a longer window.
    func smoothedPaceMetersPerSec(windowSeconds: TimeInterval = 60) -> Double? {
        guard let path = activeRecording?.path else { return nil }
        return Self.paceMetersPerSec(path: path, windowSeconds: windowSeconds)
    }

    /// Pure pace computation, extracted so it's unit-testable without the
    /// @Observable service + a live recording.
    ///
    /// CRITICAL: path timestamps (`point[2]`) are epoch **milliseconds**
    /// (see `appendPoint`). This function was previously inline and read
    /// them as seconds — so the 60 s window was really 60 ms and, with
    /// GPS samples ~2 s apart, it never caught a second sample in-window
    /// and returned nil every time (pace + ETA + suggestion timing all
    /// silently dead). All time math below converts ms → s explicitly.
    nonisolated static func paceMetersPerSec(path: [GpsPoint],
                                             windowSeconds: TimeInterval) -> Double? {
        guard path.count >= 5, let lastTs = path.last?[2] else { return nil }
        let cutoffMs = lastTs - windowSeconds * 1000
        // Collect tail samples within the time window (timestamps in ms).
        var tail: [GpsPoint] = []
        for p in path.reversed() {
            guard p.count >= 3 else { continue }
            if p[2] < cutoffMs { break }
            tail.append(p)
        }
        let recent = Array(tail.reversed())
        guard recent.count >= 2 else { return nil }
        let elapsed = (recent.last![2] - recent.first![2]) / 1000   // ms → s
        guard elapsed >= 30 else { return nil }
        var meters = 0.0
        for i in 1..<recent.count {
            meters += MapMath.haversineMeters(
                lat1: recent[i - 1][0], lon1: recent[i - 1][1],
                lat2: recent[i][0],     lon2: recent[i][1]
            )
        }
        let pace = meters / elapsed
        // Filter out near-zero "user is standing still" pace —
        // dividing trail distance by it would produce huge ETAs
        // that just confuse the user. Threshold of 0.3 m/s ≈ 1
        // km/h, well below any sustained walking pace.
        guard pace >= 0.3 else { return nil }
        return pace
    }

    /// Replay every saved hike's GPS path against the area's *current* trails
    /// and merge the resulting coverage. Idempotent and self-healing: if an
    /// upstream re-fetch ever assigns new IDs to the same trails (e.g. after
    /// the trail-id determinism fix), the next AreaView open recomputes
    /// completions against the new IDs from history alone — no manual
    /// re-toggle needed. Suppresses the "newly complete" haptic by going
    /// straight through CoverageService + ProgressService.bulkMarkComplete.
    func rebuildCoverageFromHistory(areaId: String, trails: [Trail]) async {
        // touchedAreaIds: walks saved under another primary area still
        // contribute their GPS paths + credits to THIS area's rebuild.
        // Walks enter as in-memory PROJECTIONS — flat per-area views via
        // the walk-aware accessors — so every classification/anchor/
        // retro pass below reads the correct per-area credits without
        // knowing about walks. Projections are never persisted: the
        // patch loop at the bottom skips walk records entirely (their
        // stop-time classification is already correct, and rewriting
        // them from a projection would corrupt the multi-area dicts).
        // areaAndTwins: hikes recorded under a now-hidden duplicate twin
        // (docs/adr/0002) rebuild THIS canonical area's coverage too — their
        // GPS paths snap onto the identical trail geometry.
        let areas = AreaDataService.shared.areaAndTwins(areaId)
        let areaHistory = loadHistorySync()
            .filter { !$0.touchedAreaIds.isDisjoint(with: areas) }
            .map { hike -> SavedRecording in
                guard hike.isWalk else { return hike }
                return SavedRecording(
                    id: hike.id,
                    areaId: areaId,
                    startedAt: hike.startedAt,
                    endedAt: hike.endedAt,
                    distanceMi: hike.distanceMi,
                    durationSeconds: hike.durationSeconds,
                    completedTrailIds: hike.completedTrailIds(in: areaId),
                    path: hike.path,
                    trailId: nil,
                    revisitedTrailIds: hike.revisitedTrailIds(in: areaId)
                )
            }
            .sorted { $0.startedAt < $1.startedAt }   // oldest first, for the credit pass below
        guard !areaHistory.isEmpty, !trails.isEmpty else { return }

        // Combine every hike's GPS path into one big path and measure
        // coverage once. This is the union-of-visits computation —
        // any trail node within bufferMeters of ANY historical GPS
        // sample counts. Replaces the previous per-hike-then-max
        // approach which lost progress when two hikes covered
        // disjoint halves of the same trail (each hike read ~0.5,
        // max(0.5, 0.5) = 0.5, never crossing the completion gate).
        var combined: [GpsPoint] = []
        for hike in areaHistory { combined.append(contentsOf: hike.path) }
        let cov = measureCoverage(path: combined, trails: trails, bufferMeters: bufferMeters)
        guard !cov.isEmpty else { return }

        var aggregate: [String: Double] = [:]
        var endpointsHit: [String: Bool] = [:]   // diagnostics only
        var completesByTid: [String: Bool] = [:]
        for (tid, score) in cov {
            aggregate[tid] = score.fraction
            if score.endpointsVisited { endpointsHit[tid] = true }
            if score.completesTrail { completesByTid[tid] = true }
        }

        await CoverageService.shared.mergeCoverage(areaId: areaId, delta: aggregate)

        // mergeCoverage above max-merges `aggregate` into BOTH the
        // lifetime `state` AND `sinceCompletion` buckets, which
        // would clobber the sinceCompletion-for-completed-trails
        // semantic — a trail completed yesterday would re-inherit
        // its lifetime ~0.94 coverage and appear "6% remaining"
        // even when the user has walked nothing since the
        // completion event.
        //
        // Recompute sinceCompletion per-trail using LENGTH-based
        // coverage at the tight 10m buffer (`measureCoverageByLength`)
        // so the bar's "% remaining" literally describes the length
        // of orange drawn by the post-completion overlay — same
        // run-of-2+-covered-nodes rule, summed as polyline distance.
        // The node-count `aggregate` above still drives the
        // completion gate (looser 30m so completion stays
        // reachable through GPS scatter); this pass is display-only.
        //
        // Two cases:
        //
        //   • Never completed → measure the area's entire history
        //     against the trail at 10m. Replaces the previous
        //     "lifetime IS since-completion at 30m node-count"
        //     pass, which over-credited dense junctions (Pima
        //     Canyon Loop showed 25% from incidental crossings of
        //     parallel trails at the trailhead).
        //
        //   • Completed → measure only hikes with
        //     `startedAt > completionDate`. `startedAt` (not
        //     `endedAt`) so the completing hike — which has
        //     startedAt before and endedAt after the completion
        //     stamp — is excluded.
        let lifetimeLengthCov = measureCoverageByLength(
            path: combined,
            trails: trails,
            bufferMeters: sinceCompletionBufferMeters
        )
        var sinceCompletionAuthoritative: [String: Double] = [:]
        for trail in trails {
            let tid = trail.id
            let completionDate = ProgressService.shared.completionDate(areaId: areaId, trailId: tid)
            guard let completionDate else {
                let value = lifetimeLengthCov[tid] ?? 0
                sinceCompletionAuthoritative[tid] = value
                log.notice("sinceCompletionComputed tid=\(tid, privacy: .public) completionDate=nil postCompletionHikes=\(areaHistory.count) value=\(value)")
                continue
            }
            let postCompletionHikes = areaHistory.filter { $0.startedAt > completionDate }
            if postCompletionHikes.isEmpty {
                sinceCompletionAuthoritative[tid] = 0
                log.notice("sinceCompletionComputed tid=\(tid, privacy: .public) completionDate=\(completionDate.timeIntervalSince1970) postCompletionHikes=0 value=0")
                continue
            }
            let postPath = postCompletionHikes.flatMap(\.path)
            let perTrailCov = measureCoverageByLength(
                path: postPath,
                trails: [trail],
                bufferMeters: sinceCompletionBufferMeters
            )
            let value = perTrailCov[tid] ?? 0
            sinceCompletionAuthoritative[tid] = value
            log.notice("sinceCompletionComputed tid=\(tid, privacy: .public) completionDate=\(completionDate.timeIntervalSince1970) postCompletionHikes=\(postCompletionHikes.count) value=\(value)")
        }
        await CoverageService.shared.setSinceCompletion(
            areaId: areaId,
            values: sinceCompletionAuthoritative
        )

        let nowComplete = aggregate.compactMap { (tid, v) in
            v >= completeThreshold && (completesByTid[tid] ?? false) ? tid : nil
        }
        // Diagnostic log for trails this rebuild is marking complete
        // that weren't already in ProgressService. Captures the same
        // shape as the live `trailComplete` log so we can tell
        // "trail X was credited by the union of every hike in this
        // area, with fraction F and endpoints at distances D1/D2"
        // — separates legitimate multi-day completions from dense-
        // network over-crediting in the diag bundle. Only fires for
        // trails newly added by this rebuild to avoid spamming the
        // log every cold launch with already-complete trails.
        let priorComplete = Set(ProgressService.shared.completedTrails(in: areaId).keys)
        ProgressService.shared.bulkMarkComplete(areaId: areaId, trailIds: Set(nowComplete))
        for tid in nowComplete where !priorComplete.contains(tid) {
            let frac = aggregate[tid] ?? 0
            let (startDist, endDist) = trailEndpointDistances(trailId: tid, trails: trails, path: combined)
            log.notice("trailRetroComplete tid=\(tid, privacy: .public) fraction=\(frac) startDist=\(startDist)m endDist=\(endDist)m unionPathPoints=\(combined.count)")
        }

        // Retro-credit the historical hike that tipped each
        // multi-hike completion. Without this, the History tab's
        // "newly completed" badge stays at zero for the hike that
        // actually closed the loop — the trail shows complete on the
        // map but no past hike claims responsibility.
        //
        // Only attempt the newly-completed credit pass for trails
        // that aren't already in some hike's `completedTrailIds`.
        // Common case: single-hike completion stored correctly at
        // stopRecording time, or PR #85's tipping-hike pass already
        // ran and credited everything that could be.
        let alreadyCredited = Set(areaHistory.flatMap { $0.completedTrailIds })
        var pending = Set(nowComplete).subtracting(alreadyCredited)

        var creditedByHikeId: [String: [String]] = [:]
        if !pending.isEmpty {
            var running: [GpsPoint] = []
            for hike in areaHistory {
                if pending.isEmpty { break }
                running.append(contentsOf: hike.path)
                let snapshot = measureCoverage(path: running, trails: trails, bufferMeters: bufferMeters)
                var toCredit: [String] = []
                for tid in pending {
                    if let s = snapshot[tid], s.completesTrail {
                        toCredit.append(tid)
                    }
                }
                if toCredit.isEmpty { continue }
                creditedByHikeId[hike.id, default: []].append(contentsOf: toCredit)
                for t in toCredit { pending.remove(t) }
            }
        }

        // Retro-credit revisits: walk history chronologically per
        // already-completed trail, maintaining an anchor + post-
        // anchor union path. Every time the post-anchor union
        // crosses the completion gate, that hike fired a revisit
        // event. If it isn't already in the hike's
        // `revisitedTrailIds` or `completedTrailIds`, schedule a
        // patch. Same semantics as the live revisit check at
        // stopRecording time — this pass closes the gap for
        // historical hikes that pre-date the symmetric-revisit
        // logic landing.
        let completionDates = ProgressService.shared.completedTrails(in: areaId)
        var revisitCreditByHikeId: [String: Set<String>] = [:]
        let iso = ISO8601DateFormatter()
        var retroSkippedNoAnchor = 0
        var retroConsidered = 0
        for (tid, progressStamp) in completionDates {
            retroConsidered += 1
            // Anchor: the earliest hike-credit event for this trail
            // (initial completion claimed by a saved hike), with a
            // fallback to the ProgressService completion stamp when
            // no hike has it credited. The fallback covers two real
            // cases that the previous code silently skipped:
            //   (a) The trail was completed under pre-build-13 logic
            //       which didn't enforce the endpoint gate, so no
            //       hike's stop credited it in `completedTrailIds`
            //       even though it sits at fraction >= 0.95 in
            //       CoverageService.
            //   (b) The trail was completed via manual toggle and
            //       no hike has it credited.
            // In both cases, walk forward from the ProgressService
            // stamp and look for the first hike whose post-stamp
            // union path covers the trail to 0.95 + endpoints.
            var initialAnchor: Date? = nil
            for hike in areaHistory {
                if hike.completedTrailIds.contains(tid) {
                    if initialAnchor == nil || hike.endedAt < initialAnchor! {
                        initialAnchor = hike.endedAt
                    }
                }
            }
            if initialAnchor == nil {
                initialAnchor = iso.date(from: progressStamp)
            }
            guard var anchor = initialAnchor else {
                retroSkippedNoAnchor += 1
                continue
            }

            var postAnchor: [GpsPoint] = []
            for hike in areaHistory where hike.endedAt > anchor {
                postAnchor.append(contentsOf: hike.path)
                guard let s = measureCoverage(
                    path: postAnchor,
                    trails: trails,
                    bufferMeters: bufferMeters
                )[tid], s.completesTrail else {
                    continue
                }
                let alreadyClaimed = hike.completedTrailIds.contains(tid)
                    || hike.revisitedTrailIds.contains(tid)
                if !alreadyClaimed {
                    revisitCreditByHikeId[hike.id, default: []].insert(tid)
                }
                anchor = hike.endedAt
                postAnchor = []
            }
        }

        // Diagnostic summary of the retro-credit pass so the next
        // diag bundle reveals what the rebuild actually did. Without
        // this, debugging "why didn't my history heal" is opaque.
        log.notice("rebuildCoverageFromHistory area=\(areaId, privacy: .public) hikes=\(areaHistory.count) completedTrails=\(retroConsidered) newRevisitCredits=\(revisitCreditByHikeId.values.map(\.count).reduce(0, +)) newCompletionCredits=\(creditedByHikeId.values.map(\.count).reduce(0, +)) skippedNoAnchor=\(retroSkippedNoAnchor)")

        // Per-trail snapshot so a "previously completed going crazy"
        // bundle reveals, for each currently-complete trail, whether
        // the all-time union math thinks the trail is genuinely
        // covered + which hike (or fallback) provides the revisit
        // anchor. Lets a future investigation answer "did this trail
        // ever actually get covered, or is the gate firing on
        // coincidence?" without re-hiking. Bounded by the area's
        // completed-trail count (typically < 50).
        //
        // Two anchors logged per trail:
        // - `earliestAnchor` — first hike whose stop credited the
        //   trail as completed. Used by the retro-credit forward-walk
        //   pass above.
        // - `latestAnchor` — most recent hike that claims the trail
        //   in EITHER completedTrailIds OR revisitedTrailIds. Used by
        //   the live `computeRevisits` gate at every stopRecording.
        //   This is the one that determines whether *today's* hike
        //   re-fires a revisit, so it's the more diagnostic of the
        //   two when a user reports "I didn't even walk that today."
        //
        // Also logs the all-time union fraction at three buffer
        // widths (30m default + 15m + 10m). If a trail reads 1.0 at
        // 30m but drops sharply at 15m/10m, the gate is firing
        // because the user's GPS path runs *parallel to* the trail
        // 15-30m away (dense network coincidence), not on it. If
        // fraction stays high at 10m, the GPS path is genuinely on
        // top of the trail's polyline — the user walked it (perhaps
        // not knowing the trail's name) or two OSM ways share
        // geometry.
        let cov15 = measureCoverage(path: combined, trails: trails, bufferMeters: 15.0)
        let cov10 = measureCoverage(path: combined, trails: trails, bufferMeters: 10.0)
        // Coverage measured against the LATEST hike's path alone (not
        // the all-time union). Lets us tell whether the most recent
        // hike's GPS path *by itself* covered each trail — vs the
        // union math crediting it via cumulative post-anchor history.
        // If `latestFrac10` for a trail is high → the latest hike
        // physically traversed the trail. If low → the latest hike
        // didn't, but cumulative history did, which is how older-
        // anchor / multi-hike-cumulative revisit credits surface.
        // `mostRecentHike` = chronologically newest hike in this area
        // (by startedAt), which differs from `latestAnchor` below: the
        // anchor only counts hikes that *claim* the trail, while this
        // is just "the last hike you logged here." For the user's
        // open question — "did today's hike's path alone cover that
        // trail?" — this is the path we want.
        let mostRecentHike = areaHistory.max(by: { $0.startedAt < $1.startedAt })
        let mostRecentHikePath = mostRecentHike?.path ?? []
        let mostRecentHikeId = mostRecentHike?.id ?? "none"
        let latestCov30 = measureCoverage(path: mostRecentHikePath, trails: trails, bufferMeters: 30.0)
        let latestCov15 = measureCoverage(path: mostRecentHikePath, trails: trails, bufferMeters: 15.0)
        let latestCov10 = measureCoverage(path: mostRecentHikePath, trails: trails, bufferMeters: 10.0)
        // Per-hike 10m coverage precomputed once per past hike so the
        // trailHikeContribution log below is O(hikes × completedTrails)
        // lookups, not O(hikes × completedTrails) measureCoverage calls.
        let sortedHikes = areaHistory.sorted { $0.startedAt < $1.startedAt }
        let perHikeCov10: [(SavedRecording, [String: CoverageScore])] = sortedHikes.map {
            ($0, measureCoverage(path: $0.path, trails: trails, bufferMeters: 10.0))
        }
        for (tid, progressStamp) in completionDates {
            let score = cov[tid]
            let frac30 = score?.fraction ?? -1
            let frac15 = cov15[tid]?.fraction ?? -1
            let frac10 = cov10[tid]?.fraction ?? -1
            let latestFrac30 = latestCov30[tid]?.fraction ?? -1
            let latestFrac15 = latestCov15[tid]?.fraction ?? -1
            let latestFrac10 = latestCov10[tid]?.fraction ?? -1
            let endpointsHit = score?.endpointsVisited ?? false
            let (startDist, endDist) = trailEndpointDistances(trailId: tid, trails: trails, path: combined)

            // Earliest anchor — first hike to credit the trail
            // complete. Matches the retro-credit pass's anchor
            // selection.
            var earliestHikeId = "none"
            var earliestAt: TimeInterval = -1
            for hike in areaHistory where hike.completedTrailIds.contains(tid) {
                if earliestAt < 0 || hike.endedAt.timeIntervalSince1970 < earliestAt {
                    earliestHikeId = hike.id
                    earliestAt = hike.endedAt.timeIntervalSince1970
                }
            }
            let earliestSource: String
            if earliestAt < 0 {
                earliestSource = "progressFallback"
                earliestAt = iso.date(from: progressStamp)?.timeIntervalSince1970 ?? -1
            } else {
                earliestSource = "hike"
            }

            // Latest anchor — same logic as `latestCompletionAnchor`
            // (the static helper used by computeRevisits). Picks the
            // MOST RECENT hike whose completedTrailIds OR
            // revisitedTrailIds contains the trail.
            var latestHikeId = "none"
            var latestAt: TimeInterval = -1
            for hike in areaHistory where hike.completedTrailIds.contains(tid)
                || hike.revisitedTrailIds.contains(tid)
            {
                if latestAt < 0 || hike.endedAt.timeIntervalSince1970 > latestAt {
                    latestHikeId = hike.id
                    latestAt = hike.endedAt.timeIntervalSince1970
                }
            }
            let latestSource: String
            if latestAt < 0 {
                latestSource = "progressFallback"
                latestAt = iso.date(from: progressStamp)?.timeIntervalSince1970 ?? -1
            } else {
                latestSource = "hike"
            }

            log.notice("trailCompletionState tid=\(tid, privacy: .public) frac30=\(frac30) frac15=\(frac15) frac10=\(frac10) latestHikeFrac30=\(latestFrac30) latestHikeFrac15=\(latestFrac15) latestHikeFrac10=\(latestFrac10) startDist=\(startDist)m endDist=\(endDist)m endpointsHit=\(endpointsHit) mostRecentHikeId=\(mostRecentHikeId, privacy: .public) earliestAnchor=\(earliestSource, privacy: .public)/\(earliestHikeId, privacy: .public)@\(earliestAt) latestAnchor=\(latestSource, privacy: .public)/\(latestHikeId, privacy: .public)@\(latestAt)")

            // Per-hike contribution at the 10m buffer. Lets us tell
            // whether the all-time union coverage came from one or
            // two hikes that genuinely walked the trail, or from
            // many hikes each accidentally contributing a few percent
            // via parallel walking / OSM polyline overlap. One log
            // line per (trail, hike) pair, ordered chronologically.
            for (hike, hikeCov) in perHikeCov10 {
                let perHikeFrac = hikeCov[tid]?.fraction ?? -1
                log.notice("trailHikeContribution tid=\(tid, privacy: .public) hikeId=\(hike.id, privacy: .public) startedAt=\(hike.startedAt.timeIntervalSince1970) frac10=\(perHikeFrac)")
            }
        }

        // Retro-suppress wrongly-credited revisits. Walks history
        // chronologically and applies the same tipping check used by
        // the live computeRevisits gate (PR #104): for each hike's
        // revisitedTrailIds, suppress trails whose cumulative
        // post-anchor union WITHOUT this hike was already past the
        // gate. Cleans up false revisit credits accumulated under
        // pre-tipping logic — the "today's hike got credit for trails
        // I didn't walk today" symptom. Forward propagation: each
        // hike's anchor is computed against the cleaned simulated
        // history, so suppressing hike B's wrong credit correctly
        // moves the anchor back for hike C's evaluation. Idempotent.
        var suppressByHikeId: [String: Set<String>] = [:]
        var simulated: [SavedRecording] = []
        let trailsById = Dictionary(uniqueKeysWithValues: trails.map { ($0.id, $0) })
        for hike in areaHistory.sorted(by: { $0.startedAt < $1.startedAt }) {
            // Fold in any credits this rebuild just added so the
            // simulated history reflects the post-rebuild state, not
            // the pre-rebuild one.
            var hikeCompleted = Set(hike.completedTrailIds)
            if let extras = creditedByHikeId[hike.id] { hikeCompleted.formUnion(extras) }
            var hikeRevisited = Set(hike.revisitedTrailIds)
            if let extras = revisitCreditByHikeId[hike.id] { hikeRevisited.formUnion(extras) }

            var keptRevisited: Set<String> = []
            var suppressedHere: Set<String> = []
            for tid in hikeRevisited {
                guard let trail = trailsById[tid] else {
                    keptRevisited.insert(tid)
                    continue
                }
                var anchorDate: Date = .distantPast
                for prior in simulated where prior.completedTrailIds.contains(tid)
                    || prior.revisitedTrailIds.contains(tid)
                {
                    if prior.endedAt > anchorDate { anchorDate = prior.endedAt }
                }
                var postAnchorWithoutH: [GpsPoint] = []
                for prior in simulated where prior.startedAt > anchorDate {
                    postAnchorWithoutH.append(contentsOf: prior.path)
                }
                let withoutScore = measureCoverage(
                    path: postAnchorWithoutH,
                    trails: [trail],
                    bufferMeters: bufferMeters
                )[tid]
                let alreadyEligible: Bool = withoutScore.map { $0.completesTrail } ?? false
                // Symmetric check: does post-anchor WITH this hike's
                // path actually cross the gate? If not, this hike
                // didn't earn the credit under tipping semantics —
                // the only reason it has the credit at all is because
                // yesterday's pre-PR-#104 stopRecording used a looser
                // anchor (anchor walked all the way back to an
                // earlier hike that hadn't itself credited the trail,
                // making post-anchor huge). Cleanup needs to apply
                // the FULL tipping check, not just "was it already
                // eligible." Otherwise hikes whose contribution
                // doesn't push cumulative past the gate keep wrong
                // credits.
                var postAnchorWithH = postAnchorWithoutH
                postAnchorWithH.append(contentsOf: hike.path)
                let withScore = measureCoverage(
                    path: postAnchorWithH,
                    trails: [trail],
                    bufferMeters: bufferMeters
                )[tid]
                let crossesWithH: Bool = withScore.map { $0.completesTrail } ?? false
                if alreadyEligible || !crossesWithH {
                    suppressedHere.insert(tid)
                    let reason = alreadyEligible ? "alreadyTippedBeforeHike" : "doesNotTipWithHike"
                    let withoutFrac = withoutScore?.fraction ?? -1
                    let withFrac = withScore?.fraction ?? -1
                    log.notice("revisitSuppressed hikeId=\(hike.id, privacy: .public) tid=\(tid, privacy: .public) reason=\(reason, privacy: .public) fracWithoutHike=\(withoutFrac) fracWithHike=\(withFrac)")
                } else {
                    keptRevisited.insert(tid)
                }
            }
            if !suppressedHere.isEmpty {
                suppressByHikeId[hike.id] = suppressedHere
            }
            simulated.append(SavedRecording(
                id: hike.id,
                areaId: hike.areaId,
                startedAt: hike.startedAt,
                endedAt: hike.endedAt,
                distanceMi: hike.distanceMi,
                durationSeconds: hike.durationSeconds,
                completedTrailIds: Array(hikeCompleted),
                path: hike.path,
                trailId: hike.trailId,
                revisitedTrailIds: Array(keptRevisited)
            ))
        }
        let totalSuppressed = suppressByHikeId.values.map(\.count).reduce(0, +)
        if totalSuppressed > 0 {
            log.notice("revisitCleanup area=\(areaId, privacy: .public) suppressed=\(totalSuppressed) hikesAffected=\(suppressByHikeId.count)")
        }

        // Also run when any of this area's records has an overlap between its
        // completed and revisited arrays — a trail can't be both, and such a
        // record needs healing even if this pass produced no new credits.
        let anyOverlap = areaHistory.contains {
            !Set($0.revisitedTrailIds).isDisjoint(with: $0.completedTrailIds)
        }
        if creditedByHikeId.isEmpty, revisitCreditByHikeId.isEmpty,
           suppressByHikeId.isEmpty, !anyOverlap { return }

        // Persist: rewrite the full history file with the credited
        // entries patched in. Re-load to avoid clobbering hikes from
        // other areas that may have been written between our initial
        // load and now.
        var allHistory = loadHistorySync()
        for i in allHistory.indices {
            // Never rewrite WALK records here: the credits/suppressions
            // were computed against this area's projection of the walk,
            // and merging them into the walk's flat arrays (which are
            // primary-area-only) or reconstructing it field-by-field
            // would corrupt the multi-area dicts. Walks' stop-time
            // classification is already produced by the correct logic;
            // retro passes are for healing legacy single-area records.
            if allHistory[i].isWalk { continue }
            let newCompletions = creditedByHikeId[allHistory[i].id]
            let newRevisits = revisitCreditByHikeId[allHistory[i].id]
            let suppressedRevisits = suppressByHikeId[allHistory[i].id]
            let hadOverlap = !Set(allHistory[i].revisitedTrailIds)
                .isDisjoint(with: allHistory[i].completedTrailIds)
            if newCompletions == nil, newRevisits == nil, suppressedRevisits == nil,
               !hadOverlap { continue }
            let mergedCompleted = newCompletions.map { extras in
                Array(Set(allHistory[i].completedTrailIds).union(extras))
            } ?? allHistory[i].completedTrailIds
            var mergedRevisitedSet = Set(allHistory[i].revisitedTrailIds)
            if let extras = newRevisits { mergedRevisitedSet.formUnion(extras) }
            if let suppress = suppressedRevisits { mergedRevisitedSet.subtract(suppress) }
            // Invariant: a trail this hike completed is never also "previously
            // completed" here (see SavedRecording.displayRevisitedTrailIds).
            // The stop-time split is disjoint, but a rebuild that credits a
            // first-time completion above without clearing the stop-time revisit
            // tag would otherwise leave the two arrays overlapping.
            mergedRevisitedSet.subtract(mergedCompleted)
            let mergedRevisited = Array(mergedRevisitedSet)
            let old = allHistory[i]
            allHistory[i] = SavedRecording(
                id: old.id,
                areaId: old.areaId,
                startedAt: old.startedAt,
                endedAt: old.endedAt,
                distanceMi: old.distanceMi,
                durationSeconds: old.durationSeconds,
                completedTrailIds: mergedCompleted,
                path: old.path,
                trailId: old.trailId,
                revisitedTrailIds: mergedRevisited
            )
        }
        if let data = try? JSONEncoder().encode(allHistory) {
            try? data.write(to: Self.historyFileURL)
        }
    }

    /// Merge a per-trail coverage map (this hike's view of coverage) into the
    /// persisted CoverageService, marking trails complete when they cross the
    /// completion threshold for the first time.
    /// Returns (newly-completed trail ids, revisited trail ids that this hike
    /// re-walked while already complete, merged coverage map).
    @discardableResult
    private func mergeCoverage(
        areaId: String,
        sessionCoverage: [String: CoverageScore],
        trails: [Trail] = [],
        combinedPath: [GpsPoint] = []
    ) async -> (newlyCompleted: [String], revisited: [String], merged: [String: Double]) {
        let progressService = ProgressService.shared
        let coverageService = CoverageService.shared
        let prior = coverageService.coverage(for: areaId)
        var merged: [String: Double] = [:]
        var newlyCompleted: [String] = []
        var revisited: [String] = []

        for (tid, score) in sessionCoverage {
            let m = max(prior[tid] ?? 0, score.fraction)
            merged[tid] = m
            let priorComplete = (prior[tid] ?? 0) >= completeThreshold
            // Both gates required: enough of the trail covered AND
            // the hiker actually reached both endpoints. Since
            // `sessionCoverage` is the UNION of every prior hike's
            // path plus today's, this gate fires on the hike whose
            // GPS finally pushed a multi-day coverage over the
            // 0.95 threshold — the "tipping hike" for first-time
            // completion.
            let sessionComplete = score.completesTrail
            if sessionComplete && !priorComplete {
                newlyCompleted.append(tid)
            }
            // Intentionally NOT classifying `sessionComplete &&
            // priorComplete` as a revisit here. `sessionCoverage`
            // is the all-time union; for any already-complete trail
            // it still trivially passes the gate, so this branch
            // would mark EVERY previously-complete trail as
            // "revisited" on every hike — regardless of whether
            // the user actually walked it today. Revisit detection
            // lives in `computeRevisits` (called after this
            // function returns) which uses post-anchor union math
            // — the path slice since the trail was last credited
            // — and only fires when the user genuinely re-walked
            // the trail.
        }

        await coverageService.mergeCoverage(areaId: areaId, delta: merged)
        let areaName = AreaDataService.shared.cachedArea(id: areaId)?.name
            ?? AreaDataService.shared.summaries.first { $0.id == areaId }?.name
            ?? "this area"
        for tid in newlyCompleted {
            await progressService.markComplete(areaId: areaId, trailId: tid)
            // The completion event resets this trail's
            // since-completion coverage bucket. From the user's
            // POV the next time they look at the trail it shows
            // "0% walked toward the next completion" — fresh for
            // the revisit cycle. Lifetime coverage stays put.
            await coverageService.resetSinceCompletion(areaId: areaId, trailId: tid)
            // Local push notification for the trail completion. Fires
            // whether the app is foreground or background, so a user with
            // the phone in their pocket on the trail still gets the beat.
            let trailName = trails.first { $0.id == tid }?.name ?? "a trail"
            NotificationService.shared.notifyTrailComplete(
                areaId: areaId,
                areaName: areaName,
                trailId: tid,
                trailName: trailName
            )
            // Diagnostic log so future "completion fired too early"
            // reports carry the actual endpoint distances + union
            // fraction in the diag bundle. If both are
            // ≤ endpointBufferMeters (10 m), the user genuinely
            // reached both ends; otherwise the OSM endpoint and the
            // user's stopping point disagree, which usually means
            // a trail-segmentation artifact.
            let frac = sessionCoverage[tid]?.fraction ?? 0
            let (startDist, endDist) = trailEndpointDistances(trailId: tid, trails: trails, path: combinedPath)
            log.notice("trailComplete tid=\(tid, privacy: .public) fraction=\(frac) startDist=\(startDist)m endDist=\(endDist)m")
        }
        return (newlyCompleted, revisited, merged)
    }

    // MARK: - GPS point ingestion

    private func beginObservingLocation() {
        locationObserver?.cancel()
        locationObserver = Task { [weak self] in
            while !Task.isCancelled {
                let sample = await MainActor.run { () -> (CLLocationCoordinate2D, Double?)? in
                    guard let coord = self?.locationService.liveLocation else { return nil }
                    return (coord, self?.locationService.liveAltitude)
                }
                if let (coord, altitude) = sample {
                    await MainActor.run { self?.appendPoint(coord, altitude: altitude) }
                }
                try? await Task.sleep(for: gpsPollingInterval)
            }
        }
    }

    private func appendPoint(_ coord: CLLocationCoordinate2D, altitude: Double? = nil) {
        guard var rec = activeRecording else { return }
        let lat = Double(String(format: "%.6f", coord.latitude))!
        let lon = Double(String(format: "%.6f", coord.longitude))!
        let ts = Date().timeIntervalSince1970 * 1000

        if let last = rec.path.last {
            let d = haversineDistanceM(lat1: last[0], lon1: last[1], lat2: lat, lon2: lon)
            if rec.path.count > 5 {
                if d < jitterMeters || d > badFixMeters { return }
            } else if d > badFixMeters { return }
            rec.distanceMi += d / 1609.344
        }

        // 4-element point when altitude was available, else 3-element
        // for back-compat. Mixed-format paths are handled by every
        // downstream consumer via `point.altitudeMeters` (returns nil
        // for 3-element samples).
        if let altitude {
            rec.path.append([lat, lon, ts, altitude])
        } else {
            rec.path.append([lat, lon, ts])
        }
        activeRecording = rec
        persist()
    }

    // MARK: - Local history persistence

    /// Areas — other than `primary` — that this hike's GPS path actually
    /// crossed and covered a trail in, each paired with its trails (dense
    /// `rawTrails` when available). Powers trail/roam multi-area completion
    /// (walk mode gathers its areas at start instead).
    ///
    /// Three gates, cheapest first, so a dense metro can't trigger a fetch
    /// storm at stop: (1) `AreaSummary` carries only a CENTER, so pre-filter
    /// by center proximity to the path extent; (2) sort by nearest center and
    /// take at most `maxLoads`, then LOAD only those (cache first, else fetch)
    /// and gate on whether the path entered the loaded area's real bbox;
    /// (3) touch-gate on actual trail coverage. A plain loop (not
    /// `.filter { … }`) avoids Swift 6 mis-picking Foundation's `Predicate`
    /// `filter` overload.
    private func neighborAreasCrossed(by path: [GpsPoint], excluding primary: String) async -> [(String, [Trail])] {
        let pts = path.filter { $0.count >= 2 }
        guard pts.count >= 2,
              let minLat = pts.map({ $0[0] }).min(), let maxLat = pts.map({ $0[0] }).max(),
              let minLon = pts.map({ $0[1] }).min(), let maxLon = pts.map({ $0[1] }).max()
        else { return [] }
        let margin = 0.1     // ~7 mi of latitude; centers within this are candidates
        let maxLoads = 16    // hard cap on area loads per stop (fetch-storm guard)
        let cLat = (minLat + maxLat) / 2, cLon = (minLon + maxLon) / 2

        var candidates: [AreaSummary] = []
        for s in AreaDataService.shared.summaries {
            guard s.id != primary,
                  s.centerLat >= minLat - margin, s.centerLat <= maxLat + margin,
                  s.centerLon >= minLon - margin, s.centerLon <= maxLon + margin
            else { continue }
            candidates.append(s)
        }
        // Nearest-first so, under the cap, we load the parks most likely
        // actually crossed (squared distance — no sqrt needed for ordering).
        candidates.sort { a, b in
            let da = (a.centerLat - cLat) * (a.centerLat - cLat) + (a.centerLon - cLon) * (a.centerLon - cLon)
            let db = (b.centerLat - cLat) * (b.centerLat - cLat) + (b.centerLon - cLon) * (b.centerLon - cLon)
            return da < db
        }

        var out: [(String, [Trail])] = []
        for s in candidates.prefix(maxLoads) {
            let area: Area?
            if let cached = AreaDataService.shared.cachedArea(id: s.id) {
                area = cached
            } else {
                area = await AreaDataService.shared.area(id: s.id)
            }
            // Precise gate on the loaded area's real bbox: did the path
            // physically enter this area's bounds?
            guard let a = area, Self.pathEntersBBox(pts, areaBBox: a.bbox) else { continue }
            let trails = a.rawTrails ?? a.trails
            guard !trails.isEmpty else { continue }
            // Touch-gate: only a neighbor whose trails THIS hike's path
            // actually covers gets credited — otherwise a park merely near
            // the route (or one visited on a past hike) would be re-credited
            // on every unrelated stop.
            let touched = measureCoverage(path: path, trails: trails, bufferMeters: bufferMeters)
            guard touched.contains(where: { $0.value.fraction > 0 }) else { continue }
            out.append((s.id, trails))
        }
        return out
    }

    /// Whether any point of `path` lies inside `areaBBox` (padded), i.e. the
    /// hike physically entered the area's bounds. `areaBBox` is
    /// `[minLon, minLat, maxLon, maxLat]`. Pure + `nonisolated` for tests.
    nonisolated static func pathEntersBBox(_ path: [GpsPoint], areaBBox: [Double]?,
                                           padDegrees: Double = 0.005) -> Bool {
        guard let b = areaBBox, b.count == 4 else { return false }
        let minLon = b[0] - padDegrees, minLat = b[1] - padDegrees
        let maxLon = b[2] + padDegrees, maxLat = b[3] + padDegrees
        for p in path where p.count >= 2 {
            if p[0] >= minLat && p[0] <= maxLat && p[1] >= minLon && p[1] <= maxLon {
                return true
            }
        }
        return false
    }

    private func saveToHistory(_ rec: FinishedRecording) {
        var history = loadHistorySync()
        let saved = SavedRecording(
            id: UUID().uuidString,
            areaId: rec.areaId,
            startedAt: rec.startedAt,
            endedAt: rec.endedAt,
            distanceMi: (rec.distanceMi * 100).rounded() / 100,
            durationSeconds: rec.durationSeconds,
            completedTrailIds: rec.newlyCompletedTrailIds,
            path: rec.path,
            trailId: rec.trailId,
            revisitedTrailIds: rec.revisitedTrailIds,
            multiAreaCompletions: rec.multiAreaCompletions,
            multiAreaRevisited: rec.multiAreaRevisited,
            mode: rec.mode
        )
        history.insert(saved, at: 0)
        if let data = try? JSONEncoder().encode(history) {
            try? data.write(to: Self.historyFileURL)
        }
    }

    private func loadHistorySync() -> [SavedRecording] {
        guard let data = try? Data(contentsOf: Self.historyFileURL),
              let decoded = try? JSONDecoder().decode([SavedRecording].self, from: data)
        else { return [] }
        return decoded
    }

    /// Build a single GPS path that's the union of every prior hike's path
    /// in `areaId` plus an optional in-progress `currentPath`. Used by
    /// `stopRecording` and `applyLiveCoverage` to compute coverage from
    /// the union of all visits, not the max of per-hike fractions — that
    /// max-merge would lose progress when two hikes cover different
    /// halves of the same trail.
    /// `history` lets multi-area callers (stopWalk) decode the history
    /// file once and share it across per-area passes instead of paying
    /// a full JSON decode per area.
    private func combinedPathForArea(
        _ areaId: String,
        currentPath: [GpsPoint] = [],
        history: [SavedRecording]? = nil
    ) -> [GpsPoint] {
        var combined = currentPath
        // touchedAreaIds (not areaId ==): a WALK saved under a primary
        // area still contributes its GPS path to every area it credited,
        // so later hikes there build on the walk's coverage.
        for hike in (history ?? loadHistorySync()) where hike.touchedAreaIds.contains(areaId) {
            combined.append(contentsOf: hike.path)
        }
        return combined
    }

    /// For each trail the user has previously completed in this
    /// area, decide whether this hike's stop should credit a fresh
    /// "revisit" — symmetric with how initial completions work after
    /// the PR #84 union fix. The per-trail anchor is the most recent
    /// time the trail was fully covered (initial mark in
    /// `ProgressService`, or any later hike whose stop classified
    /// the trail as `newlyCompleted` / `revisited`). The post-anchor
    /// union of paths is the slice of history that hasn't yet been
    /// "credited" to a completion; if it covers the trail to >= 0.95
    /// with both endpoints reached, this hike is the one that closes
    /// the revisit.
    ///
    /// Returns only trails *not* already in `alreadyClassified`, so
    /// it composes safely after the strict `mergeCoverage` pass.
    /// Pure-ish: depends on `loadHistorySync()` + `ProgressService`,
    /// but takes the current hike's path explicitly so the same
    /// shape is testable by hand-feeding history + completion dates
    /// to `Self.computeRevisits(...)` if needed in the future.
    private func computeRevisits(
        areaId: String,
        currentPath: [GpsPoint],
        trails: [Trail],
        alreadyClassified: Set<String>,
        fullHistory: [SavedRecording]? = nil
    ) -> [String] {
        // touchedAreaIds so walks credited in this area anchor and
        // contribute paths here, not just hikes saved under it.
        let history = (fullHistory ?? loadHistorySync()).filter { $0.touchedAreaIds.contains(areaId) }
        let completionDates = ProgressService.shared.completedTrails(in: areaId)
        guard !completionDates.isEmpty else { return [] }

        var out: [String] = []
        for (tid, completionStamp) in completionDates where !alreadyClassified.contains(tid) {
            guard let anchor = Self.latestCompletionAnchor(
                trailId: tid,
                areaId: areaId,
                areaHistory: history,
                progressStamp: completionStamp
            ) else { continue }

            // Union of GPS samples taken after the anchor: every
            // past hike whose recording started after the anchor,
            // plus this hike's path. Anything earlier already
            // contributed to the previous completion.
            var postAnchorWithoutCurrent: [GpsPoint] = []
            for hike in history where hike.startedAt > anchor {
                postAnchorWithoutCurrent.append(contentsOf: hike.path)
            }
            var postAnchor: [GpsPoint] = postAnchorWithoutCurrent
            postAnchor.append(contentsOf: currentPath)

            let scores = measureCoverage(path: postAnchor, trails: trails, bufferMeters: bufferMeters)
            guard let s = scores[tid], s.completesTrail else {
                continue
            }

            // Tipping check: only credit THIS hike with the revisit
            // if the cumulative WITHOUT this hike was below the gate.
            // Otherwise the trail was already revisit-eligible at the
            // start of this hike and crediting it now would falsely
            // attribute the walk to a hike that didn't physically
            // traverse much of the trail. Matches the user's intuition
            // that the credited hike is the one that "tipped" the
            // coverage past the threshold. Trails whose cumulative
            // crossed the gate via accumulated drift (no single
            // tipping hike) stay marked complete in ProgressService
            // — they just don't appear in any hike's revisit list,
            // which is the honest record.
            let withoutScores = measureCoverage(path: postAnchorWithoutCurrent, trails: trails, bufferMeters: bufferMeters)
            let wasAlreadyEligible: Bool = withoutScores[tid].map { $0.completesTrail } ?? false
            if wasAlreadyEligible {
                let priorFrac = withoutScores[tid]?.fraction ?? -1
                log.notice("trailRevisitSuppressed tid=\(tid, privacy: .public) reason=alreadyTippedBeforeHike priorFraction=\(priorFrac) postFraction=\(s.fraction) currentPathPoints=\(currentPath.count) historyPathsSinceAnchor=\(postAnchorWithoutCurrent.count)")
                continue
            }

            out.append(tid)
            // Match the trailComplete diag log shape so a Send
            // Diagnostics bundle reveals exactly why a revisit
            // fired — the post-anchor path point count, the union
            // fraction it produced, and how close the user got
            // to each polyline endpoint.
            let (startDist, endDist) = trailEndpointDistances(trailId: tid, trails: trails, path: postAnchor)
            log.notice("trailRevisit tid=\(tid, privacy: .public) fraction=\(s.fraction) startDist=\(startDist)m endDist=\(endDist)m postAnchorPoints=\(postAnchor.count) anchor=\(anchor.timeIntervalSince1970)")
        }
        return out
    }

    /// The "anchor date" for revisit detection — the most recent
    /// moment we have evidence the trail was fully covered. Picks
    /// the LATEST date among saved hikes that credit this trail in
    /// either `completedTrailIds` or `revisitedTrailIds`. Falls back
    /// to the `ProgressService` completion stamp only when no hike
    /// has claimed the trail (manual toggle case). Returns nil when
    /// neither source is usable.
    ///
    /// `nonisolated` and `static` so the rebuild pass and the live
    /// pass share the exact same anchor logic — no risk of the two
    /// drifting apart and disagreeing on when a revisit should fire.
    nonisolated static func latestCompletionAnchor(
        trailId tid: String,
        areaId: String,
        areaHistory: [SavedRecording],
        progressStamp: String
    ) -> Date? {
        var anchor: Date? = nil
        for hike in areaHistory {
            // Walk-aware accessors: a walk's flat arrays are primary-
            // area-only, so a walk that credited this trail in a
            // secondary area anchors via its multi-area dict.
            if hike.completedTrailIds(in: areaId).contains(tid)
                || hike.revisitedTrailIds(in: areaId).contains(tid) {
                if anchor == nil || hike.endedAt > anchor! {
                    anchor = hike.endedAt
                }
            }
        }
        if anchor == nil {
            anchor = ISO8601DateFormatter().date(from: progressStamp)
        }
        return anchor
    }

    func loadHistory() async -> [SavedRecording] {
        loadHistorySync()
    }

    func deleteRecording(id: String) async {
        var history = loadHistorySync()
        history.removeAll { $0.id == id }
        if let data = try? JSONEncoder().encode(history) {
            try? data.write(to: Self.historyFileURL)
        }
    }

    // MARK: - Active recording persistence

    private func persist() {
        guard let rec = activeRecording,
              let data = try? JSONEncoder().encode(rec) else { return }
        UserDefaults.standard.set(data, forKey: persistKey)
    }
}
