import SwiftUI

struct ContentView: View {
    @Environment(AuthService.self) private var auth
    @Environment(RecordingService.self) private var recording
    @Environment(AreaDataService.self) private var areas

    @AppStorage("summit:onboarded") private var onboarded = false

    @State private var showStopConfirm = false

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
        .safeAreaInset(edge: .top, spacing: 0) {
            if let rec = recording.activeRecording {
                ActiveRecordingBanner(
                    areaName: areaName(for: rec.areaId),
                    distanceMi: rec.distanceMi,
                    startedAt: rec.startedAt,
                    onStop: { showStopConfirm = true }
                )
            }
        }
        .confirmationDialog(
            "Stop and save this hike?",
            isPresented: $showStopConfirm,
            titleVisibility: .visible
        ) {
            Button("Stop & Save", role: .destructive) {
                Task { await stopActiveRecording() }
            }
            Button("Keep Recording", role: .cancel) { }
        }
        .fullScreenCover(isPresented: .constant(!onboarded)) {
            OnboardingView()
                .onDisappear { onboarded = true }
        }
    }

    /// Best-effort name resolution: cached Area first (has full trails),
    /// then the lighter AreaSummary list, then a generic fallback. Keeps the
    /// banner readable even before AreaDataService has hydrated the cache.
    private func areaName(for id: String) -> String {
        if let cached = areas.cachedArea(id: id)?.name { return cached }
        if let summary = areas.summaries.first(where: { $0.id == id })?.name { return summary }
        return "Hiking"
    }

    private func stopActiveRecording() async {
        guard let rec = recording.activeRecording else { return }
        // Pull trails from cache so coverage merges still work; fall back to
        // an async fetch if the area hasn't been opened this session.
        // (Split into an if/else because `??` takes an autoclosure that
        // can't host an `await`.)
        let trails: [Trail]
        if let cached = areas.cachedArea(id: rec.areaId) {
            trails = cached.trails
        } else {
            trails = (await areas.area(id: rec.areaId))?.trails ?? []
        }
        _ = await recording.stopRecording(trails: trails)
    }
}
