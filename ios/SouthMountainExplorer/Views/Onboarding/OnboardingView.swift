import SwiftUI

/// First-launch onboarding. A swipeable page-style walkthrough — four
/// pages, each focused on one capability the user just got. The Stats
/// dashboard and live recording features (elevation strip, pace stats)
/// landed after the previous single-screen onboarding was written, so
/// they get explicit shout-outs here so the user knows what's available.
///
/// CTA at the bottom is persistent: "Continue" on every page except
/// the last, "Get Started" at the end. Tapping advances + animates;
/// the page indicator is visible throughout so the user knows how far
/// they have to go. Re-presented after Reset All Progress (the
/// `onboarded` AppStorage flag is cleared in DataBackupManager).
struct OnboardingView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var selectedPage = 0

    private static let pageCount = 4

    var body: some View {
        VStack(spacing: 0) {
            TabView(selection: $selectedPage) {
                welcomePage.tag(0)
                discoverPage.tag(1)
                page(icon: "record.circle.fill",
                     title: "Record",
                     body: "GPS-track your hikes — works in the background while your phone's in your pocket. Live elevation and pace update as you climb.")
                    .tag(2)
                page(icon: "checkmark.seal.fill",
                     title: "Complete",
                     body: "Trails you finish turn cyan on the map. Watch your progress fill in over time, area by area.")
                    .tag(3)
            }
            .tabViewStyle(.page(indexDisplayMode: .always))
            .indexViewStyle(.page(backgroundDisplayMode: .always))

            Button {
                if selectedPage < Self.pageCount - 1 {
                    withAnimation { selectedPage += 1 }
                } else {
                    dismiss()
                }
            } label: {
                Text(selectedPage < Self.pageCount - 1 ? "Continue" : "Get Started")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .padding(.horizontal, 24)
            .padding(.bottom, 32)
        }
        .interactiveDismissDisabled()
    }

    // MARK: - Pages

    private var welcomePage: some View {
        VStack(spacing: 28) {
            // Hero: South Mountain's trails lighting cyan one by one. The
            // sentence leads with the brand, so no separate "TrekDex" wordmark.
            OnboardingTrailAnimation()
                .frame(maxWidth: .infinity)
                .frame(height: 200)

            Text("TrekDex helps you track and complete every trail in your favorite park")
                .font(.title2.weight(.semibold))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 28)
        }
        .padding(.horizontal, 24)
    }

    /// "Discover" page: a gallery of real park silhouettes in place of a stock
    /// SF Symbol, keeping the same title and body copy.
    private var discoverPage: some View {
        VStack(spacing: 20) {
            OnboardingAreaGallery()
                .frame(maxWidth: 460)
                .padding(.horizontal, 8)

            Text("Discover")
                .font(.largeTitle.weight(.bold))

            Text("Browse hiking areas with map previews, search, and trail counts. The Stats tab tracks your overall progress as you build out a hiking history.")
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
                .frame(maxWidth: 480)
        }
        .padding(.horizontal, 24)
    }

    /// Shared layout for the three feature pages. `selectedPage` drives
    /// the symbol effect's trigger so the icon bounces every time the
    /// user swipes to a new page (any tag changes the value SwiftUI
    /// sees), giving a small visual reward for progressing.
    private func page(icon: String, title: String, body: String) -> some View {
        VStack(spacing: 20) {
            Image(systemName: icon)
                .font(.system(size: 96))
                .foregroundStyle(.cyan)
                .symbolEffect(.bounce, value: selectedPage)

            Text(title)
                .font(.largeTitle.weight(.bold))

            Text(body)
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
                .frame(maxWidth: 480)
        }
        .padding(.horizontal, 24)
    }
}
