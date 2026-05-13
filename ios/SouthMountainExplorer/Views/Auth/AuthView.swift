import SwiftUI
import AuthenticationServices

struct AuthView: View {
    @Environment(AuthService.self) private var auth
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        NavigationStack {
            VStack(spacing: 40) {
                Spacer()

                VStack(spacing: 12) {
                    Image(systemName: "mountain.2.fill")
                        .font(.system(size: 72))
                        .foregroundStyle(.green)
                    Text("TrekDex")
                        .font(.title2.bold())
                    Text("Sign in to record hikes and\ntrack your trail progress.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }

                VStack(spacing: 12) {
                    SignInWithAppleButton(.signIn) { request in
                        request.requestedScopes = [.fullName, .email]
                    } onCompletion: { _ in
                        // Handled in AuthService
                    }
                    .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
                    .frame(height: 50)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay {
                        // Intercept tap to use our async flow
                        Button {
                            Task {
                                await auth.signInWithApple()
                                if auth.isSignedIn { dismiss() }
                            }
                        } label: { Color.clear }
                    }

                    if let error = auth.errorMessage {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .multilineTextAlignment(.center)
                    }

                    if auth.isLoading {
                        ProgressView()
                    }
                }
                .padding(.horizontal, 40)

                Spacer()
                Spacer()
            }
            .navigationTitle("Sign In")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }
}
