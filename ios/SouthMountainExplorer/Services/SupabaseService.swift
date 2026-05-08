import Foundation
import Supabase

let supabase = SupabaseClient(
    supabaseURL: URL(string: "https://lwfjxctmllesvwgtcfby.supabase.co")!,
    supabaseKey: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx3Zmp4Y3RtbGxlc3Z3Z3RjZmJ5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc3NjEwNjIsImV4cCI6MjA5MzMzNzA2Mn0.7tuX6HlErq2WkXBir5TMpwPYyt-E2ZFvswj-mn5JnkY"
)

@MainActor
@Observable
final class AuthService {
    static let shared = AuthService()

    private(set) var userId: String? = nil
    private(set) var isLoading = false
    private(set) var errorMessage: String? = nil

    private init() {
        Task { await observeAuthState() }
    }

    private func observeAuthState() async {
        for await (_, session) in supabase.auth.authStateChanges {
            userId = session?.user.id.uuidString
        }
    }

    func signIn(email: String, password: String) async {
        isLoading = true
        errorMessage = nil
        do {
            try await supabase.auth.signIn(email: email, password: password)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func signUp(email: String, password: String) async {
        isLoading = true
        errorMessage = nil
        do {
            try await supabase.auth.signUp(email: email, password: password)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func signOut() async {
        do {
            try await supabase.auth.signOut()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    var isSignedIn: Bool { userId != nil }
}
