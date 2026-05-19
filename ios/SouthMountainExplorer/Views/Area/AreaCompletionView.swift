import SwiftUI

struct AreaCompletionView: View {
    let area: Area

    @Environment(\.dismiss) private var dismiss
    @State private var bounce = false

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            ZStack {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [.yellow.opacity(0.6), .clear],
                            center: .center,
                            startRadius: 10,
                            endRadius: 160
                        )
                    )
                    .frame(width: 320, height: 320)
                    .blur(radius: 8)

                Image(systemName: "trophy.fill")
                    .font(.system(size: 120))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.yellow, .orange],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .symbolEffect(.bounce, options: .repeat(2), value: bounce)
                    .shadow(color: .yellow.opacity(0.6), radius: 20)
            }

            VStack(spacing: 8) {
                Text("Area Complete!")
                    .font(.largeTitle)
                    .fontWeight(.bold)

                Text(area.name)
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 24)

            HStack(spacing: 0) {
                stat(value: "\(area.resolvedTrailCount)", label: "Trails")
                Divider().frame(height: 36)
                stat(value: String(format: "%.1f", area.resolvedTotalMi), label: "Miles")
            }
            .padding(.vertical, 16)
            .frame(maxWidth: .infinity)
            .compatibleGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .padding(.horizontal, 32)

            Spacer()

            Button {
                dismiss()
            } label: {
                Text("Continue")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .padding(.horizontal, 24)
            .padding(.bottom, 32)
        }
        .onAppear {
            bounce.toggle()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    private func stat(value: String, label: String) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.title2.bold().monospacedDigit())
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}
