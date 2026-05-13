import SwiftUI
import UIKit

/// SwiftUI wrapper around `UIActivityViewController` so we can
/// share a file URL (the diagnostics bundle) from a `.sheet`.
///
/// `ShareLink` (iOS 16+) would also work for static URLs, but the
/// diagnostics URL is produced asynchronously by
/// `DiagnosticsService.exportBundle`, so we need a sheet we can
/// present once the URL is ready.
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {
        // No-op — the activity items are baked in at construction.
    }
}
