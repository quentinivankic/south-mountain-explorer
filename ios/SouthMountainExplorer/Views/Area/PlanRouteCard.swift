import SwiftUI

/// A pill button used for time choices and route choices. Always at least 44 pt
/// tall, with a selected trait for VoiceOver.
struct PlanChip: View {
    let title: String
    let isSelected: Bool
    let spokenLabel: String
    let identifier: String
    var fillsWidth = false
    let action: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                .minimumScaleFactor(dynamicTypeSize.isAccessibilitySize ? 1 : 0.8)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: fillsWidth ? .infinity : nil, minHeight: 44)
                .padding(.horizontal, 12)
                .background(isSelected ? Color.accentColor : Color.secondary.opacity(0.18), in: Capsule())
                .foregroundStyle(isSelected ? Color.white : Color.primary)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(spokenLabel)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .accessibilityIdentifier(identifier)
    }
}

/// The result card shown while the planned route is drawn on the map: route
/// choices (chips), one summary line, a plain note when the route is mostly
/// repeat ground, then Start hike and Edit.
struct PlanRouteCard: View {
    let controller: PlanHikeController
    let display: PlanResultDisplay
    let gate: PlanStartGate
    let onStart: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var showTrails = false

    private var selectedRoute: PlanResultDisplay.Route? {
        guard let id = controller.preview.selected?.id else { return nil }
        if id == "best" { return display.route }
        return display.alternatives.first { PlanPreview.slug($0.title) == id }?.route
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if controller.preview.options.count > 1 { alternativesPicker }
                if let route = selectedRoute { summary(route) }
                ForEach(display.notes, id: \.self) { note in
                    Label(note, systemImage: "info.circle")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("plan-note")
                }
                Text(PlanStartGate.guidanceNote)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("plan-guidance-note")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { actionBar }
    }

    // MARK: - Summary

    @ViewBuilder
    private func summary(_ r: PlanResultDisplay.Route) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(r.summary)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            Text(r.climbLine)
                .font(.footnote)
                .foregroundStyle(.secondary)
            if let low = r.lowValueNote {
                Label(low, systemImage: "info.circle")
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
                    .accessibilityIdentifier("plan-low-value")
            }
            ForEach(r.flags, id: \.self) { flag in
                Label(flag, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if r.flagCount > 0 {
                Text("Dashed lines are off-trail or unverified connections.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(r.spoken)
        .accessibilityIdentifier("plan-route-card")

        if r.trailNames.count > 1 {
            DisclosureGroup(isExpanded: $showTrails) {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(r.trailNames.indices, id: \.self) { i in
                        Text(r.trailNames[i]).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 4)
            } label: {
                Text("Trails it finishes (\(r.trailNames.count))")
                    .frame(minHeight: 44, alignment: .leading)
            }
            .accessibilityIdentifier("plan-route-trails")
        }
    }

    // MARK: - Route choices

    @ViewBuilder
    private var alternativesPicker: some View {
        let options = controller.preview.options
        let tall = dynamicTypeSize.isAccessibilitySize
        let layout = tall
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 8))
        let chips = ForEach(options, id: \.id) { option in
            PlanChip(
                title: option.title,
                isSelected: controller.preview.selected?.id == option.id,
                spokenLabel: "\(option.title) route",
                identifier: "plan-alt-\(option.id)",
                fillsWidth: tall
            ) { controller.selectRoute(id: option.id) }
        }
        if tall {
            layout { chips }
        } else {
            ScrollView(.horizontal, showsIndicators: false) { layout { chips } }
                .accessibilityIdentifier("plan-alternatives-picker")
        }
    }

    // MARK: - Actions

    private var actionBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            if case .blocked(let reason) = gate {
                Text(reason)
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("plan-start-blocked")
            }
            let layout = dynamicTypeSize.isAccessibilitySize
                ? AnyLayout(VStackLayout(spacing: 8))
                : AnyLayout(HStackLayout(spacing: 12))
            layout {
                Button(action: onStart) {
                    Text("Start hike")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!gate.isAllowed)
                .accessibilityIdentifier("plan-start-hike")
                .accessibilityHint("Starts recording a normal hike.")

                Button {
                    controller.edit()
                } label: {
                    Text("Edit")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("plan-edit")
                .accessibilityHint("Goes back to the start and time to plan again.")
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(.bar)
    }
}
