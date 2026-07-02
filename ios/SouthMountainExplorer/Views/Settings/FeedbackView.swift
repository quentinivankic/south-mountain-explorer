import SwiftUI

/// In-app feedback form. Captures a `feedback_submitted` analytics event
/// (category + message + optional email); with PostHog wired (Phase 3)
/// these land in the dashboard, and until then they route harmlessly to
/// the no-op analytics backend.
///
/// Replaces the old Settings → Feedback hint that told users to use
/// TestFlight's screenshot-share — meaningless once the app ships on the
/// App Store, where there's no TestFlight.
struct FeedbackView: View {
    @Environment(\.dismiss) private var dismiss

    /// Feedback bucket. Display label is user-facing; `key` is the
    /// stable analytics value (decoupled so relabeling the UI doesn't
    /// silently reshape the data).
    enum Category: String, CaseIterable, Identifiable {
        case bug = "Bug"
        case idea = "Idea"
        case praise = "Praise"
        case other = "Other"

        var id: String { rawValue }
        var key: String { rawValue.lowercased() }
    }

    @State private var category: Category = .idea
    @State private var message: String = ""
    @State private var email: String = ""
    @State private var submitted = false

    private var trimmedMessage: String {
        message.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var canSubmit: Bool { !trimmedMessage.isEmpty }

    var body: some View {
        Form {
            Section {
                Picker("Type", selection: $category) {
                    ForEach(Category.allCases) { Text($0.rawValue).tag($0) }
                }
            }

            Section("Your feedback") {
                TextEditor(text: $message)
                    .frame(minHeight: 120)
                    .accessibilityLabel("Feedback message")
            }

            Section {
                TextField("Email (optional)", text: $email)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } footer: {
                Text("Add your email only if you'd like a reply — otherwise your feedback is sent without it.")
            }

            Section {
                Button("Send Feedback") { submit() }
                    .disabled(!canSubmit)
            }
        }
        .navigationTitle("Send Feedback")
        .navigationBarTitleDisplayMode(.inline)
        .alert("Thanks for the feedback!", isPresented: $submitted) {
            Button("OK") { dismiss() }
        } message: {
            Text("We read every note.")
        }
    }

    private func submit() {
        let trimmedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        AnalyticsService.shared.capture(.feedbackSubmitted(
            category: category.key,
            message: trimmedMessage,
            email: trimmedEmail.isEmpty ? nil : trimmedEmail))
        // Local breadcrumb too (no message content — keeps the local
        // activity log free-text-free, matching its existing discipline).
        ActivityLogService.shared.log(
            category: "feedback", action: "submit",
            context: ["category": category.key,
                      "hasEmail": trimmedEmail.isEmpty ? "false" : "true"])
        submitted = true
    }
}
