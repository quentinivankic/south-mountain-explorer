import Foundation
import Testing
@testable import SouthMountainExplorer

struct StorageKeysTests {
    @Test func launchMigrationRemovesOnlyRetiredDebugDiagAutoSyncPreference() throws {
        let suiteName = "StorageKeysTests-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let retiredKey = "summit:debug-diag-autosync"
        let unknownFutureDebugKey = "summit:debug-future-preference"
        defaults.set(true, forKey: retiredKey)
        defaults.set(true, forKey: StorageKeys.debugHUD)
        defaults.set("keep-me", forKey: unknownFutureDebugKey)

        StorageKeys.removeRetiredDebugDiagAutoSyncPreference(from: defaults)

        #expect(defaults.object(forKey: retiredKey) == nil)
        #expect(defaults.bool(forKey: StorageKeys.debugHUD))
        #expect(defaults.string(forKey: unknownFutureDebugKey) == "keep-me")
    }
}
