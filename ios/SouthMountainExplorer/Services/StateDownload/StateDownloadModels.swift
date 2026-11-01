import Foundation

/// Why one area failed to download. Persisted, so keep cases stable.
enum FailureKind: Codable, Equatable, Hashable, Sendable {
    case http(Int)
    case network
    case timeout
    case invalidPayload
    case storage

    /// Worth one automatic retry (and counted toward the stall breaker).
    var isTransient: Bool {
        switch self {
        case .network, .timeout: return true
        case .http(let code): return code >= 500 || code == 429
        case .invalidPayload, .storage: return false
        }
    }

    var shortReason: String {
        switch self {
        case .http(let code): return "HTTP \(code)"
        case .network: return "network error"
        case .timeout: return "timed out"
        case .invalidPayload: return "invalid data"
        case .storage: return "couldn't save"
        }
    }
}

/// Result of fetching one area. `.cancelled` and `.policyBlocked` are neither
/// success nor failure: the id simply stays pending.
enum AreaOutcome: Equatable, Sendable {
    case stored(bytes: Int, etag: String?)
    case unchanged
    case failed(FailureKind)
    case cancelled
    case policyBlocked
}

enum StateDownloadStatus: String, Codable, Sendable {
    case idle
    case running
    case paused
    case waitingForNetwork
    case complete
}

/// Persisted per-scope job state. Disk truth (valid files) always wins over
/// this record when deciding what is on the device.
struct StateRecord: Codable, Equatable, Sendable {
    var stateKey: String
    var status: StateDownloadStatus = .idle
    var memberIDs: [String] = []
    /// Member files that already existed (and verified) before this feature
    /// touched them. Never claimed, never removed by this feature.
    var preexistingIDs: Set<String> = []
    var failed: [String: FailureKind] = [:]
    var startedAt: Date?
    var completedAt: Date?
    var lastUpdatedAt: Date?
    var verifiedCount: Int = 0
    var onDeviceBytes: Int = 0

    init(stateKey: String) { self.stateKey = stateKey }
}

/// "This feature wrote this area's file for this scope."
struct Claim: Codable, Equatable, Sendable {
    var stateKey: String
    var bytes: Int
    var verifiedAt: Date
}

/// Whole persisted file for one scope.
struct StateLedger: Codable, Equatable, Sendable {
    var schemaVersion: Int = StateLedger.currentSchemaVersion
    var record: StateRecord
    var claims: [String: Claim] = [:]
    /// Opaque ETags of area files this feature fetched, for conditional update.
    var etags: [String: String] = [:]

    static let currentSchemaVersion = 1

    init(stateKey: String) { record = StateRecord(stateKey: stateKey) }
}
