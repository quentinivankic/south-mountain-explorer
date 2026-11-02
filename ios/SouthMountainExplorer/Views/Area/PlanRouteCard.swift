import SwiftUI

/// "Plan a Hike", Task 10: the small card shown while the planned route is
/// drawn on the map. Summary, trail list, alternatives, Start hike, Clear.
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

    private func summaryLine(_ r: PlanResultDisplay.Route) -> String {
        func value(_ label: String) -> String { r.stats.first { $0.label == label }?.value ?? "" }
        let trails = r.trailNames.isEmpty ? "No whole trail" : "Finishes \(PlanText.trailCount(r.trailNames.count))"
        return [trails, value("Time"), value("Distance"), "\(value("Climb")) climb"].joined(separator: " · ")
    }

    @ViewBuilder
    private func summary(_ r: PlanResultDisplay.Route) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(summaryLine(r))
                .font(.subheadline.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
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

        if !r.trailNames.isEmpty {
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

    // MARK: - Alternatives

    @ViewBuilder
    private var alternativesPicker: some View {
        let options = controller.preview.options
        let layout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 8))
        let chips = ForEach(options, id: \.id) { option in
            let isSelected = controller.preview.selected?.id == option.id
            Button {
                controller.selectRoute(id: option.id)
            } label: {
                Text(option.title)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: dynamicTypeSize.isAccessibilitySize ? .infinity : nil, minHeight: 44)
                    .padding(.horizontal, 12)
                    .background(isSelected ? Color.accentColor : Color.secondary.opacity(0.18), in: Capsule())
                    .foregroundStyle(isSelected ? Color.white : Color.primary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(option.title) route")
            .accessibilityAddTraits(isSelected ? .isSelected : [])
            .accessibilityIdentifier("plan-alt-\(option.id)")
        }
        if dynamicTypeSize.isAccessibilitySize {
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
                    controller.clearRoute()
                } label: {
                    Text("Clear")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("plan-clear-route")
                .accessibilityHint("Removes the route from the map.")
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(.bar)
    }
}
