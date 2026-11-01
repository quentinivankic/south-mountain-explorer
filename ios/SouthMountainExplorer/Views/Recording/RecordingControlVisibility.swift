import Foundation
import Observation

/// Tracks contextual recording controls that are currently mounted. The root
/// banner uses this registry to suppress only its duplicate Stop action while
/// preserving the global fallback everywhere else.
@MainActor
@Observable
final class RecordingControlVisibility {
    struct Token: Hashable, Sendable {
        fileprivate let id: UUID

        init() {
            id = UUID()
        }
    }

    private var tokens: Set<Token> = []
    private(set) var localControlCount = 0

    var hasLocalControls: Bool { localControlCount > 0 }

    func acquire(_ token: Token) {
        tokens.insert(token)
        localControlCount = tokens.count
    }

    func release(_ token: Token) {
        tokens.remove(token)
        localControlCount = tokens.count
    }
}
