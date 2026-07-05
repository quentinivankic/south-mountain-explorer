import SwiftUI
import UniformTypeIdentifiers

private struct UserStats: Equatable {
    var hikeCount: Int
    var totalMi: Double
    var trailsCompleted: Int
    var areasExplored: Int
}

/// Privacy policy, hosted at trekdex.app. Pinned here so the Privacy
/// Policy row in Settings → About links to the authoritative copy.
/// The SAME URL must go into App Store Connect's Privacy Policy URL
/// field at submission — keep them in sync.
private let privacyPolicyURL: URL? = URL(string: "https://trekdex.app/privacy-policy")

/// Terms of Service, hosted at trekdex.app. Surfaced in Settings →
/// About next to the privacy policy.
private let termsOfServiceURL: URL? = URL(string: "https://trekdex.app/terms-of-service")

/// OpenStreetMap copyright / licence page. The ODbL requires the
/// "© OpenStreetMap contributors" credit to link here. Force-unwrapped
/// — it's a compile-time constant literal that always parses.
private let osmCopyrightURL = URL(string: "https://www.openstreetmap.org/copyright")!

/// Small `Identifiable` wrapper around a `URL` so SwiftUI views
/// can drive a `.sheet(item:)` off file URLs. `URL` itself doesn't
/// conform to `Identifiable`, and `sheet(item:)` needs an identity
/// to know when to re-present. Shared by Settings' diagnostics
/// export and HikeDetail's GPX export.
struct IdentifiedURL: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

/// How long the "Refresh Trail Data" button stays in its
/// disabled / "Trail Data Cleared" confirmation state before
/// flipping back to the actionable label.
private let refreshButtonReenableDelay: Duration = .seconds(3)

/// How long the download buttons hold their "(N of N)" final
/// count visible after a prefetch completes, so the user sees
/// the result land instead of the label snapping back to the
/// idle state immediately.
private let progressHoldDuration: Duration = .seconds(1.5)

struct SettingsView: View {
    @Environment(AuthService.self) private var auth
    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(FavoritesService.self) private var favorites
    @Environment(RecordingService.self) private var recording

    @AppStorage(StorageKeys.theme) private var theme: AppTheme = .system
    @AppStorage(StorageKeys.debugHUD) private var showDebugHUD: Bool = false
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    /// URL of the most recent diagnostics bundle. Non-nil while
    /// the share sheet is presented; cleared when it dismisses
    /// (sheet's `onDismiss`). Identifiable via `Self` already
    /// (URL is Hashable + Identifiable in iOS 16+).
    @State private var diagnosticsShareURL: IdentifiedURL? = nil
    /// User-visible error from the diagnostics export — surfaced
    /// inline in the Developer section rather than as an alert so
    /// it doesn't interrupt the user mid-flow.
    @State private var diagnosticsError: String? = nil
    @State private var diagnosticsExporting: Bool = false

    @State private var showSignIn = false
    @State private var showResetConfirm = false
    @State private var showSignOutConfirm = false
    @State private var showDeleteAccountConfirm = false

    /// Backup export — non-nil while the share sheet is presented with
    /// the exported JSON file URL. Cleared on dismiss.
    @State private var exportShareURL: IdentifiedURL? = nil
    @State private var exportError: String? = nil
    /// Import — fileImporter is shown when this is true; user-picked
    /// file lands in `importPendingURL` for the confirmation dialog;
    /// `importSuccess` flips true after a successful import so the
    /// "relaunch the app" alert can present.
    @State private var showDataImporter = false
    @State private var importPendingURL: URL? = nil
    @State private var importError: String? = nil
    @State private var importSuccess = false
    @State private var showRefreshConfirm = false
    @State private var trailDataRefreshed = false
    @State private var stats: UserStats? = nil
    /// Active "Download for Offline" progress as `(completed, total)`.
    /// Non-nil while the prefetch task is running so the button label can
    /// show "Downloading 2 of 5…". Cleared a beat after completion so the
    /// user sees the final state briefly before it reverts.
    @State private var downloadProgress: (Int, Int)? = nil
    @State private var showDownloadConfirm = false
    /// Same idea for the "Download Nearby Areas" radius prefetch button.
    @State private var nearbyProgress: (Int, Int)? = nil
    @State private var showNearbyCellularConfirm = false

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
                        // Required by App Store Guideline 5.1.1(v): any
                        // app offering account creation (Sign in with
                        // Apple counts) must offer in-app deletion. The
                        // account is local-only, so this removes the
                        // Apple credential and leaves hikes/progress in
                        // place — data wiping is Reset All Progress.
                        Button(role: .destructive) {
                            showDeleteAccountConfirm = true
                        } label: {
                            Label("Delete Account", systemImage: "person.crop.circle.badge.xmark")
                        }
                        .confirmationDialog(
                            "Delete your account?",
                            isPresented: $showDeleteAccountConfirm,
                            titleVisibility: .visible
                        ) {
                            Button("Delete Account", role: .destructive) {
                                auth.deleteAccount()
                            }
                            Button("Cancel", role: .cancel) { }
                        } message: {
                            Text("This removes Sign in with Apple from TrekDex. Your hikes, trail progress, and badges stay on this device — to erase those too, use Reset All Progress under Data.")
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
                    .onChange(of: theme) { _, newValue in
                        ActivityLogService.shared.log(
                            category: "settings", action: "theme",
                            context: ["value": newValue.rawValue]
                        )
                        AnalyticsService.shared.capture(.themeChanged(value: newValue.rawValue))
                    }
                }

                Section("Display") {
                    Picker("Units", selection: $units) {
                        ForEach(UnitsPreference.allCases) { unit in
                            Text(unit.label).tag(unit)
                        }
                    }
                    .onChange(of: units) { _, newValue in
                        ActivityLogService.shared.log(
                            category: "settings", action: "units",
                            context: ["value": newValue.rawValue]
                        )
                        AnalyticsService.shared.capture(.unitsChanged(value: newValue.rawValue))
                    }
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
                            ActivityLogService.shared.log(category: "settings", action: "refreshTrails")
                            AreaDataService.shared.clearAreaCache()
                            trailDataRefreshed = true
                            // Re-enable the button after a brief
                            // confirmation window so the user can refresh
                            // again later in the same session.
                            Task {
                                try? await Task.sleep(for: refreshButtonReenableDelay)
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
                                    // prefetchOffline runs off MainActor, so
                                    // hop back here before touching @State —
                                    // otherwise writes race the renderer and
                                    // get coalesced away.
                                    await MainActor.run {
                                        downloadProgress = (completed, total)
                                    }
                                }
                                // Hold the final "(N of N)" reading for a
                                // beat so the user sees the result land
                                // instead of the label snapping back
                                // immediately.
                                try? await Task.sleep(for: progressHoldDuration)
                                downloadProgress = nil
                            }
                        }
                        Button("Cancel", role: .cancel) { }
                    } message: {
                        Text("Saves trail data for your favorited and recently-opened areas so you can open them without a signal. Skips anything that's already up to date.")
                    }

                    Button {
                        guard LocationService.shared.userLocation != nil else {
                            // No location yet — kick off the permission
                            // prompt; user can tap again once they've
                            // granted access and a fix has come in.
                            LocationService.shared.requestPermission()
                            return
                        }
                        if NetworkService.shared.isExpensive {
                            showNearbyCellularConfirm = true
                        } else {
                            runNearbyDownload()
                        }
                    } label: {
                        if let p = nearbyProgress {
                            Label("Downloading \(p.0) of \(p.1)…", systemImage: "location.circle")
                        } else {
                            Label("Download Nearby Areas", systemImage: "location.circle")
                        }
                    }
                    .disabled(nearbyProgress != nil)
                    .confirmationDialog(
                        "You're on a cellular network. Download anyway?",
                        isPresented: $showNearbyCellularConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Download") { runNearbyDownload() }
                        Button("Cancel", role: .cancel) { }
                    } message: {
                        Text("Sweeps a 50-mile radius around your current location. May use significant cellular data depending on how many areas are nearby.")
                    }

                    NavigationLink {
                        DownloadedAreasView()
                    } label: {
                        Label("Manage Downloads", systemImage: "internaldrive")
                    }
                }

                Section("Data") {
                    Button {
                        runDataExport()
                    } label: {
                        Label("Export All Data…", systemImage: "square.and.arrow.up")
                    }
                    if let err = exportError {
                        Text(err)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }

                    Button {
                        showDataImporter = true
                    } label: {
                        Label("Import Data…", systemImage: "square.and.arrow.down")
                    }
                    if let err = importError {
                        Text(err)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }

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
                    NavigationLink {
                        FeedbackView()
                    } label: {
                        Label("Send Feedback", systemImage: "envelope")
                    }
                }

                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Back up your hikes", systemImage: "icloud.and.arrow.up")
                        Text("Hike history and trail completions live on this device. Enable iCloud Backup (iOS Settings → your Apple ID → iCloud → iCloud Backup) so your progress survives reinstalls and new devices. Cloud sync is coming in a future update.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                } header: {
                    Text("Backup")
                }

                // Developer-mode controls. Not gated by a flag — small
                // enough that the cost of always-on visibility is low,
                // and the user explicitly opted in by installing
                // TestFlight builds.
                Section("Developer") {
                    Toggle(isOn: $showDebugHUD) {
                        Label("Show Debug HUD", systemImage: "speedometer")
                    }
                    .onChange(of: showDebugHUD) { _, newValue in
                        ActivityLogService.shared.log(
                            category: "settings", action: "debugHUD",
                            context: ["value": String(newValue)]
                        )
                    }
                    Button {
                        ActivityLogService.shared.log(category: "diag", action: "send")
                        runDiagnosticsExport()
                    } label: {
                        HStack {
                            Label("Send Diagnostics", systemImage: "doc.text.magnifyingglass")
                            Spacer()
                            if diagnosticsExporting {
                                ProgressView()
                            }
                        }
                    }
                    .disabled(diagnosticsExporting)
                    if let err = diagnosticsError {
                        Text(err)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }

                Section("About") {
                    LabeledContent("Version", value: appVersion)
                    LabeledContent("Build", value: buildNumber)
                    if let url = privacyPolicyURL {
                        Link(destination: url) {
                            Label("Privacy Policy", systemImage: "hand.raised")
                        }
                    }
                    if let url = termsOfServiceURL {
                        Link(destination: url) {
                            Label("Terms of Service", systemImage: "doc.plaintext")
                        }
                    }
                    // Required attribution: trail geometry + silhouettes
                    // are derived from OpenStreetMap data, licensed under
                    // the ODbL, which requires a visible "© OpenStreetMap
                    // contributors" credit linking to the licence. Also
                    // covers App Review guideline 5.2 (third-party IP).
                    // Do not remove.
                    Link(destination: osmCopyrightURL) {
                        Label("Map data © OpenStreetMap contributors", systemImage: "map")
                    }
                }
            }
            .navigationTitle("Settings")
        }
        .sheet(isPresented: $showSignIn) {
            AuthView()
        }
        .sheet(item: $diagnosticsShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
        .sheet(item: $exportShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
        .fileImporter(
            isPresented: $showDataImporter,
            allowedContentTypes: [.json],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first { importPendingURL = url }
            case .failure(let error):
                importError = "Couldn't open file: \(error.localizedDescription)"
            }
        }
        .confirmationDialog(
            "Replace all current data with this backup?",
            isPresented: Binding(
                get: { importPendingURL != nil },
                set: { if !$0 { importPendingURL = nil } }
            ),
            titleVisibility: .visible,
            presenting: importPendingURL
        ) { url in
            Button("Replace", role: .destructive) {
                runDataImport(from: url)
            }
            Button("Cancel", role: .cancel) {
                importPendingURL = nil
            }
        } message: { _ in
            Text("Every completion, coverage value, hike, and favourite on this device will be replaced by the contents of the backup file. Cannot be undone.")
        }
        .alert("Import complete", isPresented: $importSuccess) {
            Button("OK") {}
        } message: {
            Text("Quit and relaunch the app to see the restored data — the app caches some state in memory at launch.")
        }
        // Re-run when the recording state flips (recording starts or stops)
        // so completing a hike then opening Settings shows fresh numbers
        // instead of whatever was cached on the last view appearance.
        .task(id: recording.activeRecording == nil) { await refreshStats() }
    }

    /// Kick off a manual "Download Nearby" run with `force: true` so it
    /// bypasses the network / movement gates the cold-launch path
    /// respects. Same progress-on-MainActor + 1.5 s post-completion hold
    /// pattern as the favorites prefetch above.
    /// Kick off the diagnostics-export flow. Builds the JSON
    /// bundle off the main actor (mostly — OSLogStore reads stay
    /// on main), then presents the share sheet with the resulting
    /// file URL. Errors are surfaced inline under the button so
    /// the user doesn't lose the rest of their Settings context to
    /// an alert.
    private func runDiagnosticsExport() {
        diagnosticsError = nil
        diagnosticsExporting = true
        Task {
            do {
                let url = try await DiagnosticsService.exportBundle()
                diagnosticsShareURL = IdentifiedURL(url: url)
            } catch {
                diagnosticsError = "Couldn't build diagnostics: \(error.localizedDescription)"
            }
            diagnosticsExporting = false
        }
    }

    private func runNearbyDownload() {
        Task {
            nearbyProgress = (0, 0)
            await AreaDataService.shared.runNearbyPrefetchIfAppropriate(force: true) { completed, total in
                await MainActor.run {
                    nearbyProgress = (completed, total)
                }
            }
            try? await Task.sleep(for: progressHoldDuration)
            nearbyProgress = nil
        }
    }

    private func statsBlock(_ s: UserStats) -> some View {
        HStack {
            statColumn(value: "\(s.hikeCount)", label: s.hikeCount == 1 ? "hike" : "hikes")
            Divider().frame(height: 36)
            statColumn(value: UnitFormatter.distanceValue(miles: s.totalMi, units: units),
                       label: units == .imperial ? "miles" : "km")
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
            // Walk-aware: credit each touched area's own completions.
            for areaId in hike.touchedAreaIds {
                for trailId in hike.completedTrailIds(in: areaId) {
                    completedPairs.insert("\(areaId):\(trailId)")
                }
            }
        }

        var areas = Set<String>()
        for hike in history { areas.formUnion(hike.touchedAreaIds) }
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

    /// Build the export blob, write it to a temp file, and present
    /// the share sheet pointing at that file. Errors surface inline
    /// under the Export button so the user keeps Settings context.
    private func runDataExport() {
        exportError = nil
        do {
            let data = try DataBackupManager.collectExport()
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent(DataBackupManager.suggestedFilename())
            try data.write(to: url, options: .atomic)
            exportShareURL = IdentifiedURL(url: url)
            ActivityLogService.shared.log(
                category: "settings", action: "exportData",
                context: ["bytes": "\(data.count)"]
            )
            AnalyticsService.shared.capture(.dataExported())
        } catch {
            exportError = "Export failed: \(error.localizedDescription)"
        }
    }

    /// Read the picked file and replace the entire app state with its
    /// contents. UI surfaces the "please relaunch" alert on success
    /// since the @MainActor singletons cache state in memory at init.
    private func runDataImport(from url: URL) {
        importError = nil
        importPendingURL = nil
        // Picked files are sandboxed — must startAccessingSecurityScoped
        // before reading or the read returns "permission denied" even
        // though Files chose it.
        let didStart = url.startAccessingSecurityScopedResource()
        defer { if didStart { url.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: url)
            try DataBackupManager.performImport(from: data)
            ActivityLogService.shared.log(
                category: "settings", action: "importData",
                context: ["bytes": "\(data.count)"]
            )
            AnalyticsService.shared.capture(.dataImported())
            importSuccess = true
        } catch {
            importError = error.localizedDescription
        }
    }

    private func resetAll() async {
        ActivityLogService.shared.log(category: "settings", action: "resetAll")
        // Reset every @Observable singleton that holds user progress
        // in memory. Without these calls the UI keeps showing the old
        // state — checkmarks, coverage bars, favorites — because each
        // service loads its dictionary at init and never reloads from
        // UserDefaults again. Each service's resetAll() both zeros
        // the in-memory copy AND clears its UserDefaults entry, so
        // SwiftUI views observing them refresh immediately.
        ProgressService.shared.resetAll()
        CoverageService.shared.resetAll()
        FavoritesService.shared.resetAll()
        RecordingService.shared.resetAll()
        // Sweep any remaining keys that aren't owned by a service
        // (e.g. the cached last-known user location). removeObject is
        // idempotent so the overlap with the service resets is fine.
        for key in StorageKeys.resetAllKeys { UserDefaults.standard.removeObject(forKey: key) }
        // Wipe the activity log too — fresh-device state should look
        // like a brand-new install. clear() removes the file outright.
        ActivityLogService.shared.clear()
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
