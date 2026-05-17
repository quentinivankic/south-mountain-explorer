import Foundation
import Testing
@testable import SouthMountainExplorer

/// Tests for `ActivityLogService.Entry` JSON round-trip, since the
/// `DiagnosticsService.exportBundle` JSON shape depends on it being
/// stable. The service itself is a `@MainActor` singleton with disk
/// + debounce side-effects; those aren't worth isolating in unit
/// tests (covered by the device-test plan in the PR).
struct ActivityLogServiceTests {

    @Test func entryRoundTripsThroughIso8601JSON() throws {
        let original = ActivityLogService.Entry(
            timestamp: Date(timeIntervalSince1970: 1_710_000_000),
            category: "area",
            action: "opened",
            context: ["areaId": "south-mountain-park-and-preserve-az"]
        )
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(original)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let decoded = try decoder.decode(ActivityLogService.Entry.self, from: data)
        #expect(decoded.category == original.category)
        #expect(decoded.action == original.action)
        #expect(decoded.context == original.context)
        // Timestamp may lose sub-second precision through ISO8601;
        // compare at second granularity to avoid a brittle test.
        #expect(Int(decoded.timestamp.timeIntervalSince1970)
                == Int(original.timestamp.timeIntervalSince1970))
    }

    @Test func emptyContextEncodesAsEmptyObject() throws {
        let entry = ActivityLogService.Entry(
            timestamp: Date(),
            category: "app",
            action: "launch",
            context: [:]
        )
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(entry)
        let json = String(data: data, encoding: .utf8) ?? ""
        // Context shows up as {} even when empty so downstream
        // consumers can rely on the field being present.
        #expect(json.contains("\"context\":{}"))
    }
}
