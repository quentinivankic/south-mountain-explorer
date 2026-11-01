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
            if let spoken = state.nextTurnSpoken {
                parts.append("Next: \(spoken)")
            }
            if let end = state.estimatedTrailEndAt {
                parts.append("Finish around \(HikeActivityClock.time(end))")
            }
            if let back = state.estimatedBackAt {
                parts.append("Back by around \(HikeActivityClock.time(back))")
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

/// Clock-time wording for the arrival estimates. Follows the user's locale and
/// 12/24-hour setting; `locale` and `timeZone` are injectable for tests.
enum HikeActivityClock {
    static func time(_ date: Date, locale: Locale = .autoupdatingCurrent,
                     timeZone: TimeZone = .autoupdatingCurrent) -> String {
        date.formatted(Date.FormatStyle(date: .omitted, time: .shortened,
                                        locale: locale, timeZone: timeZone))
    }

    /// "Finish ~4:12 PM" (approximate) or "Finish 4:12 PM".
    static func label(_ prefix: String, _ date: Date, approximate: Bool?,
                      locale: Locale = .autoupdatingCurrent,
                      timeZone: TimeZone = .autoupdatingCurrent) -> String {
        let mark = approximate == true ? "~" : ""
        return "\(prefix) \(mark)\(time(date, locale: locale, timeZone: timeZone))"
    }
}
