import Foundation

/// Whether a state download may use the network right now.
enum NetworkDecision: Equatable, Sendable {
    case proceed
    /// On an expensive path with cellular allowed but not yet confirmed.
    case needsCellularConsent
    case blocked(BlockReason)

    enum BlockReason: Equatable, Sendable {
        case offline
        case cellularNotAllowed
    }

    var allowsStart: Bool { self == .proceed }
}

enum StateDownloadPolicy {
    static func decide(
        isConnected: Bool,
        isExpensive: Bool,
        allowCellular: Bool,
        consentGiven: Bool
    ) -> NetworkDecision {
        guard isConnected else { return .blocked(.offline) }
        guard isExpensive else { return .proceed }
        guard allowCellular else { return .blocked(.cellularNotAllowed) }
        return consentGiven ? .proceed : .needsCellularConsent
    }
}

enum StorageCheckResult: Equatable, Sendable {
    case ok
    /// Enough to start, but little would be left afterwards.
    case lowSpaceWarning(freeAfter: Int64)
    case insufficient(neededBytes: Int64)

    var allowsStart: Bool {
        if case .insufficient = self { return false }
        return true
    }
}

enum StorageCheck {
    static let safetyMultiplier = 1.5
    static let fixedHeadroomBytes: Int64 = 50 * 1_000_000
    static let warningThresholdBytes: Int64 = 500 * 1_000_000

    /// `freeBytes == nil` means free space could not be read: treated as OK.
    static func evaluate(freeBytes: Int64?, remainingOnDeviceEstimate: Int) -> StorageCheckResult {
        guard let freeBytes else { return .ok }
        let estimate = Int64(max(remainingOnDeviceEstimate, 0))
        let needed = Int64((Double(estimate) * safetyMultiplier).rounded()) + fixedHeadroomBytes
        if freeBytes < needed { return .insufficient(neededBytes: needed) }
        let freeAfter = freeBytes - estimate
        if freeAfter < warningThresholdBytes { return .lowSpaceWarning(freeAfter: freeAfter) }
        return .ok
    }

    /// Free space the system will let the app use for important files.
    static func liveFreeBytes() -> Int64? {
        let url = URL(fileURLWithPath: NSHomeDirectory())
        let values = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage
    }
}
