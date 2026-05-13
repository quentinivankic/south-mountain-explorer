import SwiftUI

struct OnboardingView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 14) {
                Image(systemName: "mountain.2.fill")
                    .font(.system(size: 72))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.cyan, .blue, .indigo],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .symbolEffect(.bounce, value: true)

                Text("South Mountain Explorer")
                    .font(.largeTitle)
                    .fontWeight(.bold)
                    .multilineTextAlignment(.center)

                Text("Find every trail in your favorite parks.")
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 32)

            Spacer()

            VStack(alignment: .leading, spacing: 24) {
                bullet(
                    icon: "map.fill",
                    title: "Discover",
                    body: "Browse hiking areas near you with map views, search, and trail counts."
                )
                bullet(
                    icon: "record.circle.fill",
                    title: "Record",
                    body: "GPS-track your hikes — works in the background while your phone's in your pocket."
                )
                bullet(
                    icon: "checkmark.seal.fill",
                    title: "Complete",
                    body: "Trails you finish turn cyan on the map. Watch your progress fill in over time."
                )
            }
            .padding(.horizontal, 32)

            Spacer()

            Text("Tip: enable iCloud Backup in iOS Settings to keep your hikes safe across reinstalls.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
                .padding(.bottom, 12)

            Button {
                dismiss()
            } label: {
                Text("Get Started")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .padding(.horizontal, 24)
            .padding(.bottom, 32)
        }
        .interactiveDismissDisabled()
    }

    private func bullet(icon: String, title: String, body: String) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Image(systemName: icon)
                .font(.title2)
                .foregroundStyle(.cyan)
                .frame(width: 36, height: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                Text(body)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
