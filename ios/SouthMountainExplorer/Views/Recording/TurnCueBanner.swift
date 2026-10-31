import SwiftUI

/// Glass pill at the top of the app while a trail recording has a live cue:
/// arrow, headline, and a distance countdown.
struct TurnCueBanner: View {
    let cue: TurnCueBannerState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: TurnCueBanner.symbolName(cue.symbol))
                .font(.title2.weight(.semibold))
                .foregroundStyle(TurnCueBanner.tint(cue.symbol))
                .frame(width: 36, height: 36)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(cue.headline)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                if let d = cue.distanceText {
                    Text(d)
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .compatibleGlass(in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .transition(reduceMotion ? .opacity : .opacity.combined(with: .move(edge: .top)))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(cue.headline)
        .accessibilityValue(cue.distanceText ?? "")
        .accessibilityIdentifier("turn-cue-banner")
    }

    static func symbolName(_ symbol: TurnCueSymbol) -> String {
        switch symbol {
        case .keepLeft: return "arrow.up.left"
        case .keepRight: return "arrow.up.right"
        case .turnLeft, .sharpLeft: return "arrow.turn.up.left"
        case .turnRight, .sharpRight: return "arrow.turn.up.right"
        case .offTrail: return "exclamationmark.triangle.fill"
        case .backOnTrail: return "checkmark.circle.fill"
        case .gpsWeak: return "antenna.radiowaves.left.and.right.slash"
        }
    }

    static func tint(_ symbol: TurnCueSymbol) -> Color {
        switch symbol {
        case .offTrail, .gpsWeak: return .orange
        case .backOnTrail: return .green
        default: return .cyan
        }
    }
}
