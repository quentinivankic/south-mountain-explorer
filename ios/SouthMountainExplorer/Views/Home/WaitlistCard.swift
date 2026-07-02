import SwiftUI

/// Explore-tab card shown when the device's region isn't covered (see
/// `RegionSupport`). Collects an email so the user can be notified when
/// TrekDex reaches their country — the signup is a `waitlist_joined`
/// analytics event (PostHog), no separate backend. Once joined it flips
/// to a compact confirmation, remembered across launches.
///
/// Soft prompt: it sits ABOVE the normal Explore content, which still
/// lists US/CA parks, so an out-of-region user can browse/plan trips.
struct WaitlistCard: View {
    let countryName: String
    let regionCode: String

    @AppStorage(StorageKeys.waitlistJoined) private var joined = false
    @State private var email = ""

    private var trimmedEmail: String {
        email.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Minimal sanity check — an `@` with something on both sides and a
    /// dotted domain. Deliberately loose; real validation is the send.
    private var emailLooksValid: Bool {
        let e = trimmedEmail
        guard let at = e.firstIndex(of: "@"), at != e.startIndex else { return false }
        let domain = e[e.index(after: at)...]
        return domain.contains(".") && !domain.hasSuffix(".") && !domain.hasPrefix(".")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "mappin.and.ellipse")
                    .font(.title2)
                    .foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Not in \(countryName) yet")
                        .font(.headline)
                    Text("TrekDex covers the US & Canada today. Browse those parks below — or get an email when we reach you.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if joined {
                Label("You're on the waitlist", systemImage: "checkmark.circle.fill")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.green)
            } else {
                HStack(spacing: 8) {
                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(join)
                    Button("Join", action: join)
                        .buttonStyle(.borderedProminent)
                        .disabled(!emailLooksValid)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func join() {
        guard emailLooksValid else { return }
        AnalyticsService.shared.capture(
            .waitlistJoined(country: regionCode, email: trimmedEmail))
        // Local breadcrumb without the email (keeps the activity log
        // free-text-free, matching its discipline).
        ActivityLogService.shared.log(
            category: "waitlist", action: "join", context: ["country": regionCode])
        joined = true
    }
}
