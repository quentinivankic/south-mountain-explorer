import SwiftUI

/// Report a problem with a specific trail — a reason code plus optional
/// free-text details. Fires a `trail_reported` analytics event (PostHog, the
/// same pipeline as feedback); the reason codes map to our curation vocabulary,
/// so a report often points straight at an OpenStreetMap tagging fix. Mirrors
/// FeedbackView's shape. Presented as a sheet from the area's overflow menu
/// when a trail is selected.
struct ReportTrailView: View {
    let trail: Trail
    let areaId: String
    let areaName: String

    @Environment(\.dismiss) private var dismiss

    /// Why the trail is being reported. Display label is user-facing; `key` is
    /// the stable analytics value (decoupled so relabeling the UI doesn't
    /// silently reshape the data), chosen to echo our curation categories.
    enum Reason: String, CaseIterable, Identifiable {
        case notATrail = "Not a real trail"
        case wrongName = "Wrong or missing name"
        case closedPrivate = "Closed / no public access"
        case badRoute = "Route or map is wrong"
        case isARoad = "It's a road, not a trail"
        case other = "Something else"

        var id: String { rawValue }
        var key: String {
            switch self {
            case .notATrail: return "not_a_trail"
            case .wrongName: return "wrong_name"
            case .closedPrivate: return "closed_or_private"
            case .badRoute: return "bad_geometry"
            case .isARoad: return "is_a_road"
            case .other: return "other"
            }
        }
    }

    @State private var reason: Reason = .notATrail
    @State private var details: String = ""
    @State private var submitted = false

    private var trimmedDetails: String {
        details.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(trail.name)
                        .font(.headline)
                    Text(areaName)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                } header: {
                    Text("Reporting")
                }

                Section("What's wrong?") {
                    Picker("Reason", selection: $reason) {
                        ForEach(Reason.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                }

                Section("Details (optional)") {
                    TextEditor(text: $details)
                        .frame(minHeight: 100)
                        .accessibilityLabel("Report details")
                }

                Section {
                    Button("Submit Report") { submit() }
                } footer: {
                    Text("Trail data comes from OpenStreetMap. Your report helps us fix it — thank you!")
                }
            }
            .navigationTitle("Report Trail")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .alert("Report sent — thank you!", isPresented: $submitted) {
                Button("OK") { dismiss() }
            } message: {
                Text("We'll take a look and get the data fixed.")
            }
        }
    }

    private func submit() {
        AnalyticsService.shared.capture(.trailReported(
            reason: reason.key, details: trimmedDetails,
            trailId: trail.id, trailName: trail.name, areaId: areaId))
        // Local breadcrumb without the free text — keeps the activity log
        // PII-free, matching feedback / waitlist discipline.
        ActivityLogService.shared.log(
            category: "report", action: "trail",
            context: ["reason": reason.key, "trailId": trail.id, "areaId": areaId])
        submitted = true
    }
}
