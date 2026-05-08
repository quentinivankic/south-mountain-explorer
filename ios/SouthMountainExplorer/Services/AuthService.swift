import Foundation
import AuthenticationServices
import CryptoKit
import Supabase

@MainActor
@Observable
final class AuthService: NSObject {
    static let shared = AuthService()

    private(set) var userId: String? = nil
    private(set) var isLoading = false
    private(set) var errorMessage: String? = nil

    private override init() {
        super.init()
        Task { await observeAuthState() }
    }

    private func observeAuthState() async {
        for await (_, session) in supabase.auth.authStateChanges {
            userId = session?.user.id.uuidString
        }
    }

    func signInWithApple() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        let nonce = randomNonce()
        let provider = ASAuthorizationAppleIDProvider()
        let request = provider.createRequest()
        request.requestedScopes = [.fullName, .email]
        request.nonce = sha256(nonce)

        do {
            let result = try await performAppleAuth(request: request)
            guard let credential = result.credential as? ASAuthorizationAppleIDCredential,
                  let tokenData = credential.identityToken,
                  let token = String(data: tokenData, encoding: .utf8)
            else {
                errorMessage = "Could not read Apple credential."
                return
            }
            try await supabase.auth.signInWithIdToken(
                credentials: .init(provider: .apple, idToken: token, nonce: nonce)
            )
        } catch ASAuthorizationError.canceled {
            // User dismissed — not an error
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func signOut() async {
        do {
            try await supabase.auth.signOut()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    var isSignedIn: Bool { userId != nil }

    // MARK: - Apple auth presentation

    private func performAppleAuth(request: ASAuthorizationAppleIDRequest) async throws -> ASAuthorization {
        try await withCheckedThrowingContinuation { continuation in
            let controller = ASAuthorizationController(authorizationRequests: [request])
            let delegate = AppleAuthDelegate(continuation: continuation)
            controller.delegate = delegate
            controller.presentationContextProvider = self
            objc_setAssociatedObject(controller, &AssociatedKeys.delegate, delegate, .OBJC_ASSOCIATION_RETAIN)
            controller.performRequests()
        }
    }

    // MARK: - Nonce helpers

    private func randomNonce(length: Int = 32) -> String {
        let charset = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._")
        var result = ""
        var remaining = length
        while remaining > 0 {
            let randoms: [UInt8] = (0..<16).map { _ in
                var b: UInt8 = 0
                SecRandomCopyBytes(kSecRandomDefault, 1, &b)
                return b
            }
            for b in randoms {
                guard remaining > 0 else { break }
                if b < charset.count { result.append(charset[Int(b)]); remaining -= 1 }
            }
        }
        return result
    }

    private func sha256(_ input: String) -> String {
        let hash = SHA256.hash(data: Data(input.utf8))
        return hash.compactMap { String(format: "%02x", $0) }.joined()
    }
}

private enum AssociatedKeys { static var delegate = 0 }

extension AuthService: ASAuthorizationControllerPresentationContextProviding {
    nonisolated func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
            .first { $0.isKeyWindow } ?? UIWindow()
    }
}

private final class AppleAuthDelegate: NSObject, ASAuthorizationControllerDelegate {
    let continuation: CheckedContinuation<ASAuthorization, Error>
    init(continuation: CheckedContinuation<ASAuthorization, Error>) {
        self.continuation = continuation
    }
    func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        continuation.resume(returning: authorization)
    }
    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        continuation.resume(throwing: error)
    }
}
