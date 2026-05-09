import SwiftUI

struct ContentView: View {
    @Environment(AuthService.self) private var auth
    @Environment(RecordingService.self) private var recording

    @AppStorage("summit:onboarded") private var onboarded = false
    @AppStorage("summit:theme") private var theme: AppTheme = .system

    var body: some View {
        TabView {
            Tab("Explore", systemImage: "mountain.2.fill") {
                HomeView()
            }
            Tab("Browse", systemImage: "magnifyingglass") {
                BrowseView()
            }
            Tab("History", systemImage: "clock.fill") {
                HistoryView()
            }
            Tab("Settings", systemImage: "gearshape.fill") {
                SettingsView()
            }
        }
        // iOS 26 — tab bar automatically gets Liquid Glass styling
        .tabViewStyle(.sidebarAdaptable)
        .preferredColorScheme(theme.colorScheme)
        .fullScreenCover(isPresented: .constant(!onboarded)) {
            OnboardingView()
                .onDisappear { onboarded = true }
        }
    }
}
