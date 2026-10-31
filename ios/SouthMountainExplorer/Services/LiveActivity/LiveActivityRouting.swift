import Foundation

/// Where a Live Activity tap should land, mirroring the active-recording
/// banner's `onTap` (walk -> walk cover, otherwise the primary area).
enum LiveActivityRouting {
    enum Target: Equatable {
        case walk
        case area(String)
        case none
    }

    static func target(for recording: ActiveRecording?) -> Target {
        guard let recording else { return .none }
        return recording.mode == .walk ? .walk : .area(recording.areaId)
    }
}
