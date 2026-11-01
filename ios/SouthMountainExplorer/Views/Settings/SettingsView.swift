import SwiftUI
import CoreLocation

/// Privacy policy, hosted at trekdex.app. Pinned here so the Privacy
/// Policy row in Settings → About links to the authoritative copy.
/// The SAME URL must go into App Store Connect's Privacy Policy URL
/// field at submission — keep them in sync.
private let privacyPolicyURL: URL? = URL(string: "https://trekdex.app/privacypolicy")

/// Terms of Service, hosted at trekdex.app. Surfaced in Settings →
/// About next to the privacy policy.
private let termsOfServiceURL: URL? = URL(string: "https://trekdex.app/termsofservice")

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

private enum OfflineTrailTaskState: Equatable {
    case idle
    case checking
    case progress(OfflineTrailPrefetchProgress)
    case result(OfflineTrailPrefetchResult)
    case retryResult(OfflineTrailPrefetchResult)
    case skipped(OfflineTrailPrefetchSkipReason)

    var isBusy: Bool {
        switch self {
        case .checking, .progress: return true
        case .idle, .result, .retryResult, .skipped: return false
        }
    }

    var prefetchResult: OfflineTrailPrefetchResult? {
        switch self {
        case .result(let result), .retryResult(let result): return result
        case .idle, .checking, .progress, .skipped: return nil
        }
    }
}

private enum NearbyLocationAlert: String, Identifiable {
    case accessDenied
    case unavailable

    var id: String { rawValue }

    var title: String {
        switch self {
        case .accessDenied: return "Location Access Needed"
        case .unavailable: return "Location Unavailable"
        }
    }

    var message: String {
        switch self {
        case .accessDenied:
            return "Allow TrekDex to use your location in Settings, then try the nearby download again."
        case .unavailable:
            return "TrekDex couldn't get your current location. Move somewhere with a clearer view of the sky and try again."
        }
    }
}

struct SettingsView: View {
    @Environment(AuthService.self) private var auth
    @Environment(LocationService.self) private var location

    @AppStorage(StorageKeys.trailMesh) private var trailMesh = true
    @AppStorage(StorageKeys.debugHUD) private var showDebugHUD: Bool = false
    /// Temporary, TestFlight-only: see the Developer picker below.
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
    @State private var showRefreshConfirm = false
    @State private var refreshState: OfflineTrailTaskState = .idle
    @State private var downloadState: OfflineTrailTaskState = .idle
    @State private var showDownloadConfirm = false
    @State private var nearbyState: OfflineTrailTaskState = .idle
    @State private var showNearbyCellularConfirm = false
    @State private var pendingNearbyRetry: OfflineTrailPrefetchResult? = nil
    /// Covers permission/fresh-fix preparation before download progress begins.
    /// Set synchronously before any Task so repeated taps cannot launch duplicates.
    @State private var isPreparingNearbyDownload = false
    @State private var waitingForNearbyPermission = false
    @State private var nearbyLocationAlert: NearbyLocationAlert? = nil
    @State private var locationConsumer = LocationConsumerID()

    private var nearbyDownloadBusy: Bool {
        isPreparingNearbyDownload || nearbyState.isBusy
    }

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
                            Text("This removes Sign in with Apple from TrekDex. Your hikes, trail progress, and badges stay on this device — to erase those too, use Erase Hikes & Progress under Your Data.")
                        }
                    } else {
                        Button {
                            showSignIn = true
                        } label: {
                            Label("Sign in with Apple", systemImage: "apple.logo")
                        }
                    }
                }

                // "Appearance" and "Display" used to be two sections that meant
                // the same thing (theme + backdrop in one, units in the other).
                // One section, three rows.
                // No Theme picker: the app is dark-mode only for now (see
                // ContentView's preferredColorScheme), so offering a choice that
                // does nothing would be worse than offering none.
                Section("Appearance") {
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
                    Toggle("Trail backdrop", isOn: $trailMesh)
                        .onChange(of: trailMesh) { _, newValue in
                            ActivityLogService.shared.log(
                                category: "settings", action: "trailMesh",
                                context: ["value": String(newValue)]
                            )
                        }
                }

                Section("Offline Trails") {
                    Text("Downloaded trail and catalog geometry stays available without a signal. Apple base-map tiles may not.")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    Button {
                        showRefreshConfirm = true
                    } label: {
                        Label(taskLabel(defaultTitle: "Refresh Offline Trails", state: refreshState), systemImage: "arrow.clockwise")
                    }
                    .disabled(refreshState.isBusy)
                    .confirmationDialog(
                        "Refresh downloaded Offline Trails?",
                        isPresented: $showRefreshConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Refresh") { runOfflineRefresh() }
                        Button("Cancel", role: .cancel) { }
                    } message: {
                        Text("Verifies replacement trail geometry before changing each saved file. If a refresh fails, the prior offline trails stay available.")
                    }
                    offlineStatus(refreshState, retryTitle: "Retry Failed Refreshes") {
                        retryRefresh()
                    }

                    Button {
                        showDownloadConfirm = true
                    } label: {
                        Label(taskLabel(defaultTitle: "Download Saved & Recent Areas", state: downloadState), systemImage: "arrow.down.circle")
                    }
                    .disabled(downloadState.isBusy)
                    .confirmationDialog(
                        "Download saved and recently viewed areas for offline trail use?",
                        isPresented: $showDownloadConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Download") { runOfflineDownload() }
                        Button("Cancel", role: .cancel) { }
                    } message: {
                        Text("Saves trail and catalog geometry. Apple base-map tiles are not included.")
                    }
                    offlineStatus(downloadState, retryTitle: "Retry Failed Downloads") {
                        retryDownload()
                    }

                    Button {
                        beginNearbyDownload()
                    } label: {
                        if isPreparingNearbyDownload {
                            Label("Finding Location…", systemImage: "location.circle")
                        } else {
                            Label(taskLabel(defaultTitle: "Download Nearby Areas", state: nearbyState), systemImage: "location.circle")
                        }
                    }
                    .disabled(nearbyDownloadBusy)
                    .confirmationDialog(
                        "You're on a cellular network. Download anyway?",
                        isPresented: $showNearbyCellularConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Download") {
                            if let retry = pendingNearbyRetry {
                                pendingNearbyRetry = nil
                                runNearbyRetry(retry)
                            } else {
                                runNearbyDownload()
                            }
                        }
                        Button("Cancel", role: .cancel) { pendingNearbyRetry = nil }
                    } message: {
                        Text("Saves trail and catalog geometry for every area within 50 miles. This can use a lot of cellular data; Apple base-map tiles are not included.")
                    }
                    offlineStatus(nearbyState, retryTitle: "Retry Failed Nearby Areas") {
                        beginNearbyRetry()
                    }

                    NavigationLink {
                        DownloadedAreasView()
                    } label: {
                        Label("Manage Offline Trails", systemImage: "internaldrive")
                    }
                }

                Section("Your Data") {
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

                    Button(role: .destructive) {
                        showResetConfirm = true
                    } label: {
                        Label("Erase Hikes & Progress", systemImage: "trash")
                    }
                    .confirmationDialog(
                        "Erase Hikes & Progress?",
                        isPresented: $showResetConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Erase Hikes & Progress", role: .destructive) {
                            Task { await resetAll() }
                        }
                        Button("Cancel", role: .cancel) { }
                    } message: {
                        Text("Removes saved and in-progress hikes and walks, completions, coverage and badges, Saved Areas, Offline Trails, the local activity log, and onboarding. Apple sign-in and display preferences are kept.")
                    }
                }

                // One section: "Feedback" and "Support TrekDex" were two
                // separate single-row sections saying the same thing.
                Section("Support") {
                    NavigationLink {
                        FeedbackView()
                    } label: {
                        Label("Send Feedback", systemImage: "envelope")
                    }
                    NavigationLink {
                        TipJarView()
                    } label: {
                        Label("Leave a Tip", systemImage: "heart")
                    }
                    .accessibilityIdentifier("tip-jar-link")
                }

                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Back up your hikes", systemImage: "icloud.and.arrow.up")
                        Text("Your hikes live on this device. Turn on iCloud Backup in iOS Settings so they survive a reinstall or a new phone. Cloud sync is coming later.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                } header: {
                    Text("Backup")
                }

                // TESTFLIGHT/DEV ONLY: hidden from App Store production installs.
                if BuildEnv.isTestFlight {
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
                            Label("Share Diagnostics…", systemImage: "doc.text.magnifyingglass")
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
            .trailMeshBackground()
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
        .alert(
            nearbyLocationAlert?.title ?? "Location",
            isPresented: Binding(
                get: { nearbyLocationAlert != nil },
                set: { if !$0 { nearbyLocationAlert = nil } }
            ),
            presenting: nearbyLocationAlert
        ) { alert in
            switch alert {
            case .accessDenied:
                Button("Open Settings") { location.requestPermission() }
                Button("Cancel", role: .cancel) { }
            case .unavailable:
                Button("Retry") { beginNearbyDownload() }
                Button("Cancel", role: .cancel) { }
            }
        } message: { alert in
            Text(alert.message)
        }
        .onChange(of: location.authorizationStatus) { _, status in
            handleNearbyAuthorizationChange(status)
        }
        .onDisappear {
            location.releaseLocation(for: locationConsumer)
        }
    }

    private func taskLabel(defaultTitle: String, state: OfflineTrailTaskState) -> String {
        switch state {
        case .checking:
            return "Checking Offline Trails…"
        case .progress(let progress):
            return "Checking \(progress.processedCount) of \(progress.totalCount)…"
        case .idle, .result, .retryResult, .skipped:
            return defaultTitle
        }
    }

    @ViewBuilder
    private func offlineStatus(
        _ state: OfflineTrailTaskState,
        retryTitle: String,
        retry: @escaping () -> Void
    ) -> some View {
        switch state {
        case .idle, .checking:
            EmptyView()
        case .progress(let progress):
            Text("\(progress.succeededCount + progress.alreadyCurrentCount) verified, \(progress.failedCount) failed")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .result(let result):
            offlineResultStatus(result, wasRetry: false, retryTitle: retryTitle, retry: retry)
        case .retryResult(let result):
            offlineResultStatus(result, wasRetry: true, retryTitle: retryTitle, retry: retry)
        case .skipped(let reason):
            Text(skipMessage(reason))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func offlineResultStatus(
        _ result: OfflineTrailPrefetchResult,
        wasRetry: Bool,
        retryTitle: String,
        retry: @escaping () -> Void
    ) -> some View {
        let prefix = wasRetry ? "Retry: " : ""
        if result.requestedIDs.isEmpty {
            Text("\(prefix)No areas matched this request. Nothing was changed.")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else if result.succeededIDs.isEmpty,
                  result.alreadyCurrentIDs.count == result.requestedIDs.count {
            Text("\(prefix)All \(result.requestedIDs.count) area\(result.requestedIDs.count == 1 ? " was" : "s were") already available for offline trail use.")
                .font(.caption)
                .foregroundStyle(.green)
        } else if result.isCompleteDurableAvailability {
            let available = result.succeededIDs.count + result.alreadyCurrentIDs.count
            Text("\(prefix)\(available) of \(result.requestedIDs.count) areas verified for offline trail use.")
                .font(.caption)
                .foregroundStyle(.green)
        } else if result.isPartial {
            Text("\(prefix)\(result.succeededIDs.count + result.alreadyCurrentIDs.count) of \(result.requestedIDs.count) areas are available. \(result.failedIDs.count) failed; prior valid trail files were kept.")
                .font(.caption)
                .foregroundStyle(.orange)
            Button(retryTitle, action: retry)
        } else {
            Text("\(prefix)Couldn't verify \(result.failedIDs.count) area\(result.failedIDs.count == 1 ? "" : "s"). Prior valid trail files were kept.")
                .font(.caption)
                .foregroundStyle(.red)
            Button(retryTitle, action: retry)
        }
    }

    private func skipMessage(_ reason: OfflineTrailPrefetchSkipReason) -> String {
        switch reason {
        case .noLocation: return "No current location was available. Try again."
        case .networkUnavailable: return "Background download waits for an unmetered connection."
        case .expensiveNetwork: return "Background download skipped cellular data."
        case .movementCooldown: return "Nearby Offline Trails are already verified for this location."
        }
    }

    private func runOfflineRefresh() {
        guard !refreshState.isBusy else { return }
        refreshState = .checking
        ActivityLogService.shared.log(category: "settings", action: "refreshOfflineTrails")
        Task { @MainActor in
            let result = await AreaDataService.shared.refreshOfflineTrails { progress in
                await MainActor.run { refreshState = .progress(progress) }
            }
            refreshState = .result(result)
        }
    }

    private func runOfflineDownload() {
        guard !downloadState.isBusy else { return }
        downloadState = .checking
        Task { @MainActor in
            let result = await AreaDataService.shared.prefetchOffline { progress in
                await MainActor.run { downloadState = .progress(progress) }
            }
            downloadState = .result(result)
        }
    }

    private func retryRefresh() {
        guard let prior = refreshState.prefetchResult, !prior.retryIDs.isEmpty else { return }
        refreshState = .checking
        Task { @MainActor in
            let result = await AreaDataService.shared.retryOfflineTrails(
                prior,
                forceRefresh: true
            ) { progress in
                await MainActor.run { refreshState = .progress(progress) }
            }
            refreshState = .retryResult(result)
        }
    }

    private func retryDownload() {
        guard let prior = downloadState.prefetchResult, !prior.retryIDs.isEmpty else { return }
        downloadState = .checking
        Task { @MainActor in
            let result = await AreaDataService.shared.retryOfflineTrails(
                prior,
                forceRefresh: false
            ) { progress in
                await MainActor.run { downloadState = .progress(progress) }
            }
            downloadState = .retryResult(result)
        }
    }

    /// Kick off the diagnostics-export flow. Builds the JSON bundle off the
    /// main actor, then presents its file URL. Errors stay inline.
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

    private func beginNearbyDownload() {
        guard !nearbyDownloadBusy else { return }

        switch location.authorizationStatus {
        case .denied, .restricted:
            nearbyLocationAlert = .accessDenied
        case .notDetermined:
            isPreparingNearbyDownload = true
            waitingForNearbyPermission = true
            location.requestPermission()
        case .authorizedAlways, .authorizedWhenInUse:
            isPreparingNearbyDownload = true
            Task { @MainActor in
                await requestNearbyLocationFix()
            }
        @unknown default:
            nearbyLocationAlert = .accessDenied
        }
    }

    private func handleNearbyAuthorizationChange(_ status: CLAuthorizationStatus) {
        guard waitingForNearbyPermission else { return }

        switch status {
        case .authorizedAlways, .authorizedWhenInUse:
            waitingForNearbyPermission = false
            Task { @MainActor in
                await requestNearbyLocationFix()
            }
        case .denied, .restricted:
            waitingForNearbyPermission = false
            isPreparingNearbyDownload = false
            nearbyLocationAlert = .accessDenied
        case .notDetermined:
            break
        @unknown default:
            waitingForNearbyPermission = false
            isPreparingNearbyDownload = false
            nearbyLocationAlert = .accessDenied
        }
    }

    private func requestNearbyLocationFix() async {
        guard isPreparingNearbyDownload, location.isAuthorized else {
            isPreparingNearbyDownload = false
            return
        }

        // Always require a post-tap coarse fix before choosing the 50-mile
        // radius. The one-shot demand cannot enable background updates or
        // interfere with an active recording's precise ownership.
        let result = await location.requestOneShotFix(
            for: locationConsumer,
            accuracy: .coarse
        )
        guard isPreparingNearbyDownload, !Task.isCancelled else {
            isPreparingNearbyDownload = false
            return
        }
        switch result {
        case .success:
            continueNearbyDownload()
        case .denied:
            isPreparingNearbyDownload = false
            nearbyLocationAlert = .accessDenied
        case .unavailable:
            isPreparingNearbyDownload = false
            nearbyLocationAlert = .unavailable
        }
    }

    private func continueNearbyDownload() {
        guard isPreparingNearbyDownload else { return }
        isPreparingNearbyDownload = false
        if NetworkService.shared.isExpensive {
            showNearbyCellularConfirm = true
        } else {
            runNearbyDownload()
        }
    }

    private func beginNearbyRetry() {
        guard let prior = nearbyState.prefetchResult, !prior.retryIDs.isEmpty else { return }
        if NetworkService.shared.isExpensive {
            pendingNearbyRetry = prior
            showNearbyCellularConfirm = true
        } else {
            runNearbyRetry(prior)
        }
    }

    private func runNearbyRetry(_ prior: OfflineTrailPrefetchResult) {
        guard !nearbyState.isBusy else { return }
        nearbyState = .checking
        Task { @MainActor in
            let result = await AreaDataService.shared.retryOfflineTrails(
                prior,
                forceRefresh: false
            ) { progress in
                await MainActor.run { nearbyState = .progress(progress) }
            }
            nearbyState = .retryResult(result)
        }
    }

    private func runNearbyDownload() {
        guard !nearbyState.isBusy else { return }
        isPreparingNearbyDownload = false
        nearbyState = .checking
        Task { @MainActor in
            let run = await AreaDataService.shared.runNearbyPrefetchIfAppropriate(force: true) { progress in
                await MainActor.run { nearbyState = .progress(progress) }
            }
            switch run {
            case .completed(let result):
                nearbyState = .result(result)
            case .skipped(let reason):
                nearbyState = .skipped(reason)
                if reason == .noLocation { nearbyLocationAlert = .unavailable }
            }
        }
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
