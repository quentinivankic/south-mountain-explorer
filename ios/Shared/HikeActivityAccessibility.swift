import Foundation

/// Pure VoiceOver label composer for the Lock Screen / expanded activity.
/// The ticking elapsed time is exposed separately as an accessibility value
/// so it does not go stale inside this static string.
enum HikeActivityAccessibility {
    static func label(for state: HikeActivityAttributes.ContentState) -> String {
        var parts: [String] = []
        if state.status == .finished {
            parts.append("\(state.title), hike saved")
        } else {
            parts.append("\(state.status == .recording ? "Recording" : "Hike") \(state.title)")
        }
        parts.append(state.distanceText)
        if state.status != .recording && state.status != .finished {
            parts.append(state.status.spokenText)
        }
        if let progress = state.progress, let length = state.trailLengthText {
            parts.append("\(Int((progress * 100).rounded())) percent of \(length) trail")
        }
        if state.status != .finished {
            if let end = state.estimatedTrailEndAt {
                parts.append("trail end about \(end.formatted(date: .omitted, time: .shortened))")
            }
            if let back = state.estimatedBackAt {
                parts.append("back by about \(back.formatted(date: .omitted, time: .shortened))")
            }
        }
        return parts.joined(separator: ", ")
    }

    static func compactLabel(for status: HikeActivityAttributes.ContentState.Status) -> String {
        switch status {
        case .recording: return "Hike recording"
        case .finished: return "Hike saved"
        default: return "Hike recording, \(status.spokenText)"
        }
    }
}
