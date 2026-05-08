import SwiftUI

struct ContentView: View {
    @Environment(AuthService.self) private var auth
    @Environment(RecordingService.self) private var recording

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
    }
}
