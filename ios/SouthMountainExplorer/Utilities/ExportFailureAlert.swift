import SwiftUI
import OSLog

private let log = Logger(subsystem: "com.trekdex.app", category: "export")

/// Says out loud that a file export failed.
///
/// `GpxExport.temporaryFile` throws, and all four callers used to swallow it
/// with a `// Silent — user can retry` comment. Nothing was retryable from the
/// user's side: they tapped "Export as GPX", no share sheet appeared, and the
/// app gave them no reason and no next step. A failure the user can see is the
/// minimum; a failure they can act on is the point.
///
/// The message is human copy, not the thrown error's string — the same reason
/// AreaView's load-failure screen stopped echoing "Fetch already in progress but
/// returned no data." The real error goes to the log, which rides along in a
/// Send Diagnostics bundle.
extension View {
    /// - Parameter failure: set to a short human sentence to raise the alert;
    ///   the alert clears it on dismiss.
    func exportFailureAlert(_ failure: Binding<String?>) -> some View {
        alert(
            "Couldn't create the file",
            isPresented: Binding(
                get: { failure.wrappedValue != nil },
                set: { if !$0 { failure.wrappedValue = nil } }
            )
        ) {
            Button("OK", role: .cancel) { failure.wrappedValue = nil }
        } message: {
            Text(failure.wrappedValue ?? "")
        }
    }
}

/// What to show the user, and what to write to the log, when an export throws.
///
/// Running out of space is the failure people can actually do something about,
/// so it gets its own sentence instead of being folded into a generic one.
enum ExportFailure {
    static func message(for error: Error, what: String) -> String {
        log.error("exportFailed what=\(what, privacy: .public) error=\(String(describing: error), privacy: .public)")
        let ns = error as NSError
        if ns.domain == NSCocoaErrorDomain, ns.code == NSFileWriteOutOfSpaceError {
            return "Your iPhone is out of storage, so \(what) couldn't be written. Free up some space and try again."
        }
        return "Something went wrong writing \(what). Try again in a moment."
    }
}
