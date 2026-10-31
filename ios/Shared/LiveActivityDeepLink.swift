import Foundation

/// Tap target for the Live Activity. Shared so the widget and the app agree
/// on the exact URL.
enum LiveActivityDeepLink {
    enum Destination: Equatable { case activeHike }

    static let scheme = "trekdex"
    static let host = "hike"
    static let activePath = "/active"

    static var activeHikeURL: URL { URL(string: "\(scheme)://\(host)\(activePath)")! }

    static func parse(_ url: URL) -> Destination? {
        guard url.scheme?.lowercased() == scheme,
              url.host?.lowercased() == host,
              url.path == activePath else { return nil }
        return .activeHike
    }
}
