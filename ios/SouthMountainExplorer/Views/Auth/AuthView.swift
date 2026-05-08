import SwiftUI

struct AuthView: View {
    @Environment(AuthService.self) private var auth
    @Environment(\.dismiss) private var dismiss

    @State private var email = ""
    @State private var password = ""
    @State private var isSignUp = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 32) {
                    // Logo / header
                    VStack(spacing: 12) {
                        Image(systemName: "mountain.2.fill")
                            .font(.system(size: 64))
                            .foregroundStyle(.green)
                        Text("South Mountain Explorer")
                            .font(.title2)
                            .fontWeight(.bold)
                        Text(isSignUp ? "Create your account" : "Welcome back")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.top, 20)

                    // Form
                    VStack(spacing: 16) {
                        TextField("Email", text: $email)
                            .textInputAutocapitalization(.never)
                            .keyboardType(.emailAddress)
                            .autocorrectionDisabled()
                            .padding()
                            .glassEffect(in: .rect(cornerRadius: 14))

                        SecureField("Password", text: $password)
                            .padding()
                            .glassEffect(in: .rect(cornerRadius: 14))

                        if let error = auth.errorMessage {
                            Text(error)
                                .font(.caption)
                                .foregroundStyle(.red)
                                .multilineTextAlignment(.center)
                        }

                        Button {
                            Task {
                                if isSignUp {
                                    await auth.signUp(email: email, password: password)
                                } else {
                                    await auth.signIn(email: email, password: password)
                                }
                                if auth.isSignedIn { dismiss() }
                            }
                        } label: {
                            Group {
                                if auth.isLoading {
                                    ProgressView()
                                } else {
                                    Text(isSignUp ? "Create Account" : "Sign In")
                                        .fontWeight(.semibold)
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(.green, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                            .foregroundStyle(.white)
                        }
                        .disabled(auth.isLoading || email.isEmpty || password.isEmpty)
                    }
                    .padding(.horizontal)

                    // Toggle sign in / sign up
                    Button {
                        withAnimation { isSignUp.toggle() }
                    } label: {
                        Text(isSignUp ? "Already have an account? **Sign In**" : "Don't have an account? **Sign Up**")
                            .font(.subheadline)
                    }
                    .foregroundStyle(.secondary)
                }
            }
            .navigationTitle(isSignUp ? "Sign Up" : "Sign In")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }
}
