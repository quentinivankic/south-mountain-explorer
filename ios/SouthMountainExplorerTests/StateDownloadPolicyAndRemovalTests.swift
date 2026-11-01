import Foundation
import Testing
@testable import SouthMountainExplorer

struct StateDownloadPolicyTests {
    @Test func wifiProceedsRegardlessOfCellularSetting() {
        for allow in [false, true] {
            #expect(StateDownloadPolicy.decide(isConnected: true, isExpensive: false, allowCellular: allow, consentGiven: false) == .proceed)
        }
    }

    @Test func cellularWithAllowOffIsBlockedEvenWithConsent() {
        #expect(StateDownloadPolicy.decide(isConnected: true, isExpensive: true, allowCellular: false, consentGiven: false) == .blocked(.cellularNotAllowed))
        #expect(StateDownloadPolicy.decide(isConnected: true, isExpensive: true, allowCellular: false, consentGiven: true) == .blocked(.cellularNotAllowed))
    }

    @Test func cellularWithAllowOnNeedsConsentThenProceeds() {
        #expect(StateDownloadPolicy.decide(isConnected: true, isExpensive: true, allowCellular: true, consentGiven: false) == .needsCellularConsent)
        #expect(StateDownloadPolicy.decide(isConnected: true, isExpensive: true, allowCellular: true, consentGiven: true) == .proceed)
    }

    @Test func offlineIsBlocked() {
        #expect(StateDownloadPolicy.decide(isConnected: false, isExpensive: false, allowCellular: true, consentGiven: true) == .blocked(.offline))
        #expect(!NetworkDecision.blocked(.offline).allowsStart)
        #expect(!NetworkDecision.needsCellularConsent.allowsStart)
    }
}

struct StateDownloadStorageCheckTests {
    private let mb: Int64 = 1_000_000

    @Test func insufficientBelowOnePointFiveTimesPlusHeadroom() {
        // estimate 100 MB -> needed 150 + 50 = 200 MB
        let low = StorageCheck.evaluate(freeBytes: 199 * mb, remainingOnDeviceEstimate: Int(100 * mb))
        #expect(low == .insufficient(neededBytes: 200 * mb))
        #expect(!low.allowsStart)
    }

    @Test func warningWhenLittleWouldRemain() {
        // free 400 MB, estimate 100 MB -> needed 200 ok, but 300 MB left < 500 MB
        let result = StorageCheck.evaluate(freeBytes: 400 * mb, remainingOnDeviceEstimate: Int(100 * mb))
        #expect(result == .lowSpaceWarning(freeAfter: 300 * mb))
        #expect(result.allowsStart)
    }

    @Test func okWhenPlentyOfSpaceAndBoundaryIsExact() {
        #expect(StorageCheck.evaluate(freeBytes: 5_000 * mb, remainingOnDeviceEstimate: Int(100 * mb)) == .ok)
        #expect(StorageCheck.evaluate(freeBytes: 600 * mb, remainingOnDeviceEstimate: Int(100 * mb)) == .ok)
        #expect(StorageCheck.evaluate(freeBytes: 599 * mb, remainingOnDeviceEstimate: Int(100 * mb)) == .lowSpaceWarning(freeAfter: 499 * mb))
        #expect(StorageCheck.evaluate(freeBytes: 200 * mb, remainingOnDeviceEstimate: Int(100 * mb)).allowsStart)
    }

    @Test func resumeUsesOnlyTheRemainingEstimate() {
        let free = 300 * mb
        #expect(!StorageCheck.evaluate(freeBytes: free, remainingOnDeviceEstimate: Int(200 * mb)).allowsStart)
        #expect(StorageCheck.evaluate(freeBytes: free, remainingOnDeviceEstimate: Int(20 * mb)).allowsStart)
    }

    @Test func unreadableFreeSpaceIsTreatedAsOK() {
        #expect(StorageCheck.evaluate(freeBytes: nil, remainingOnDeviceEstimate: Int(10_000 * mb)) == .ok)
    }
}

struct StateRemovalPlannerTests {
    private func claim(_ state: String) -> Claim {
        Claim(stateKey: state, bytes: 10, verifiedAt: Date())
    }

    @Test func deletesOnlyIDsClaimedByThisState() {
        let claims = ["ca1": claim("California"), "ca2": claim("California"), "co1": claim("Colorado")]
        let plan = StateRemovalPlanner.plan(stateKey: "California", claims: claims, preexisting: [], protected: [])
        #expect(plan.delete == ["ca1", "ca2"])
        #expect(plan.keptProtected.isEmpty)
    }

    @Test func neverDeletesFavoritesOpenedHeldRecordingOrPreexistingAreas() {
        let claims = Dictionary(uniqueKeysWithValues: ["fav", "opened", "held", "rec", "pre", "plain"].map { ($0, claim("California")) })
        let protected = StateProtection.protectedIDs(
            favorites: ["fav"], opened: ["opened"], userHeld: ["held"], activeRecordingAreaID: "rec"
        )
        let plan = StateRemovalPlanner.plan(stateKey: "California", claims: claims, preexisting: ["pre"], protected: protected)
        #expect(plan.delete == ["plain"])
        #expect(Set(plan.keptProtected) == ["fav", "opened", "held", "rec", "pre"])
    }

    @Test func unclaimedAndOtherStateAreasAreNeverInThePlan() {
        // "manual" is a California area on disk with no claim; "co1" is Colorado's.
        let claims = ["ca1": claim("California"), "co1": claim("Colorado")]
        let plan = StateRemovalPlanner.plan(stateKey: "California", claims: claims, preexisting: [], protected: [])
        #expect(!plan.delete.contains("manual"))
        #expect(!plan.delete.contains("co1"))
        #expect(!plan.keptProtected.contains("co1"))
    }

    @Test func purgedDiskStillPlansOnlyClaims() {
        let plan = StateRemovalPlanner.plan(stateKey: "Utah", claims: [:], preexisting: ["a"], protected: ["b"])
        #expect(plan.delete.isEmpty && plan.keptProtected.isEmpty)
    }

    @Test func protectionIncludesCanonicalTwinOfAProtectedAlias() {
        let protected = StateProtection.protectedIDs(
            favorites: ["twin"], opened: [], userHeld: [], activeRecordingAreaID: nil,
            canonical: { $0 == "twin" ? "canon" : $0 }
        )
        #expect(protected == ["twin", "canon"])
    }

    @Test func userHeldRoundTripsThroughDefaults() throws {
        let suite = "StateRemovalPlannerTests-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        UserHeldAreas.add(["b", "a"], defaults: defaults)
        UserHeldAreas.add(["a", "c"], defaults: defaults)
        #expect(UserHeldAreas.all(defaults: defaults) == ["a", "b", "c"])
    }
}

struct StateDownloadStoreTests {
    private func makeStore() -> (StateDownloadStore, URL) {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("StateDownloadStoreTests-\(UUID().uuidString)", isDirectory: true)
        return (StateDownloadStore(directory: dir), dir)
    }

    @Test func ledgerRoundTrips() {
        let (store, dir) = makeStore()
        defer { try? FileManager.default.removeItem(at: dir) }
        var ledger = StateLedger(stateKey: "New Mexico")
        ledger.record.memberIDs = ["a", "b"]
        ledger.record.status = .paused
        ledger.record.failed = ["b": .http(404)]
        ledger.claims["a"] = Claim(stateKey: "New Mexico", bytes: 5, verifiedAt: Date(timeIntervalSince1970: 1))
        ledger.etags["a"] = "\"abc\""
        #expect(store.save(ledger))
        let loaded = store.load(stateKey: "New Mexico")
        #expect(loaded == ledger)
    }

    @Test func missingCorruptAndUnknownSchemaReadAsFreshIdle() throws {
        let (store, dir) = makeStore()
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(store.load(stateKey: "Utah") == StateLedger(stateKey: "Utah"))

        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data("not json".utf8).write(to: dir.appendingPathComponent("utah.json"))
        #expect(store.load(stateKey: "Utah") == StateLedger(stateKey: "Utah"))

        var future = StateLedger(stateKey: "Utah")
        future.schemaVersion = 99
        future.claims["a"] = Claim(stateKey: "Utah", bytes: 1, verifiedAt: Date())
        try JSONEncoder().encode(future).write(to: dir.appendingPathComponent("utah.json"))
        let loaded = store.load(stateKey: "Utah")
        #expect(loaded.claims.isEmpty)
        #expect(loaded.record.status == .idle)
    }

    @Test func clearAllForgetsEveryLedger() {
        let (store, dir) = makeStore()
        defer { try? FileManager.default.removeItem(at: dir) }
        store.save(StateLedger(stateKey: "Utah"))
        var ledger = StateLedger(stateKey: "Ohio")
        ledger.claims["x"] = Claim(stateKey: "Ohio", bytes: 1, verifiedAt: Date())
        store.save(ledger)
        store.clearAll()
        #expect(store.load(stateKey: "Ohio").claims.isEmpty)
    }

    @Test func crashWindowRecoveryClaimsOnlyProvablyOwnFiles() {
        let recovered = StateClaimRecovery.recoverable(
            members: ["new", "pre", "claimed", "fav", "missing"],
            availableOnDisk: ["new", "pre", "claimed", "fav"],
            preexisting: ["pre"],
            claims: ["claimed": Claim(stateKey: "Utah", bytes: 1, verifiedAt: Date())],
            protected: ["fav"]
        )
        #expect(recovered == ["new"])
    }

    @Test func stateSlugIsStableAndFileSafe() {
        #expect(StateCatalog.slug("New Mexico") == "new-mexico")
        #expect(StateCatalog.slug("District of Columbia") == "district-of-columbia")
    }
}
