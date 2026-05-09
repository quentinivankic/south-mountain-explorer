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
