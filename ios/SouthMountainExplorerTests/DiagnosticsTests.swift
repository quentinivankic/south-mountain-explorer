import Foundation
import OSLog
import Testing
@testable import SouthMountainExplorer

/// Smoke tests for the build-10 PR-2 diagnostics machinery —
/// `MemoryProbe` + `DiagnosticsService`. These don't validate the
/// numeric values (FPS / memory are environment-dependent), but
/// they pin down the shape of the export and catch silent
/// regressions like "OSLogStore can't be opened in this test
/// configuration" or "device-model parsing returns empty string".
struct DiagnosticsTests {

    // MARK: - MemoryProbe

    @Test func memoryProbeReturnsPositiveFootprint() {
        // Any iOS process is well above 0 MB. Loose lower bound
        // (≥ 1 MB) avoids brittleness across iOS versions and
        // simulator vs device — we just want "the syscall worked
        // and returned a sane-looking number."
        let mb = MemoryProbe.footprintMB()
        #expect(mb > 1.0, "MemoryProbe returned \(mb) MB — expected ≥ 1 MB")
    }

    // MARK: - DiagnosticsService bundle export

    @Test @MainActor func exportBundleProducesReadableJSON() async throws {
        let url = try await DiagnosticsService.exportBundle()

        // File exists on disk.
        #expect(FileManager.default.fileExists(atPath: url.path),
                "Bundle file not at \(url.path)")

        // Filename pattern is the documented `trekdex-diagnostics-<epoch>.json`.
        #expect(url.lastPathComponent.hasPrefix("trekdex-diagnostics-"))
        #expect(url.pathExtension == "json")

        // Round-trip parse to verify the bundle structure didn't
        // drift away from the documented `Bundle` schema.
        let data = try Data(contentsOf: url)
        let parsed = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let obj = try #require(parsed)

        // Required keys all present.
        #expect(obj["appVersion"] is String)
        #expect(obj["buildNumber"] is String)
        #expect(obj["osVersion"] is String)
        #expect(obj["device"] is String)
        #expect(obj["collectedAt"] is String)
        #expect(obj["logs"] is [Any])

        // collectedAt parses as ISO8601 — sanity check we wrote a
        // real timestamp, not a placeholder.
        let stamp = try #require(obj["collectedAt"] as? String)
        let formatter = ISO8601DateFormatter()
        #expect(formatter.date(from: stamp) != nil, "Bad ISO8601 stamp: \(stamp)")

        // Cleanup.
        try? FileManager.default.removeItem(at: url)
    }

    @Test func deviceModelIsNonEmpty() {
        // On the simulator this returns the simulator's reported
        // hardware string (e.g. "arm64"). On device it's
        // "iPhone16,2" etc. Either way it should be non-empty.
        let model = DiagnosticsService.deviceModel()
        #expect(!model.isEmpty, "deviceModel() returned empty string")
    }

    @Test func appVersionAndBuildAreNonEmpty() {
        // Real values come from the app's Info.plist on a
        // TestFlight build. In the unit-test runner Bundle.main is
        // the test bundle, which may not carry CFBundleShortVersionString
        // — DiagnosticsService falls back to "?" in that case.
        // Either way, the strings are non-empty.
        #expect(!DiagnosticsService.appVersion.isEmpty)
        #expect(!DiagnosticsService.buildNumber.isEmpty)
    }

    @Test @MainActor func exportBundleCapturesRecentLoggerCall() async throws {
        // Build-13 PR 2 regression test: confirm a Logger.info call
        // emitted right before exportBundle gets captured in the
        // bundle's logs array. Pre-PR-2 the collector only matched
        // OSLogEntryLog with the right subsystem, but the app
        // emitted no log lines at all (only signposts), so the
        // array was empty. After PR 2 the same call shows up.
        //
        // Logger uses the app's subsystem so the
        // DiagnosticsService filter accepts it. The category is
        // unique to this test so we can scan the bundle for it
        // without colliding with real app logs.
        let log = Logger(subsystem: "com.trekdex.app", category: "DiagnosticsTests")
        let marker = "diagnostics-marker-\(UUID().uuidString)"
        // Emit at `.notice` so the entry is persisted to OSLogStore.
        // `.info` is the default for `Logger.log(...)` but iOS only
        // keeps `.default`-level and above in persistent storage, so
        // a build that exercises this test via OSLogStore must use
        // notice+ or the entry is gone by the time we read.
        log.notice("\(marker, privacy: .public)")

        // Small delay so OSLogStore's writer has time to flush
        // before we read. Without this the test races and
        // occasionally finds the bundle empty.
        try await Task.sleep(for: .milliseconds(200))

        let url = try await DiagnosticsService.exportBundle()
        defer { try? FileManager.default.removeItem(at: url) }
        let data = try Data(contentsOf: url)
        let parsed = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let logs = try #require(parsed["logs"] as? [String])

        // Best-effort: OSLogStore in unit-test mode sometimes
        // doesn't surface entries from the same process. If the
        // test consistently passes locally + on CI, we keep the
        // assertion strict. If it flakes, weaken to "logs is non-
        // empty" or drop the assertion.
        #expect(
            logs.contains(where: { $0.contains(marker) }),
            "Bundle didn't include the test-emitted Logger.info call. Logs: \(logs.suffix(5))"
        )
    }
}
