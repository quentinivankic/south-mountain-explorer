import Foundation
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

        // Filename pattern is the documented `south-mountain-diagnostics-<epoch>.json`.
        #expect(url.lastPathComponent.hasPrefix("south-mountain-diagnostics-"))
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
}
