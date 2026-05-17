import Foundation
import OSLog
import UIKit

/// Bundles app + OS + recent-log context into a single shareable
/// JSON file. Triggered from Settings → Developer → Send
/// Diagnostics; the user then mails / messages it to themselves
/// (or to me) so a device-test bug report carries the runtime
/// state that produced it instead of a written description that
/// drops half the signal.
///
/// Logs are pulled from `OSLogStore`, scoped to the subsystem the
/// app's `OSLog` instances already use (`com.trekdex.app`,
/// see `AreaDataService.areaLoadLog`). Entries from system
/// frameworks are filtered out so the bundle stays small and
/// human-readable.
enum DiagnosticsService {

    /// Top-level shape of the exported JSON. `Encodable` so a
/// future bump (e.g. adding hike-history summary, recording
    /// state) is just a struct field away.
    struct Bundle: Encodable {
        let appVersion: String
        let buildNumber: String
        let osVersion: String
        let device: String
        let collectedAt: String
        /// Each entry: ISO8601 timestamp + log level + category +
        /// composed message, joined into one string per line so the
        /// JSON stays grep-friendly when opened in a plain editor.
        let logs: [String]
        /// Append-only meaningful-action log from `ActivityLogService`.
        /// Captures user-initiated actions (tap area, change setting,
        /// start recording, etc.) with structured context so a
        /// session can be replayed faithfully when paired with the
        /// user's oral feedback. Chronological order.
        let activityLog: [ActivityLogService.Entry]
    }

    /// Build a diagnostics bundle and write it to a temporary file.
    /// Returns the URL of the file so the caller can hand it to
    /// `UIActivityViewController` for sharing.
    @MainActor
    static func exportBundle() async throws -> URL {
        let logs = collectLogs()
        // Flush pending writes so the just-tapped "Send Diagnostics"
        // action itself lands in the activity log slice we bundle.
        ActivityLogService.shared.flush()
        let activityLog = ActivityLogService.shared.recentEntries()
        let bundle = Bundle(
            appVersion: appVersion,
            buildNumber: buildNumber,
            osVersion: UIDevice.current.systemVersion,
            device: deviceModel(),
            collectedAt: ISO8601DateFormatter().string(from: Date()),
            logs: logs,
            activityLog: activityLog
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(bundle)
        let filename = "trekdex-diagnostics-\(Int(Date().timeIntervalSince1970)).json"
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(filename)
        try data.write(to: url, options: .atomic)
        return url
    }

    // MARK: - OSLog collection

    private static let logSubsystem = "com.trekdex.app"

    /// Pull recent log entries from `OSLogStore`. Scoped to this
    /// process so we don't accidentally bundle other apps' logs;
    /// further filtered to our subsystem so system-framework
    /// chatter doesn't dominate the file.
    ///
    /// Captures both `OSLogEntryLog` (regular `Logger.notice` /
    /// `Logger.error` / `os_log` calls — anything at level
    /// `.default` or higher, which is the iOS persistence cutoff)
    /// AND `OSLogEntrySignpost` (the existing `os_signpost`
    /// markers in `AreaDataService`). Without the signpost pass
    /// the bundle was empty for the build-12 user hike. Without
    /// emitting at `.notice`+ instead of `.info`, the bundle was
    /// STILL empty in build 114 — iOS doesn't persist `.info` by
    /// default, so OSLogStore couldn't see those entries after
    /// the fact. Fixed by promoting every diagnostic-relevant
    /// call site to `Logger.notice(...)`.
    ///
    /// Capped at the most recent 500 entries — enough to span the
    /// past few minutes of activity at typical log volume, small
    /// enough that the resulting file stays under ~100 KB and
    /// emails attach without complaint.
    private static func collectLogs() -> [String] {
        guard let store = try? OSLogStore(scope: .currentProcessIdentifier) else {
            return ["(OSLogStore unavailable — running under a configuration that doesn't permit log access)"]
        }
        // Last ~10 minutes of log history. `position(date:)` accepts
        // a `Date` and returns a position to iterate from.
        let since = Date().addingTimeInterval(-600)
        let position = store.position(date: since)
        let entries: [OSLogEntry]
        do {
            entries = try Array(store.getEntries(at: position))
        } catch {
            return ["(OSLogStore read failed: \(error.localizedDescription))"]
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        // Two passes, merged by timestamp. Building a single ordered
        // list rather than concatenating preserves the actual event
        // sequence so the bundle reads chronologically.
        var dated: [(Date, String)] = []
        dated.reserveCapacity(min(entries.count, 500))
        for entry in entries {
            if let logEntry = entry as? OSLogEntryLog,
               logEntry.subsystem == logSubsystem {
                let ts = formatter.string(from: logEntry.date)
                dated.append((logEntry.date, "[\(ts)] [\(logEntry.category)] \(logEntry.composedMessage)"))
                continue
            }
            if let signpost = entry as? OSLogEntrySignpost,
               signpost.subsystem == logSubsystem {
                let ts = formatter.string(from: signpost.date)
                let kind: String
                switch signpost.signpostType {
                case .intervalBegin: kind = "begin"
                case .intervalEnd:   kind = "end"
                case .event:         kind = "event"
                default:             kind = "signpost"
                }
                let message = signpost.composedMessage
                let suffix = message.isEmpty ? "" : " \(message)"
                dated.append((signpost.date, "[\(ts)] [\(signpost.category)] signpost.\(signpost.signpostName) \(kind)\(suffix)"))
            }
        }
        // Sort + cap at the most recent 500 entries. Sorting first
        // would lose tail entries on cap; cap first would break
        // chronology. Sort, then suffix.
        dated.sort { $0.0 < $1.0 }
        return Array(dated.suffix(500)).map(\.1)
    }

    // MARK: - App + device metadata

    /// CFBundleShortVersionString (e.g. "1.0"). Falls back to "?"
    /// when not set, which shouldn't happen in a release build.
    static var appVersion: String {
        Foundation.Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
    }

    /// CFBundleVersion (build number, e.g. "42"). Injected at
    /// archive time by ios-testflight.yml.
    static var buildNumber: String {
        Foundation.Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
    }

    /// Hardware identifier (`iPhone16,2` style). We resolve this
    /// via `uname` rather than UIDevice's marketing name because
    /// the marketing name is "iPhone" on every device and useless
    /// for triage.
    static func deviceModel() -> String {
        var systemInfo = utsname()
        uname(&systemInfo)
        let mirror = Mirror(reflecting: systemInfo.machine)
        return mirror.children
            .compactMap { ($0.value as? Int8).flatMap { $0 == 0 ? nil : UnicodeScalar(UInt8($0)) } }
            .map { String($0) }
            .joined()
    }
}
