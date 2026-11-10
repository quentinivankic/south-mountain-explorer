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
    /// Slim bar: the summary line and Start hike only, so the map stays in view.
    var isCollapsed = false

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial
    @State private var showTrails = false
    @State private var showSteps = true

    private var steps: [PlanStep] { controller.preview.selected?.route.steps ?? [] }
    private var isLoop: Bool { controller.preview.selected?.route.end == nil }

    private var selectedRoute: PlanResultDisplay.Route? {
        guard let id = controller.preview.selected?.id else { return nil }
        if id == "best" { return display.route }
        return display.alternatives.first { PlanPreview.slug($0.title) == id }?.route
    }

    var body: some View {
        if isCollapsed { collapsedBar } else { card }
    }

    /// What a swipe down leaves: one summary line and Start hike. Swipe up for the card.
    private var collapsedBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let i = controller.selectedStep, i < steps.count {
                collapsedStep(i)
            } else if let route = selectedRoute {
                Text(route.summary)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? 4 : 2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityLabel(route.spoken)
                    .accessibilityHint("Swipe up for the route details.")
                    .accessibilityIdentifier("plan-collapsed-summary")
            }
            if showsLegend, controller.selectedStep == nil, !dynamicTypeSize.isAccessibilitySize { legend.lineLimit(1) }
            startButton
        }
        .padding(.horizontal, 20)
        .padding(.top, 18)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("plan-collapsed-bar")
    }

    /// The selected step in the bar, with previous and next, so the route can
    /// be followed leg by leg while the map stays in view.
    private func collapsedStep(_ i: Int) -> some View {
        HStack(spacing: 4) {
            stepNavButton("chevron.left", label: "Previous step", enabled: i > 0) { controller.selectStep(i - 1) }
            Text(steps[i].line(units: units))
                .font(.subheadline.weight(.semibold))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? 4 : 2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("plan-collapsed-step")
            stepNavButton("chevron.right", label: "Next step", enabled: i + 1 < steps.count) { controller.selectStep(i + 1) }
        }
    }

    private func stepNavButton(_ symbol: String, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.body.weight(.semibold))
                .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .foregroundStyle(enabled ? Color.accentColor : Color.secondary)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }

    /// The drawn route has both new and already walked trail runs.
    private var showsLegend: Bool { controller.preview.selected?.route.mixesNewAndDone ?? false }

    /// How to read the two line shades. Visual only: the spoken summary already gives the new miles.
    private var legend: some View {
        Text("Bright = new, faded = done")
            .font(.caption)
            .foregroundStyle(.secondary)
            .accessibilityHidden(true)
            .accessibilityIdentifier("plan-route-legend")
    }

    private var startButton: some View {
        Button(action: onStart) {
            Text("Start hike")
                .font(.headline)
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.borderedProminent)
        .disabled(!gate.isAllowed)
        .accessibilityIdentifier("plan-start-hike")
        .accessibilityHint("Starts recording a normal hike.")
    }

    private var card: some View {
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
            if !r.breakdown.isEmpty {
                Text(r.breakdown)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("plan-route-breakdown")
            }
            if showsLegend { legend }
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

        if !steps.isEmpty { stepList }

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

    // MARK: - Steps

    /// "1. Start on X, 0.6 mi", "2. Left onto Y, 1.1 mi", ... The numbers
    /// match the badges on the map. Tapping a step highlights its leg and
    /// zooms to it; tapping it again shows the whole route.
    private var stepList: some View {
        DisclosureGroup(isExpanded: $showSteps) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(steps.indices, id: \.self) { i in stepRow(i) }
                Label(isLoop ? "Finish back at the start" : "Finish", systemImage: "flag.checkered")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(minHeight: 44, alignment: .leading)
                    .accessibilityIdentifier("plan-step-finish")
            }
            .padding(.top, 4)
        } label: {
            Text("Steps (\(steps.count))")
                .font(.subheadline.weight(.semibold))
                .frame(minHeight: 44, alignment: .leading)
                .accessibilityIdentifier("plan-route-steps")
        }
    }

    private func stepRow(_ i: Int) -> some View {
        let step = steps[i]
        let selected = controller.selectedStep == i
        return Button {
            controller.selectStep(i)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("\(step.number)")
                    .font(.caption.weight(.bold).monospacedDigit())
                    .foregroundStyle(selected ? Color.white : Self.routeColor)
                    .frame(minWidth: 22, minHeight: 22)
                    .background(
                        Capsule().fill(selected ? Self.routeColor : Color.clear)
                    )
                    .overlay(Capsule().stroke(Self.routeColor, lineWidth: 1.5))
                    .accessibilityHidden(true)
                Text(step.instruction)
                    .font(.subheadline)
                    .foregroundStyle(Color.primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                Text(UnitFormatter.distance(meters: step.meters, units: units))
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 6)
            .frame(minHeight: 44)
            .background(
                RoundedRectangle(cornerRadius: 8).fill(selected ? Self.routeColor.opacity(0.12) : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(step.line(units: units))
        .accessibilityHint(selected ? "Shows the whole route." : "Highlights this part on the map.")
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("plan-step-\(step.number)")
    }

    /// Same magenta as the planned route line on the map.
    static let routeColor = Color(red: 0.86, green: 0.0, blue: 0.52)

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
                startButton
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
