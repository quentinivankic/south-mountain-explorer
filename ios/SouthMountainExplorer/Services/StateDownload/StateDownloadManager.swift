import Foundation
import Observation
import OSLog
import UIKit

/// Live counters for the scope being downloaded.
struct StateProgress: Equatable, Sendable {
    var total: Int
    var verified: Int
    var failed: Int
    var bytes: Int
}

/// What the preflight sheet shows before a job starts.
struct StatePreflight: Equatable, Sendable {
    var keys: [String]
    var areaCount: Int
    var pendingCount: Int
    var estimate: SizeEstimate
    var network: NetworkDecision
    var storage: StorageCheckResult
    var freeBytes: Int64?

    var canStart: Bool {
        pendingCount > 0 && storage.allowsStart
            && (network == .proceed || network == .needsCellularConsent)
    }
}

enum StateRowStatus: Equatable, Sendable {
    case notDownloaded
    case downloading(verified: Int, total: Int)
    case queued
    case paused(verified: Int, total: Int)
    case waitingForNetwork(verified: Int, total: Int)
    case interrupted(verified: Int, total: Int)
    case incomplete(onDevice: Int, total: Int)
    case complete(verified: Int, total: Int, date: Date?)
}

struct StateRemovalSummary: Equatable, Sendable {
    var removed: Int
    var kept: Int
}

/// Orchestrates state and country downloads on top of the verified per-area
/// pipeline. UI state lives here on the main actor; network, decode and disk
/// work runs inside `StateDownloadEngine` child tasks.
@MainActor
@Observable
final class StateDownloadManager {
    static let shared = StateDownloadManager()

    private(set) var scopes: [StateScope] = []
    private(set) var ledgers: [String: StateLedger] = [:]
    private(set) var onDiskCounts: [String: Int] = [:]
    private(set) var progress: [String: StateProgress] = [:]
    private(set) var currentKey: String?
    private(set) var jobKeys: [String] = []
    private(set) var isRunning = false
    private(set) var notice: String?
    private(set) var updateSummaries: [String: String] = [:]
    private(set) var lastAnnouncement: String?

    @ObservationIgnored private let log = Logger(subsystem: "com.southmountainexplorer.app", category: "StateDownload")
    @ObservationIgnored private var store: StateDownloadStore
    @ObservationIgnored private var sizeTable: StateSizeTable?
    @ObservationIgnored private var jobTask: Task<Void, Never>?
    @ObservationIgnored private var jobGeneration = 0
    @ObservationIgnored private var work: [String: StateLedger] = [:]
    @ObservationIgnored private var consentGiven = false
    @ObservationIgnored private var interruptedKeys: Set<String> = []
    @ObservationIgnored private var resumePlan: (keys: [String], force: Bool)?
    @ObservationIgnored private var previousIdleTimer = false
    @ObservationIgnored private var lastPublish = Date.distantPast
    @ObservationIgnored private var lastFlush = Date.distantPast
    @ObservationIgnored private var eventsSincePublish = 0
    @ObservationIgnored private var eventsSinceFlush = 0
    @ObservationIgnored private var announcedMilestones: [String: Int] = [:]

    var allowCellular: Bool {
        UserDefaults.standard.bool(forKey: StorageKeys.offlineStatesAllowCellular)
    }

    private init() {
        store = StateDownloadStore.live()
        sizeTable = StateSizeTable.loadBundled()
    }

    // MARK: - Catalog

    func reload() {
        let service = AreaDataService.shared
        scopes = StateCatalog.build(
            summaries: service.summaries,
            isHiddenAlias: { service.isHiddenAlias($0) }
        )
        var loaded: [String: StateLedger] = [:]
        for scope in scopes {
            var ledger = store.load(stateKey: scope.id)
            if ledger.record.status == .running && !isRunning {
                // The app died mid-job: show as interrupted, never auto-start.
                ledger.record.status = .paused
                interruptedKeys.insert(scope.id)
                store.save(ledger)
            }
            loaded[scope.id] = ledger
        }
        if isRunning {
            for (key, ledger) in work { loaded[key] = ledger }
        }
        ledgers = loaded
        refreshOnDiskCounts()
    }

    func scope(_ key: String) -> StateScope? { scopes.first { $0.id == key } }

    var usScopes: [StateScope] { scopes.filter { $0.kind == .usState } }
    var countryScopes: [StateScope] { scopes.filter { $0.kind == .country } }

    /// The user's state, when the location is known and close to an area.
    func currentStateKey() -> String? {
        let location = LocationService.shared.userLocation
        let defaults = UserDefaults.standard
        let lat = location?.latitude ?? defaults.object(forKey: StorageKeys.userLocationLat) as? Double
        let lon = location?.longitude ?? defaults.object(forKey: StorageKeys.userLocationLon) as? Double
        guard let lat, let lon,
              let nearest = AreaDataService.shared.nearby(lat: lat, lon: lon, limit: 1).first,
              haversineDistanceMi(lat1: lat, lon1: lon, lat2: nearest.centerLat, lon2: nearest.centerLon) <= 150
        else { return nil }
        let key = nearest.subtitle.trimmingCharacters(in: .whitespacesAndNewlines)
        return scope(key) == nil ? nil : key
    }

    private func cacheDirectory() -> URL { AreaDataService.shared.areaCacheDirectory }

    /// Cheap `fileExists` count, no decoding.
    func refreshOnDiskCounts() {
        let dir = cacheDirectory()
        var counts: [String: Int] = [:]
        for scope in scopes {
            counts[scope.id] = scope.areaIDs.reduce(0) { total, id in
                total + (FileManager.default.fileExists(
                    atPath: dir.appendingPathComponent("\(id).json").path
                ) ? 1 : 0)
            }
        }
        onDiskCounts = counts
    }

    private func missingIDs(_ scope: StateScope) -> Set<String> {
        let dir = cacheDirectory()
        return Set(scope.areaIDs.filter {
            !FileManager.default.fileExists(atPath: dir.appendingPathComponent("\($0).json").path)
        })
    }

    // MARK: - Display

    func fullEstimate(_ scope: StateScope) -> SizeEstimate {
        sizeTable?.estimate(for: scope) ?? .zero
    }

    /// True when the size table has no measured row for this scope (e.g. a
    /// newly added country), so the figure is a global-rate guess.
    func isRoughEstimate(_ scope: StateScope) -> Bool {
        sizeTable?.row(for: scope.id) == nil
    }

    func remainingEstimate(_ scope: StateScope) -> SizeEstimate {
        sizeTable?.estimate(for: scope, missingIDs: missingIDs(scope)) ?? .zero
    }

    func failedCount(_ key: String) -> Int {
        ledgers[key]?.record.failed.count ?? 0
    }

    /// Failed areas as (area name, short reason), sorted by name.
    func failedAreas(_ key: String) -> [(name: String, reason: String)] {
        let names = AreaDataService.shared.summariesById.mapValues(\.name)
        return (ledgers[key]?.record.failed ?? [:])
            .map { (name: names[$0.key] ?? $0.key, reason: $0.value.shortReason) }
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    func status(for key: String) -> StateRowStatus {
        guard let scope = scope(key) else { return .notDownloaded }
        let total = scope.areaIDs.count
        let onDisk = onDiskCounts[key] ?? 0
        let record = ledgers[key]?.record
        if isRunning, jobKeys.contains(key) {
            if currentKey == key {
                return .downloading(verified: progress[key]?.verified ?? onDisk, total: total)
            }
            if let mine = jobKeys.firstIndex(of: key),
               let current = currentKey.flatMap({ jobKeys.firstIndex(of: $0) }),
               mine > current {
                return .queued
            }
        }
        guard let record else { return onDisk > 0 ? .incomplete(onDevice: onDisk, total: total) : .notDownloaded }
        switch record.status {
        case .complete:
            return onDisk >= total
                ? .complete(verified: total, total: total, date: record.completedAt)
                : .incomplete(onDevice: onDisk, total: total)
        case .waitingForNetwork:
            return .waitingForNetwork(verified: onDisk, total: total)
        case .paused where record.startedAt != nil:
            return interruptedKeys.contains(key)
                ? .interrupted(verified: onDisk, total: total)
                : .paused(verified: onDisk, total: total)
        case .running:
            return .interrupted(verified: onDisk, total: total)
        case .paused, .idle:
            return .notDownloaded
        }
    }

    // MARK: - Preflight

    func preflight(keys: [String], consent: Bool = false) -> StatePreflight {
        var estimate = SizeEstimate.zero
        var areaCount = 0
        var pending = 0
        for key in keys {
            guard let scope = scope(key) else { continue }
            let missing = missingIDs(scope)
            areaCount += scope.areaIDs.count
            pending += missing.count
            estimate = estimate + (sizeTable?.estimate(for: scope, missingIDs: missing) ?? .zero)
        }
        let network = NetworkService.shared
        let decision = StateDownloadPolicy.decide(
            isConnected: network.isConnected,
            isExpensive: network.isExpensive,
            allowCellular: allowCellular,
            consentGiven: consent
        )
        let free = StorageCheck.liveFreeBytes()
        return StatePreflight(
            keys: keys,
            areaCount: areaCount,
            pendingCount: pending,
            estimate: estimate,
            network: decision,
            storage: StorageCheck.evaluate(freeBytes: free, remainingOnDeviceEstimate: estimate.onDeviceBytes),
            freeBytes: free
        )
    }

    func usKeys() -> [String] {
        let current = currentStateKey()
        let keys = usScopes.map(\.id)
        guard let current, keys.contains(current) else { return keys }
        return [current] + keys.filter { $0 != current }
    }

    // MARK: - Start / cancel / resume

    func start(keys: [String], forceUpdate: Bool = false, onlyFailed: Bool = false, consent: Bool = false) {
        guard !isRunning else { return }
        let valid = keys.filter { scope($0) != nil }
        guard !valid.isEmpty else { return }
        consentGiven = consent
        let gate = preflight(keys: valid, consent: consent)
        if !forceUpdate, !onlyFailed {
            guard gate.canStart else {
                notice = Self.blockedNotice(gate)
                return
            }
        }
        // Wi-Fi gate also applies to update/retry: no request without a path.
        switch gate.network {
        case .blocked(let reason):
            notice = reason == .offline
                ? "You're offline. Connect to download."
                : "Cellular data is off for downloads. Connect to Wi-Fi or turn on Allow cellular data."
            return
        case .needsCellularConsent where !consent:
            notice = "Confirm cellular data use to start."
            return
        default:
            break
        }

        notice = nil
        resumePlan = nil
        isRunning = true
        jobKeys = valid
        progress = [:]
        announcedMilestones = [:]
        previousIdleTimer = UIApplication.shared.isIdleTimerDisabled
        UIApplication.shared.isIdleTimerDisabled = true
        jobGeneration += 1
        let generation = jobGeneration
        jobTask = Task { [weak self] in
            await self?.runJob(keys: valid, forceUpdate: forceUpdate, onlyFailed: onlyFailed, generation: generation)
        }
    }

    func cancel() {
        resumePlan = nil
        jobTask?.cancel()
    }

    /// Foreground return or a network change: pick up a stalled job if the
    /// policy now allows it. Never starts anything that was not already running
    /// in this process.
    func resumeIfStalled() {
        guard !isRunning, let plan = resumePlan else { return }
        let network = NetworkService.shared
        let decision = StateDownloadPolicy.decide(
            isConnected: network.isConnected,
            isExpensive: network.isExpensive,
            allowCellular: allowCellular,
            consentGiven: consentGiven
        )
        guard decision == .proceed else { return }
        start(keys: plan.keys, forceUpdate: plan.force, consent: consentGiven)
    }

    func sceneDidEnterBackground() {
        guard isRunning else { return }
        let taskID = UIApplication.shared.beginBackgroundTask(withName: "state-download-ledger-flush")
        flushAll()
        if taskID != .invalid { UIApplication.shared.endBackgroundTask(taskID) }
    }

    // MARK: - Job

    private func runJob(keys: [String], forceUpdate: Bool, onlyFailed: Bool, generation: Int) async {
        var stopped: EngineStopReason = .finished
        for key in keys {
            if Task.isCancelled { stopped = .cancelled; break }
            let reason = await runScope(key: key, forceUpdate: forceUpdate, onlyFailed: onlyFailed)
            if reason != .finished { stopped = reason; break }
        }
        guard generation == jobGeneration else { return }
        finishJob(stopped: stopped, keys: keys, forceUpdate: forceUpdate)
    }

    private func finishJob(stopped: EngineStopReason, keys: [String], forceUpdate: Bool) {
        flushAll()
        UIApplication.shared.isIdleTimerDisabled = previousIdleTimer
        isRunning = false
        currentKey = nil
        jobTask = nil
        if stopped == .waitingForNetwork {
            resumePlan = (keys, forceUpdate)
            notice = "Waiting for a connection. Downloads resume automatically."
        }
        work = [:]
        reload()
        publishAnnouncement(for: keys, stopped: stopped)
    }

    private func publishAnnouncement(for keys: [String], stopped: EngineStopReason) {
        guard let key = keys.first else { return }
        let name = keys.count > 1 ? "United States" : key
        switch stopped {
        case .finished:
            let failed = keys.reduce(0) { $0 + failedCount($1) }
            lastAnnouncement = failed == 0 ? "\(name) download complete." : "\(name) download finished with \(failed) failed areas."
        case .cancelled:
            lastAnnouncement = "\(name) download paused."
        case .waitingForNetwork:
            lastAnnouncement = "\(name) download waiting for a connection."
        }
    }

    private func runScope(key: String, forceUpdate: Bool, onlyFailed: Bool) async -> EngineStopReason {
        guard let scope = scope(key) else { return .finished }
        var ledger = work[key] ?? ledgers[key] ?? store.load(stateKey: key)
        currentKey = key

        let members = (forceUpdate || ledger.record.memberIDs.isEmpty)
            ? scope.areaIDs
            : ledger.record.memberIDs
        let downloader = AreaCDNDownloader(
            session: AreaCDNDownloader.makeSession(allowCellular: allowCellular),
            cacheDirectory: cacheDirectory()
        )

        // Verified scan off the main actor: decoding hundreds of files.
        let available: Set<String> = await Task.detached(priority: .userInitiated) {
            Set(members.filter { downloader.isAvailable(id: $0) })
        }.value
        if Task.isCancelled { return .cancelled }

        let protected = currentProtectedIDs()
        if ledger.record.memberIDs.isEmpty {
            ledger.record.preexistingIDs = available
        } else {
            for id in StateClaimRecovery.recoverable(
                members: members,
                availableOnDisk: available,
                preexisting: ledger.record.preexistingIDs,
                claims: ledger.claims,
                protected: protected
            ) {
                ledger.claims[id] = Claim(stateKey: key, bytes: 0, verifiedAt: Date())
            }
        }
        if ledger.record.startedAt == nil || ledger.record.status == .complete {
            ledger.record.startedAt = Date()
        }
        ledger.record.memberIDs = members
        ledger.record.status = .running
        interruptedKeys.remove(key)
        ledger.record.lastUpdatedAt = Date()

        var ids: [String]
        if forceUpdate {
            ids = members
        } else if onlyFailed {
            ids = members.filter { ledger.record.failed[$0] != nil && !available.contains($0) }
        } else {
            ids = members.filter { !available.contains($0) }
        }
        if onlyFailed, !forceUpdate {
            ledger.record.failed = ledger.record.failed.filter { !available.contains($0.key) }
        }

        work[key] = ledger
        store.save(ledger)
        progress[key] = StateProgress(
            total: members.count,
            verified: members.filter { available.contains($0) }.count,
            failed: 0,
            bytes: 0
        )
        ledgers[key] = ledger
        lastFlush = Date()
        lastPublish = Date()
        eventsSinceFlush = 0
        eventsSincePublish = 0

        let engine = StateDownloadEngine(
            isAvailable: { downloader.isAvailable(id: $0) },
            fetch: { id, etag in await downloader.download(id: id, etag: etag) }
        )
        let etags = ledger.etags
        let result = await engine.run(
            ids: ids,
            forceUpdate: forceUpdate,
            etags: etags,
            onEvent: { [weak self] event in
                await self?.handle(event, key: key, forceUpdate: forceUpdate)
            }
        )

        finalize(
            key: key,
            members: members,
            available: available,
            result: result,
            forceUpdate: forceUpdate
        )
        return result.stopReason
    }

    private func handle(_ event: EngineEvent, key: String, forceUpdate: Bool) {
        guard var ledger = work[key] else { return }
        var p = progress[key] ?? StateProgress(total: ledger.record.memberIDs.count, verified: 0, failed: 0, bytes: 0)
        switch event.outcome {
        case .stored(let bytes, let etag):
            if !ledger.record.preexistingIDs.contains(event.id) {
                ledger.claims[event.id] = Claim(stateKey: key, bytes: bytes, verifiedAt: Date())
            }
            if let etag { ledger.etags[event.id] = etag }
            ledger.record.failed[event.id] = nil
            if !forceUpdate { p.verified += 1 }
            p.bytes += bytes
        case .unchanged:
            if !forceUpdate { p.verified += 1 }
        case .failed(let kind):
            if !kind.isTransient { ledger.record.failed[event.id] = kind }
            p.failed += 1
        case .cancelled, .policyBlocked:
            break
        }
        work[key] = ledger
        progress[key] = p
        eventsSincePublish += 1
        eventsSinceFlush += 1

        let now = Date()
        if eventsSincePublish >= 10 || now.timeIntervalSince(lastPublish) >= 0.25 {
            lastPublish = now
            eventsSincePublish = 0
            ledgers[key] = ledger
            announceMilestone(key: key, progress: p)
        }
        if eventsSinceFlush >= 25 || now.timeIntervalSince(lastFlush) >= 2 {
            lastFlush = now
            eventsSinceFlush = 0
            store.save(ledger)
        }
    }

    private func announceMilestone(key: String, progress p: StateProgress) {
        guard p.total > 0 else { return }
        let percent = p.verified * 100 / p.total
        let step = (percent / 25) * 25
        guard step > 0, step < 100, (announcedMilestones[key] ?? 0) < step else { return }
        announcedMilestones[key] = step
        lastAnnouncement = "\(key) download \(step) percent."
    }

    private func finalize(
        key: String,
        members: [String],
        available: Set<String>,
        result: EngineResult,
        forceUpdate: Bool
    ) {
        guard var ledger = work[key] else { return }
        let memberSet = Set(members)
        let verified = available.union(result.stored.keys).union(result.unchanged)
            .union(result.alreadyOnDevice).intersection(memberSet)

        // Failures: this run's non-transient failures plus the ones that
        // survived the retry; anything verified without an update error is clear.
        var failed = ledger.record.failed
        for (id, kind) in result.failed { failed[id] = kind }
        for id in result.stored.keys { failed[id] = nil }
        for id in result.unchanged { failed[id] = nil }
        if !forceUpdate {
            for id in verified { failed[id] = nil }
        }
        failed = failed.filter { memberSet.contains($0.key) }

        for (id, tag) in result.etags { ledger.etags[id] = tag }
        for (id, bytes) in result.stored where !ledger.record.preexistingIDs.contains(id) {
            ledger.claims[id] = Claim(stateKey: key, bytes: bytes, verifiedAt: Date())
        }

        ledger.record.failed = failed
        ledger.record.verifiedCount = verified.count
        ledger.record.onDeviceBytes = ledger.claims.values.reduce(0) { $0 + $1.bytes }
        ledger.record.lastUpdatedAt = Date()
        let allVerified = verified.count == memberSet.count
        switch result.stopReason {
        case .finished where allVerified:
            ledger.record.status = .complete
            ledger.record.completedAt = Date()
        case .finished, .cancelled:
            ledger.record.status = .paused
        case .waitingForNetwork:
            ledger.record.status = .waitingForNetwork
        }

        if forceUpdate, result.stopReason == .finished {
            let changed = result.stored.count
            let fresh = result.stored.keys.filter { !available.contains($0) }.count
            updateSummaries[key] = "\(changed - fresh) updated, \(fresh) new, \(result.unchanged.count) unchanged"
            removeStaleClaims(ledger: &ledger, key: key, currentMembers: memberSet)
        }

        progress[key] = StateProgress(
            total: members.count, verified: verified.count, failed: failed.count,
            bytes: progress[key]?.bytes ?? 0
        )
        work[key] = ledger
        ledgers[key] = ledger
        store.save(ledger)
    }

    /// Update: claimed areas that left the index are deleted unless protected.
    private func removeStaleClaims(ledger: inout StateLedger, key: String, currentMembers: Set<String>) {
        let stale = ledger.claims.filter { $0.value.stateKey == key && !currentMembers.contains($0.key) }
        let protected = currentProtectedIDs()
        for id in stale.keys {
            if !protected.contains(id) && !ledger.record.preexistingIDs.contains(id) {
                AreaDataService.shared.removeDownloadedArea(id: id)
            }
            ledger.claims[id] = nil
            ledger.etags[id] = nil
        }
    }

    private func flushAll() {
        for (_, ledger) in work { store.save(ledger) }
        for (key, ledger) in work { ledgers[key] = ledger }
    }

    // MARK: - Remove

    func currentProtectedIDs() -> Set<String> {
        let service = AreaDataService.shared
        return StateProtection.protectedIDs(
            favorites: FavoritesService.shared.favoriteIds,
            opened: Set(ActivityService.shared.areaOpenedAt.keys),
            userHeld: UserHeldAreas.all(),
            activeRecordingAreaID: RecordingService.shared.activeRecording?.areaId,
            canonical: { service.canonicalId(for: $0) }
        )
    }

    func removalPreview(_ key: String) -> RemovalPlan {
        let ledger = ledgers[key] ?? store.load(stateKey: key)
        return StateRemovalPlanner.plan(
            stateKey: key,
            claims: ledger.claims,
            preexisting: ledger.record.preexistingIDs,
            protected: currentProtectedIDs()
        )
    }

    @discardableResult
    func remove(_ key: String) -> StateRemovalSummary? {
        guard !isRunning else { return nil }
        var ledger = store.load(stateKey: key)
        let plan = StateRemovalPlanner.plan(
            stateKey: key,
            claims: ledger.claims,
            preexisting: ledger.record.preexistingIDs,
            protected: currentProtectedIDs()
        )
        var removed: [String] = []
        var kept = plan.keptProtected
        for id in plan.delete {
            // Re-read just before deleting: a favorite added mid-removal wins.
            if currentProtectedIDs().contains(id) {
                kept.append(id)
                continue
            }
            AreaDataService.shared.removeDownloadedArea(id: id)
            removed.append(id)
        }
        AreaDataService.shared.dropMemoryCache(ids: removed)
        // Kept files stay on the device as the user's own from now on.
        UserHeldAreas.add(kept)
        ledger = StateLedger(stateKey: key)
        store.save(ledger)
        ledgers[key] = ledger
        updateSummaries[key] = nil
        refreshOnDiskCounts()
        return StateRemovalSummary(removed: removed.count, kept: kept.count)
    }

    /// After Reset All / Clear All Offline Trails removed the area files.
    func resetAll() {
        cancel()
        store.clearAll()
        UserDefaults.standard.removeObject(forKey: StorageKeys.offlineStatesUserHeld)
        ledgers = [:]
        work = [:]
        progress = [:]
        updateSummaries = [:]
        resumePlan = nil
        if !scopes.isEmpty { reload() }
    }

    func dismissNotice() { notice = nil }

    private static func blockedNotice(_ gate: StatePreflight) -> String {
        if case .insufficient(let needed) = gate.storage {
            let text = ByteCountFormatter.string(fromByteCount: needed, countStyle: .file)
            return "Not enough free space. About \(text) is needed."
        }
        switch gate.network {
        case .blocked(.offline): return "You're offline. Connect to download."
        case .blocked(.cellularNotAllowed):
            return "Cellular data is off for downloads. Connect to Wi-Fi or turn on Allow cellular data."
        default: return "Nothing left to download."
        }
    }
}
