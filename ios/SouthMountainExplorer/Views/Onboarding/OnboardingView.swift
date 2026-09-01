import SwiftUI

/// First-launch onboarding. A swipeable page-style walkthrough — five
/// pages, each focused on one capability the user just got, ending with
/// the location ask. The Stats
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
    @Environment(LocationService.self) private var location

    /// Called when the walkthrough finishes. Onboarding is rendered as a
    /// top-level overlay rather than presented (see `ContentView`), so there
    /// is no cover to close — the host flips its own flag.
    let onFinish: () -> Void

    @State private var selectedPage = 0
    /// Set while the system permission alert is up, so the authorization
    /// callback finishes onboarding — whichever way the user answers.
    @State private var awaitingPermission = false

    private static let pageCount = 5

    /// The last page asks for location. It is last so the ask arrives with a
    /// reason already on screen, which is the whole point of putting it here
    /// rather than firing a bare system alert at launch.
    private var onLastPage: Bool { selectedPage == Self.pageCount - 1 }

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
                page(icon: "location.fill",
                     title: "Find trails near you",
                     body: "TrekDex uses your location to show the parks around you, draw where you are on the map, and record a hike while your phone is in your pocket. It stays on your device.")
                    .tag(4)
            }
            .tabViewStyle(.page(indexDisplayMode: .always))
            .indexViewStyle(.page(backgroundDisplayMode: .always))

            Button {
                if !onLastPage {
                    withAnimation { selectedPage += 1 }
                } else if location.isAuthorized || location.isDenied {
                    // Nothing left to ask: granted already, or refused in a
                    // way `requestWhenInUseAuthorization` cannot reopen.
                    onFinish()
                } else {
                    awaitingPermission = true
                    location.requestPermission()
                }
            } label: {
                Text(ctaTitle)
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .padding(.horizontal, 24)

            // Never a dead end: the ask is worth making once, not worth
            // trapping someone who does not want to answer it.
            Button("Not now") { onFinish() }
                .font(.subheadline)
                .padding(.top, 12)
                .opacity(onLastPage && !location.isAuthorized ? 1 : 0)
                .disabled(!onLastPage || location.isAuthorized)
                .accessibilityHidden(!onLastPage || location.isAuthorized)

            Color.clear.frame(height: 20)
        }
        .onChange(of: location.authorizationStatus) { _, _ in
            // Fires once the user answers the system alert, either way.
            if awaitingPermission {
                awaitingPermission = false
                onFinish()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var ctaTitle: String {
        guard onLastPage else { return "Continue" }
        return location.isAuthorized || location.isDenied ? "Get Started" : "Enable Location"
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
