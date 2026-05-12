import Foundation
import CoreLocation

private let completeThreshold = 0.9
private let bufferMeters = 30.0
private let jitterMeters = 3.0
private let badFixMeters = 200.0

/// Interval between samples of `LocationService.liveLocation` while
/// a recording is active. 2 s is a comfortable hiking cadence —
/// frequent enough that GPS jitter is averaged out by the
/// `jitterMeters` filter, infrequent enough to keep battery use
/// reasonable on a multi-hour hike.
private let gpsPollingInterval: Duration = .seconds(2)

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
        if let data = UserDefaults.standard.data(forKey: persistKey),
           let restored = try? JSONDecoder().decode(ActiveRecording.self, from: data) {
            activeRecording = restored
            beginObservingLocation()
        }
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
        locationService.startBackgroundTracking()
        beginObservingLocation()
        // Lazy-prompt for notifications now that the user has actually
        // started a hike. The OS only asks once per install, so the
        // request is a no-op on subsequent calls.
        Task { await NotificationService.shared.ensurePermission() }
    }

    func discardRecording() {
        locationObserver?.cancel()
        locationObserver = nil
        locationService.stopBackgroundTracking()
        activeRecording = nil
        errorMessage = nil
        UserDefaults.standard.removeObject(forKey: persistKey)
    }

    func stopRecording(trails: [Trail]) async -> FinishedRecording? {
        guard let rec = activeRecording else { return nil }
        locationObserver?.cancel()
        locationObserver = nil
        locationService.stopBackgroundTracking()

        let endedAt = Date()
        let sessionCoverage = measureCoverage(path: rec.path, trails: trails, bufferMeters: bufferMeters)
        let (mergeNew, mergeRevisited, _) = await mergeCoverage(
            areaId: rec.areaId,
            sessionCoverage: sessionCoverage,
            trails: trails
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
            coverageDelta: sessionCoverage.mapValues(\.fraction)
        )

        saveToHistory(finished)

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
        let sessionCoverage = measureCoverage(path: rec.path, trails: trails, bufferMeters: bufferMeters)
        _ = await mergeCoverage(areaId: rec.areaId, sessionCoverage: sessionCoverage, trails: trails)
    }

    /// Replay every saved hike's GPS path against the area's *current* trails
    /// and merge the resulting coverage. Idempotent and self-healing: if an
    /// upstream re-fetch ever assigns new IDs to the same trails (e.g. after
    /// the trail-id determinism fix), the next AreaView open recomputes
    /// completions against the new IDs from history alone — no manual
    /// re-toggle needed. Suppresses the "newly complete" haptic by going
    /// straight through CoverageService + ProgressService.bulkMarkComplete.
    func rebuildCoverageFromHistory(areaId: String, trails: [Trail]) async {
        let history = loadHistorySync().filter { $0.areaId == areaId }
        guard !history.isEmpty, !trails.isEmpty else { return }

        // Coverage fraction aggregates as before (max across hikes).
        // Endpoint-visited rolls up too: a trail counts as "endpoints
        // hit" if at least one past hike visited both ends. This keeps
        // rebuild consistent with the new live-completion gate.
        var aggregate: [String: Double] = [:]
        var endpointsHit: [String: Bool] = [:]
        for hike in history {
            let cov = measureCoverage(path: hike.path, trails: trails, bufferMeters: bufferMeters)
            for (tid, score) in cov {
                aggregate[tid] = max(aggregate[tid] ?? 0, score.fraction)
                if score.endpointsVisited { endpointsHit[tid] = true }
            }
        }
        guard !aggregate.isEmpty else { return }

        await CoverageService.shared.mergeCoverage(areaId: areaId, delta: aggregate)
        let nowComplete = aggregate.compactMap { (tid, v) in
            v >= completeThreshold && (endpointsHit[tid] ?? false) ? tid : nil
        }
        ProgressService.shared.bulkMarkComplete(areaId: areaId, trailIds: Set(nowComplete))
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
        trails: [Trail] = []
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
            // the hiker actually reached both endpoints in this
            // session. The endpoint gate is what stops "I walked 90%
            // of a linear trail but turned around before the end"
            // from firing the celebration.
            let sessionComplete = score.fraction >= completeThreshold && score.endpointsVisited
            if sessionComplete && !priorComplete {
                newlyCompleted.append(tid)
            } else if sessionComplete && priorComplete {
                revisited.append(tid)
            }
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
