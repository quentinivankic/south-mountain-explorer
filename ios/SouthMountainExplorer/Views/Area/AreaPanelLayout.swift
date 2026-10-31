import SwiftUI

/// Pure layout math for the wide-window area screen (iPhone Duo unfolded, or
/// any window at least `wideThreshold` points wide): the trail menu becomes a
/// full-height side panel with the map beside it, instead of a bottom sheet.
///
/// Everything here is a function of measured widths so it can be unit-tested
/// without a window. Compact windows (every iPhone, the folded Duo) never
/// reach the wide path: `isWide` is false for them.
enum AreaPanelLayout {
    /// Windows at least this wide (points) use the side panel. The folded Duo
    /// (466 pt) and every iPhone (<= 440 pt) stay on the bottom sheet.
    static let wideThreshold: CGFloat = 600
    /// The map pane never gets narrower than this.
    static let minMapWidth: CGFloat = 280
    /// Smallest side panel, at standard and accessibility text sizes.
    static let minPanelWidth: CGFloat = 320
    static let minPanelWidthAccessibility: CGFloat = 360
    /// The panel never takes less/more than this share of the usable width.
    static let minFraction: Double = 0.3
    static let maxFraction: Double = 0.7
    static let defaultFraction: Double = 0.5
    static let defaultFractionAccessibility: Double = 0.55
    /// VoiceOver increment/decrement step.
    static let accessibilityStep: Double = 0.05
    /// Minimum touch target of the divider.
    static let dividerHitWidth: CGFloat = 44

    static func minPanelWidth(accessibility: Bool) -> CGFloat {
        accessibility ? minPanelWidthAccessibility : minPanelWidth
    }

    static func defaultFraction(accessibility: Bool) -> Double {
        accessibility ? defaultFractionAccessibility : defaultFraction
    }

    /// Wide only when the window is wide enough AND the usable width (window
    /// minus system safe areas such as the Duo's right rail) can hold the
    /// smallest panel plus the smallest map, so the clamp can never invert.
    static func isWide(windowWidth: CGFloat, usableWidth: CGFloat, accessibility: Bool) -> Bool {
        guard windowWidth.isFinite, usableWidth.isFinite else { return false }
        return windowWidth >= wideThreshold
            && usableWidth >= minPanelWidth(accessibility: accessibility) + minMapWidth
    }

    /// Panel width for a requested width, clamped to 30-70% of the usable
    /// width, at least the panel minimum, leaving the map at least its minimum.
    static func clampedWidth(_ requested: CGFloat, usableWidth: CGFloat, accessibility: Bool) -> CGFloat {
        let low = max(CGFloat(minFraction) * usableWidth, minPanelWidth(accessibility: accessibility))
        let high = min(CGFloat(maxFraction) * usableWidth, usableWidth - minMapWidth)
        guard high >= low else { return low }
        guard requested.isFinite else { return low }
        return min(max(requested, low), high)
    }

    /// Panel width for a stored fraction (nil or non-positive = never set).
    static func panelWidth(fraction: Double?, usableWidth: CGFloat, accessibility: Bool) -> CGFloat {
        let f = (fraction ?? 0) > 0 ? fraction! : defaultFraction(accessibility: accessibility)
        return clampedWidth(CGFloat(f) * usableWidth, usableWidth: usableWidth, accessibility: accessibility)
    }

    /// The fraction to persist for a committed panel width.
    static func fraction(forWidth width: CGFloat, usableWidth: CGFloat) -> Double {
        guard usableWidth > 0, width.isFinite else { return defaultFraction }
        return min(max(Double(width / usableWidth), minFraction), maxFraction)
    }

    /// One VoiceOver step (+1 wider panel, -1 narrower) from the current width.
    static func steppedWidth(from width: CGFloat, steps: Int, usableWidth: CGFloat, accessibility: Bool) -> CGFloat {
        clampedWidth(
            width + CGFloat(Double(steps) * accessibilityStep) * usableWidth,
            usableWidth: usableWidth,
            accessibility: accessibility
        )
    }

    /// Whole-percent value read by VoiceOver.
    static func percent(width: CGFloat, usableWidth: CGFloat) -> Int {
        guard usableWidth > 0 else { return 0 }
        return Int((Double(width / usableWidth) * 100).rounded())
    }
}

/// Measured widths of the area screen: the full window and the part left
/// inside the system safe areas (the Duo's right-edge rail).
struct AreaWidths: Equatable {
    var window: CGFloat
    var usable: CGFloat
}

/// Resolves the map's own viewport size for camera math. A measured size wins
/// when it is usable; otherwise the window size is the fallback (first frame,
/// before layout). On compact layouts the map is full-bleed, so the measured
/// size equals the window size and camera output is unchanged.
enum MapViewportSizing {
    static func resolve(measured: CGSize, fallback: CGSize) -> CGSize {
        guard measured.width.isFinite, measured.height.isFinite,
              measured.width > 0, measured.height > 0 else { return fallback }
        return measured
    }
}

/// When the area screen shows its loading silhouette. The map mounts as soon
/// as the area lands and the silhouette overlays it until the loading floor
/// elapses, so the handoff fades over a live map instead of cutting to an
/// empty one. The failure state is the area-less, not-loading case.
enum AreaLoadingVisibility {
    static func overlayVisible(hasArea: Bool, isLoading: Bool, floorElapsed: Bool) -> Bool {
        hasArea ? !floorElapsed : isLoading
    }

    static func showsFailure(hasArea: Bool, isLoading: Bool) -> Bool {
        !hasArea && !isLoading
    }
}

/// Draggable, VoiceOver-adjustable divider between the side panel and the map.
/// The visual rule is 1 pt; the hit area is `dividerHitWidth` wide and floats
/// over both neighbours without taking layout space.
struct AreaPanelDivider: View {
    let panelWidth: CGFloat
    let usableWidth: CGFloat
    let accessibility: Bool
    /// Live width while dragging (not persisted).
    let onDrag: (CGFloat) -> Void
    /// Final width on release (persist + refit once).
    let onCommit: (CGFloat) -> Void

    @State private var dragStartWidth: CGFloat?

    var body: some View {
        Rectangle()
            .fill(Color(.separator))
            .frame(width: 1)
            .frame(maxHeight: .infinity)
            .overlay {
                Capsule()
                    .fill(Color(.tertiaryLabel))
                    .frame(width: 4, height: 36)
            }
            .overlay {
                Color.clear
                    .frame(width: AreaPanelLayout.dividerHitWidth)
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 1)
                            .onChanged { value in
                                let start = dragStartWidth ?? panelWidth
                                dragStartWidth = start
                                onDrag(start + value.translation.width)
                            }
                            .onEnded { value in
                                let start = dragStartWidth ?? panelWidth
                                dragStartWidth = nil
                                onCommit(start + value.translation.width)
                            }
                    )
            }
            .zIndex(1)
            .accessibilityElement()
            .accessibilityLabel("Resize trail list")
            .accessibilityValue(
                "\(AreaPanelLayout.percent(width: panelWidth, usableWidth: usableWidth)) percent"
            )
            .accessibilityAdjustableAction { direction in
                let steps: Int
                switch direction {
                case .increment: steps = 1
                case .decrement: steps = -1
                @unknown default: return
                }
                onCommit(AreaPanelLayout.steppedWidth(
                    from: panelWidth,
                    steps: steps,
                    usableWidth: usableWidth,
                    accessibility: accessibility
                ))
            }
            .accessibilityIdentifier("area-panel-divider")
    }
}
