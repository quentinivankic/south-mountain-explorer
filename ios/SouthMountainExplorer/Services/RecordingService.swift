import Foundation
import CoreLocation

private let completeThreshold = 0.9
private let bufferMeters = 30.0
private let jitterMeters = 3.0
private let badFixMeters = 200.0

@MainActor
@Observable
final class RecordingService {
    static let shared = RecordingService()

    private(set) var activeRecording: ActiveRecording? = nil
    private(set) var errorMessage: String? = nil

    private let locationService = LocationService.shared
    private var locationObserver: Task<Void, Never>? = nil

    private let persistKey = "summit:active-recording"

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
        activeRecording = ActiveRecording(
            areaId: areaId,
            mode: mode,
            trailId: mode == .trail ? trailId : nil,
            startedAt: Date(),
            path: [],
            distanceMi: 0
        )
        errorMessage = nil
        persist()
        locationService.startBackgroundTracking()
        beginObservingLocation()
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
        let sessionCoverage = measureCoverage(rec: rec, trails: trails)
        let progressService = ProgressService.shared
        let coverageService = CoverageService.shared

        let prior = coverageService.coverage(for: rec.areaId)
        var merged: [String: Double] = [:]
        var newlyCompleted: [String] = []

        for (tid, v) in sessionCoverage {
            let m = max(prior[tid] ?? 0, v)
            merged[tid] = m
            if (prior[tid] ?? 0) < completeThreshold && m >= completeThreshold {
                newlyCompleted.append(tid)
            }
        }

        await coverageService.mergeCoverage(areaId: rec.areaId, delta: merged)
        for tid in newlyCompleted {
            await progressService.markComplete(areaId: rec.areaId, trailId: tid)
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
            coverageDelta: sessionCoverage
        )

        saveToHistory(finished)

        activeRecording = nil
        UserDefaults.standard.removeObject(forKey: persistKey)
        return finished
    }

    // MARK: - GPS point ingestion

    private func beginObservingLocation() {
        locationObserver?.cancel()
        locationObserver = Task { [weak self] in
            while !Task.isCancelled {
                if let coord = await MainActor.run(body: { self?.locationService.liveLocation }) {
                    await MainActor.run { self?.appendPoint(coord) }
                }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
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

    // MARK: - Coverage calculation

    private func measureCoverage(rec: ActiveRecording, trails: [Trail]) -> [String: Double] {
        guard rec.path.count >= 3 else { return [:] }

        let cell = 0.0003
        var grid: [String: [[Double]]] = [:]
        func gridKey(_ la: Double, _ lo: Double) -> String {
            "\(Int((la / cell).rounded())):\(Int((lo / cell).rounded()))"
        }
        for p in rec.path {
            let k = gridKey(p[0], p[1])
            grid[k, default: []].append([p[0], p[1]])
        }
        func neighbors(la: Double, lo: Double) -> [[Double]] {
            let r = Int((la / cell).rounded())
            let c = Int((lo / cell).rounded())
            var out: [[Double]] = []
            for dr in -1...1 {
                for dc in -1...1 {
                    if let pts = grid["\(r + dr):\(c + dc)"] { out += pts }
                }
            }
            return out
        }

        var result: [String: Double] = [:]
        for trail in trails {
            var total = 0
            var covered = 0
            for seg in trail.segments {
                for node in seg {
                    guard node.count >= 2 else { continue }
                    total += 1
                    let cands = neighbors(la: node[0], lo: node[1])
                    for p in cands {
                        if haversineDistanceM(lat1: node[0], lon1: node[1], lat2: p[0], lon2: p[1]) <= bufferMeters {
                            covered += 1
                            break
                        }
                    }
                }
            }
            if total > 0 {
                let frac = Double(covered) / Double(total)
                if frac > 0.02 { result[trail.id] = frac }
            }
        }
        return result
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
            path: rec.path
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
