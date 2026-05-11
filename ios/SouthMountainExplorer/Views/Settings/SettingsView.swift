import SwiftUI

private struct UserStats: Equatable {
    var hikeCount: Int
    var totalMi: Double
    var trailsCompleted: Int
    var areasExplored: Int
}

private let feedbackURL = URL(string: "https://github.com/quentinivankic/south-mountain-explorer/issues/new")!

struct SettingsView: View {
    @Environment(AuthService.self) private var auth
    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(FavoritesService.self) private var favorites
    @Environment(RecordingService.self) private var recording

    @AppStorage("summit:theme") private var theme: AppTheme = .system

    @State private var showSignIn = false
    @State private var showResetConfirm = false
    @State private var showSignOutConfirm = false
    @State private var showRefreshConfirm = false
    @State private var trailDataRefreshed = false
    @State private var stats: UserStats? = nil
    /// Active "Download for Offline" progress as `(completed, total)`.
    /// Non-nil while the prefetch task is running so the button label can
    /// show "Downloading 2 of 5…". Cleared a beat after completion so the
    /// user sees the final state briefly before it reverts.
    @State private var downloadProgress: (Int, Int)? = nil
    @State private var showDownloadConfirm = false

    var body: some View {
        NavigationStack {
            List {
                Section("Account") {
                    if auth.isSignedIn {
                        HStack {
                            Image(systemName: "person.circle.fill")
                                .foregroundStyle(.green)
                                .font(.title2)
                            VStack(alignment: .leading) {
                                Text("Signed in with Apple")
                                    .fontWeight(.medium)
                                Text(auth.userId ?? "")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        Button(role: .destructive) {
                            showSignOutConfirm = true
                        } label: {
                            Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                        }
                        .confirmationDialog("Sign out of your account?", isPresented: $showSignOutConfirm) {
                            Button("Sign Out", role: .destructive) {
                                auth.signOut()
                            }
                        }
                    } else {
                        Button {
                            showSignIn = true
                        } label: {
                            Label("Sign in with Apple", systemImage: "apple.logo")
                        }
                    }
                }

                Section("Your Activity") {
                    if let s = stats {
                        statsBlock(s)
                    } else {
                        HStack {
                            ProgressView()
                            Text("Tallying your hikes…")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("Appearance") {
                    Picker("Theme", selection: $theme) {
                        ForEach(AppTheme.allCases) { theme in
                            Text(theme.label).tag(theme)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                Section("Trail Data") {
                    Button {
                        showRefreshConfirm = true
                    } label: {
                        Label(
                            trailDataRefreshed ? "Trail Data Cleared" : "Refresh Trail Data",
                            systemImage: trailDataRefreshed ? "checkmark.circle" : "arrow.clockwise"
                        )
                    }
                    .disabled(trailDataRefreshed)
                    .confirmationDialog(
                        "Refresh trail data?",
                        isPresented: $showRefreshConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Clear & Refresh") {
                            AreaDataService.shared.clearAreaCache()
                            trailDataRefreshed = true
                            // Re-enable the button after a brief
                            // confirmation window so the user can refresh
                            // again later in the same session.
                            Task {
                                try? await Task.sleep(for: .seconds(3))
                                trailDataRefreshed = false
                            }
                        }
                        Button("Cancel", role: .cancel) { }
                    } message: {
                        Text("Clears cached trail data so fresh data is fetched the next time you open each area. Hike history is preserved. Trail completions stay tied to specific trails — if a trail's underlying ID changes upstream, that completion is dropped automatically when you reopen the area.")
                    }

                    Button {
                        showDownloadConfirm = true
                    } label: {
                        if let p = downloadProgress {
                            Label("Downloading \(p.0) of \(p.1)…", systemImage: "arrow.down.circle")
                        } else {
                            Label("Download for Offline", systemImage: "arrow.down.circle")
                        }
                    }
                    .disabled(downloadProgress != nil)
                    .confirmationDialog(
                        "Download favorites and recent areas for offline use?",
                        isPresented: $showDownloadConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Download") {
                            Task {
                                downloadProgress = (0, 0)
                                await AreaDataService.shared.prefetchOffline { completed, total in
                                    downloadProgress = (completed, total)
                                }
                                // Hold the final "(N of N)" reading for a
                                // beat so the user sees the result land
                                // instead of the label snapping back
                                // immediately.
                                try? await Task.sleep(for: .seconds(1.5))
                                downloadProgress = nil
                            }
                        }
                        Button("Cancel", role: .cancel) { }
                    } message: {
                        Text("Saves trail data for your favorited and recently-opened areas so you can open them without a signal. Skips anything that's already up to date.")
                    }
                }

                Section("Data") {
                    Button(role: .destructive) {
                        showResetConfirm = true
                    } label: {
                        Label("Reset All Progress", systemImage: "trash")
                    }
                    .confirmationDialog(
                        "This will delete all trail completions, coverage data, and favourites from this device.",
                        isPresented: $showResetConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Reset Everything", role: .destructive) {
                            Task { await resetAll() }
                        }
                    }
                }

                Section("Feedback") {
                    Link(destination: feedbackURL) {
                        Label("Send Feedback", systemImage: "envelope")
                    }
                }

                Section("About") {
                    LabeledContent("Version", value: appVersion)
                    LabeledContent("Build", value: buildNumber)
                }
            }
            .navigationTitle("Settings")
        }
        .sheet(isPresented: $showSignIn) {
            AuthView()
        }
        // Re-run when the recording state flips (recording starts or stops)
        // so completing a hike then opening Settings shows fresh numbers
        // instead of whatever was cached on the last view appearance.
        .task(id: recording.activeRecording == nil) { await refreshStats() }
    }

    private func statsBlock(_ s: UserStats) -> some View {
        HStack {
            statColumn(value: "\(s.hikeCount)", label: s.hikeCount == 1 ? "hike" : "hikes")
            Divider().frame(height: 36)
            statColumn(value: String(format: "%.1f", s.totalMi), label: "miles")
            Divider().frame(height: 36)
            statColumn(value: "\(s.trailsCompleted)", label: s.trailsCompleted == 1 ? "trail" : "trails")
            Divider().frame(height: 36)
            statColumn(value: "\(s.areasExplored)", label: s.areasExplored == 1 ? "area" : "areas")
        }
        .padding(.vertical, 4)
    }

    private func statColumn(value: String, label: String) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.headline.monospacedDigit())
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    /// Roll up history + completions into the vanity stats block. Counts a
    /// (areaId, trailId) pair as a single completion across both sources so
    /// auto-completes from a hike + manual toggles for the same trail don't
    /// double-count.
    private func refreshStats() async {
        let history = await recording.loadHistory()

        var completedPairs = Set<String>()
        for (areaId, trails) in progress.completions {
            for trailId in trails.keys {
                completedPairs.insert("\(areaId):\(trailId)")
            }
        }
        for hike in history {
            for trailId in hike.completedTrailIds {
                completedPairs.insert("\(hike.areaId):\(trailId)")
            }
        }

        var areas = Set<String>()
        for hike in history { areas.insert(hike.areaId) }
        for (areaId, trails) in progress.completions where !trails.isEmpty {
            areas.insert(areaId)
        }

        stats = UserStats(
            hikeCount: history.count,
            totalMi: history.reduce(0.0) { $0 + $1.distanceMi },
            trailsCompleted: completedPairs.count,
            areasExplored: areas.count
        )
    }

    private func resetAll() async {
        let keys = ["summit:completed", "summit:coverage", "summit:favorites", "summit:active-recording", "location.lat", "location.lon"]
        for key in keys { UserDefaults.standard.removeObject(forKey: key) }
        if let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first {
            try? FileManager.default.removeItem(at: caches.appendingPathComponent("areas"))
        }
    }

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
    }

    private var buildNumber: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—"
    }
}
