import SwiftUI

struct SettingsView: View {
    @Environment(AuthService.self) private var auth
    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(FavoritesService.self) private var favorites

    @State private var showSignIn = false
    @State private var showResetConfirm = false
    @State private var showSignOutConfirm = false

    var body: some View {
        NavigationStack {
            List {
                // Account section
                Section("Account") {
                    if auth.isSignedIn {
                        HStack {
                            Image(systemName: "person.circle.fill")
                                .foregroundStyle(.green)
                                .font(.title2)
                            VStack(alignment: .leading) {
                                Text("Signed In")
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
                                Task { await auth.signOut() }
                            }
                        }
                    } else {
                        Button {
                            showSignIn = true
                        } label: {
                            Label("Sign In", systemImage: "person.circle")
                        }
                    }
                }

                // Data section
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

                // App info
                Section("About") {
                    LabeledContent("Version", value: appVersion)
                    LabeledContent("Build", value: buildNumber)
                    Link(destination: URL(string: "https://supabase.com")!) {
                        Label("Powered by Supabase", systemImage: "bolt.fill")
                    }
                }
            }
            .navigationTitle("Settings")
        }
        .sheet(isPresented: $showSignIn) {
            AuthView()
        }
    }

    private func resetAll() async {
        // Clear all local UserDefaults keys
        let keys = ["summit:completed", "summit:coverage", "summit:favorites", "summit:active-recording", "location.lat", "location.lon"]
        for key in keys { UserDefaults.standard.removeObject(forKey: key) }

        // Clear caches directory
        if let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first {
            let areasDir = caches.appendingPathComponent("areas")
            try? FileManager.default.removeItem(at: areasDir)
        }
    }

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
    }

    private var buildNumber: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—"
    }
}
