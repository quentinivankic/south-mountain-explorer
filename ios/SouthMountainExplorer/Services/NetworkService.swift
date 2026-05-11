import Foundation
import Network
import Observation

@MainActor
@Observable
final class NetworkService {
    static let shared = NetworkService()

    private(set) var isConnected: Bool = false
    /// True when the path is reachable but flagged expensive — cellular,
    /// personal hotspot, etc. Used to gate background prefetch so we
    /// don't spend a user's data plan without consent.
    private(set) var isExpensive: Bool = false

    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "com.southmountainexplorer.network-monitor")

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let connected = path.status == .satisfied
            let expensive = path.isExpensive
            Task { @MainActor in
                self?.isConnected = connected
                self?.isExpensive = expensive
            }
        }
        monitor.start(queue: queue)
    }

    /// Wi-Fi (or wired) and reachable. Safe for background data downloads.
    var isOnUnmeteredNetwork: Bool {
        isConnected && !isExpensive
    }
}
