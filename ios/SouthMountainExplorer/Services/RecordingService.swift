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
        if let data = UserDefaults.standard.data(forKey: persistKey),
           let restored = try? JSONDecoder().decode(ActiveRecording.self, from: data) {
            activeRecording = restored
            beginObservingLocation()
        }
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
                    revisitedTrailIds: Array(revisited)
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
        let priorComplete = CoverageService.shared.coverage(for: areaId)
            .filter { $0.value >= completeThreshold }
            .map(\.key)
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
        locationService.startBackgroundTracking()
        beginObservingLocation()
        // Lazy-prompt for notifications now that the user has actually
        // started a hike. The OS only asks once per install, so the
        // request is a no-op on subsequent calls.
        Task { await NotificationService.shared.ensurePermission() }
    }

    func discardRecording() {
        let prev = activeRecording
        locationObserver?.cancel()
        locationObserver = nil
        locationService.stopBackgroundTracking()
        activeRecording = nil
        errorMessage = nil
        UserDefaults.standard.removeObject(forKey: persistKey)
        log.notice("discardRecording areaId=\(prev?.areaId ?? "nil", privacy: .public) duration=\(prev.map { Date().timeIntervalSince($0.startedAt) } ?? 0)s pathPoints=\(prev?.path.count ?? 0)")
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
        // Same-id retarget on a trail-mode recording is the only
        // no-op case. Roam-mode recordings never have a matching
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
        let perHikeDelta = measureCoverage(path: rec.path, trails: trails, bufferMeters: bufferMeters)
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
            coverageDelta: perHikeDelta.mapValues(\.fraction)
        )

        saveToHistory(finished)
        log.notice("stopRecording areaId=\(rec.areaId, privacy: .public) trailId=\(rec.trailId ?? "nil", privacy: .public) duration=\(finished.durationSeconds)s distanceMi=\(rec.distanceMi) newlyCompleted=\(newlyCompleted.count) revisited=\(revisited.count)")

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
        guard let path = activeRecording?.path, path.count >= 5 else { return nil }
        let lastTs = path.last![2]
        let cutoff = lastTs - windowSeconds
        // Collect tail samples within the time window.
        var tail: [GpsPoint] = []
        for p in path.reversed() {
            guard p.count >= 3 else { continue }
            if p[2] < cutoff { break }
            tail.append(p)
        }
        let recent = Array(tail.reversed())
        guard recent.count >= 2 else { return nil }
        let elapsed = recent.last![2] - recent.first![2]
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
        let areaHistory = loadHistorySync()
            .filter { $0.areaId == areaId }
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
        var endpointsHit: [String: Bool] = [:]
        for (tid, score) in cov {
            aggregate[tid] = score.fraction
            if score.endpointsVisited { endpointsHit[tid] = true }
        }

        await CoverageService.shared.mergeCoverage(areaId: areaId, delta: aggregate)
        let nowComplete = aggregate.compactMap { (tid, v) in
            v >= completeThreshold && (endpointsHit[tid] ?? false) ? tid : nil
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
                    if let s = snapshot[tid],
                       s.fraction >= completeThreshold,
                       s.endpointsVisited
                    {
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
                )[tid], s.fraction >= completeThreshold, s.endpointsVisited else {
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
        for (tid, progressStamp) in completionDates {
            let score = cov[tid]
            let frac = score?.fraction ?? -1
            let endpointsHit = score?.endpointsVisited ?? false
            let (startDist, endDist) = trailEndpointDistances(trailId: tid, trails: trails, path: combined)
            // Anchor source: which hike claims this trail in its
            // completedTrailIds (earliest wins). Falls back to the
            // ProgressService stamp when no hike does — same shape as
            // the retro-credit pass above.
            var anchorHikeId = "none"
            var anchorAt: TimeInterval = -1
            for hike in areaHistory where hike.completedTrailIds.contains(tid) {
                if anchorAt < 0 || hike.endedAt.timeIntervalSince1970 < anchorAt {
                    anchorHikeId = hike.id
                    anchorAt = hike.endedAt.timeIntervalSince1970
                }
            }
            let anchorSource: String
            if anchorAt < 0 {
                anchorSource = "progressFallback"
                anchorAt = iso.date(from: progressStamp)?.timeIntervalSince1970 ?? -1
            } else {
                anchorSource = "hike"
            }
            log.notice("trailCompletionState tid=\(tid, privacy: .public) fraction=\(frac) startDist=\(startDist)m endDist=\(endDist)m endpointsHit=\(endpointsHit) anchorSource=\(anchorSource, privacy: .public) anchorHikeId=\(anchorHikeId, privacy: .public) anchorAt=\(anchorAt)")
        }

        if creditedByHikeId.isEmpty && revisitCreditByHikeId.isEmpty { return }

        // Persist: rewrite the full history file with the credited
        // entries patched in. Re-load to avoid clobbering hikes from
        // other areas that may have been written between our initial
        // load and now.
        var allHistory = loadHistorySync()
        for i in allHistory.indices {
            let newCompletions = creditedByHikeId[allHistory[i].id]
            let newRevisits = revisitCreditByHikeId[allHistory[i].id]
            if newCompletions == nil, newRevisits == nil { continue }
            let mergedCompleted = newCompletions.map { extras in
                Array(Set(allHistory[i].completedTrailIds).union(extras))
            } ?? allHistory[i].completedTrailIds
            let mergedRevisited = newRevisits.map { extras in
                Array(Set(allHistory[i].revisitedTrailIds).union(extras))
            } ?? allHistory[i].revisitedTrailIds
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
            let sessionComplete = score.fraction >= completeThreshold && score.endpointsVisited
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
                if let coord = await MainActor.run(body: { self?.locationService.liveLocation }) {
                    await MainActor.run { self?.appendPoint(coord) }
                }
                try? await Task.sleep(for: gpsPollingInterval)
            }
        }
    }

    private func appendPoint(_ coord: CLLocationCoordinate2D) {
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

        rec.path.append([lat, lon, ts])
        activeRecording = rec
        persist()
    }

    // MARK: - Local history persistence

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
            revisitedTrailIds: rec.revisitedTrailIds
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
    private func combinedPathForArea(_ areaId: String, currentPath: [GpsPoint] = []) -> [GpsPoint] {
        var combined = currentPath
        for hike in loadHistorySync() where hike.areaId == areaId {
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
        alreadyClassified: Set<String>
    ) -> [String] {
        let history = loadHistorySync().filter { $0.areaId == areaId }
        let completionDates = ProgressService.shared.completedTrails(in: areaId)
        guard !completionDates.isEmpty else { return [] }

        var out: [String] = []
        for (tid, completionStamp) in completionDates where !alreadyClassified.contains(tid) {
            guard let anchor = Self.latestCompletionAnchor(
                trailId: tid,
                areaHistory: history,
                progressStamp: completionStamp
            ) else { continue }

            // Union of GPS samples taken after the anchor: every
            // past hike whose recording started after the anchor,
            // plus this hike's path. Anything earlier already
            // contributed to the previous completion.
            var postAnchor: [GpsPoint] = currentPath
            for hike in history where hike.startedAt > anchor {
                postAnchor.append(contentsOf: hike.path)
            }

            let scores = measureCoverage(path: postAnchor, trails: trails, bufferMeters: bufferMeters)
            if let s = scores[tid], s.fraction >= completeThreshold, s.endpointsVisited {
                out.append(tid)
                // Match the trailComplete diag log shape so a Send
                // Diagnostics bundle reveals exactly why a revisit
                // fired — the post-anchor path point count, the union
                // fraction it produced, and how close the user got
                // to each polyline endpoint. Without this, "previously
                // completed going crazy" reports have no way to
                // distinguish dense-network over-crediting from a
                // legitimate revisit.
                let (startDist, endDist) = trailEndpointDistances(trailId: tid, trails: trails, path: postAnchor)
                log.notice("trailRevisit tid=\(tid, privacy: .public) fraction=\(s.fraction) startDist=\(startDist)m endDist=\(endDist)m postAnchorPoints=\(postAnchor.count) anchor=\(anchor.timeIntervalSince1970)")
            }
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
        areaHistory: [SavedRecording],
        progressStamp: String
    ) -> Date? {
        var anchor: Date? = nil
        for hike in areaHistory {
            if hike.completedTrailIds.contains(tid) || hike.revisitedTrailIds.contains(tid) {
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
