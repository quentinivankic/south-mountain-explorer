import Foundation

/// Lightweight engagement / behaviour telemetry persisted to UserDefaults.
/// Two captures, both cheap (a few KB after years of use) but hard to
/// derive after the fact:
///
///   - `areaOpenedAt[areaId]` — last time AreaView appeared for an area.
///     Unlocks "you haven't been to X in a while" reminders + rediscover
///     surfaces.
///   - `sessions` — pairs of (start, end) for each app foreground session.
///     Useful for tester-engagement debugging and future "your most
///     active hours" features.
///
/// All writes are local, no network. No PII captured. Cap on `sessions`
/// length so the array can't grow unbounded.
@MainActor
@Observable
final class ActivityService {
    static let shared = ActivityService()

    struct AppSession: Codable, Sendable {
        let start: Date
        let end: Date
    }

    private(set) var areaOpenedAt: [String: Date] = [:]
    private(set) var sessions: [AppSession] = []

    private var currentSessionStart: Date? = nil

    private let areaOpenedKey = "summit:area-opened-at"
    private let sessionsKey = "summit:app-sessions"
    private let maxSessions = 1000

    private init() {
        if let data = UserDefaults.standard.data(forKey: areaOpenedKey),
           let decoded = try? JSONDecoder().decode([String: Date].self, from: data) {
            areaOpenedAt = decoded
        }
        if let data = UserDefaults.standard.data(forKey: sessionsKey),
           let decoded = try? JSONDecoder().decode([AppSession].self, from: data) {
            sessions = decoded
        }
    }

    // MARK: - Area visits

    func recordAreaOpened(_ areaId: String) {
        areaOpenedAt[areaId] = Date()
        if let data = try? JSONEncoder().encode(areaOpenedAt) {
            UserDefaults.standard.set(data, forKey: areaOpenedKey)
        }
    }

    func lastOpened(areaId: String) -> Date? {
        areaOpenedAt[areaId]
    }

    // MARK: - App sessions

    /// Mark the start of a foreground session. Pair with `endSession` when
    /// the app backgrounds. Calling twice without an end in between
    /// silently overwrites — we treat the last start as authoritative
    /// rather than logging zero-length sessions.
    func startSession() {
        currentSessionStart = Date()
    }

    func endSession() {
        guard let start = currentSessionStart else { return }
        let end = Date()
        // Skip degenerate sessions (e.g. <1s) that are usually scenePhase
        // bouncing during launch transitions, not real engagement.
        if end.timeIntervalSince(start) >= 1 {
            sessions.append(AppSession(start: start, end: end))
            if sessions.count > maxSessions {
                sessions.removeFirst(sessions.count - maxSessions)
            }
            if let data = try? JSONEncoder().encode(sessions) {
                UserDefaults.standard.set(data, forKey: sessionsKey)
            }
        }
        currentSessionStart = nil
    }
}
