import SwiftUI

/// "Plan a Hike" (Release A, Task 9): inputs, a Plan button, and a plain text
/// result. Drawing the route on the map and starting it are later tasks.
struct PlanHikeSheet: View {
    let area: Area
    let controller: PlanHikeController
    /// Closes the sheet and lets the user tap the map for a start or end.
    let onDropPin: (PlanPinTarget) -> Void
    let onClose: () -> Void

    @Environment(AreaDataService.self) private var areas
    @Environment(ProgressService.self) private var progress
    @Environment(RecordingService.self) private var recording
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    private static let dropID = "drop"
    private static let pinID = "pin"
    private static let resultAnchor = "plan-result-anchor"

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                Form {
                    startSection
                    limitsSection
                    paceSection
                    shapeSection
                    resultSection
                }
                // A finished plan lands below the inputs; bring it into view.
                .onChange(of: controller.resultVersion) { _, _ in
                    withAnimation { proxy.scrollTo(Self.resultAnchor, anchor: .top) }
                }
            }
            .navigationTitle("Plan a Hike")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { onClose() }
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityIdentifier("plan-done-button")
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) { runBar }
        }
        .task { await controller.prepare(area: area, recording: recording, units: units) }
        .onChange(of: controller.inputs) { _, _ in controller.inputsChanged() }
        .onDisappear { controller.cancel() }
    }

    // MARK: - Start / end

    private var startSection: some View {
        Section {
            spotPicker(
                title: "Start", id: "plan-start-picker", pin: controller.startPin, target: .start,
                selection: controller.inputs.start,
                set: { controller.inputs.start = $0 })
        } header: {
            Text("Where")
        } footer: {
            Text("Pick a trailhead or parking spot, or drop a pin on the map.")
        }
    }

    private func spotPicker(
        title: String, id: String, pin: PlanSpot?, target: PlanPinTarget,
        selection: StartPoint?, set: @escaping (StartPoint?) -> Void
    ) -> some View {
        let current: String? = {
            guard let selection else { return nil }
            if let pin, pin.point == selection { return Self.pinID }
            return controller.spots.first { $0.point == selection }?.id
        }()
        let binding = Binding<String?>(
            get: { current },
            set: { new in
                switch new {
                case Self.dropID?: onDropPin(target)
                case Self.pinID?: set(pin?.point)
                case let spotID?: set(controller.spots.first { $0.id == spotID }?.point)
                case nil: set(nil)
                }
            })
        return Picker(title, selection: binding) {
            Text("Choose…").tag(String?.none)
            // Pin options first: the lot list can run to dozens of rows.
            Text(pin == nil ? "Drop a pin…" : "Move the pin…").tag(Optional(Self.dropID))
            if let pin { Text(pin.title).tag(Optional(Self.pinID)) }
            ForEach(controller.spots) { spot in Text(spot.title).tag(Optional(spot.id)) }
        }
        .pickerStyle(.menu)
        .frame(minHeight: 44)
        .accessibilityIdentifier(id)
        .accessibilityHint("Choose a trailhead, a parking spot, or drop a pin on the map.")
    }

    // MARK: - Time and climb

    private var limitsSection: some View {
        @Bindable var controller = controller
        return Section {
            Picker("Time limit", selection: $controller.inputs.timeLimitMinutes) {
                ForEach(controller.inputs.timeOptions(), id: \.self) { m in
                    Text(PlanText.duration(Double(m) * 60)).tag(m)
                }
            }
            .pickerStyle(.menu)
            .frame(minHeight: 44)
            .accessibilityIdentifier("plan-time-limit")

            Toggle("Limit the climb", isOn: $controller.inputs.limitClimb)
                .frame(minHeight: 44)
                .accessibilityIdentifier("plan-max-climb-toggle")

            if controller.inputs.limitClimb {
                Picker("Max climb", selection: $controller.inputs.climbValue) {
                    ForEach(PlanHikeInputs.climbOptions(units: units), id: \.self) { v in
                        Text("\(v) \(UnitFormatter.elevationSuffix(units: units))").tag(v)
                    }
                }
                .pickerStyle(.menu)
                .frame(minHeight: 44)
                .accessibilityIdentifier("plan-max-climb-picker")
            }
        } header: {
            Text("Limits")
        }
    }

    // MARK: - Pace

    @ViewBuilder
    private var paceSection: some View {
        @Bindable var controller = controller
        Section {
            if let personal = controller.personalPace {
                Picker("Pace", selection: $controller.inputs.pace) {
                    Text("Default").tag(PlanPaceChoice.standard)
                    Text("My pace").tag(PlanPaceChoice.mine)
                }
                .pickerStyle(.segmented)
                .frame(minHeight: 44)
                .accessibilityIdentifier("plan-pace-picker")
                if controller.inputs.pace == .mine {
                    Text(personal.summary)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("plan-pace-summary")
                }
            } else {
                LabeledContent("Pace", value: "Default")
                    .frame(minHeight: 44)
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("plan-pace-picker")
            }
        } header: {
            Text("Pace")
        } footer: {
            if controller.personalPace == nil {
                Text("\"My pace\" appears after a few recorded hikes.")
            }
        }
    }

    // MARK: - Shape

    private var shapeSection: some View {
        @Bindable var controller = controller
        return Section {
            shapePicker($controller.inputs.shape)

            if controller.inputs.shape == .oneWay {
                spotPicker(
                    title: "End", id: "plan-end-picker", pin: controller.endPin, target: .end,
                    selection: controller.inputs.end,
                    set: { controller.inputs.end = $0 })
            }
        } header: {
            Text("Shape")
        } footer: {
            Text(controller.inputs.shape == .loop
                 ? "Loop returns to where you started."
                 : "One-way finishes at the end you pick.")
        }
    }

    @ViewBuilder
    private func shapePicker(_ selection: Binding<RoutePlanRequest.Shape>) -> some View {
        let picker = Picker("Shape", selection: selection) {
            Text("Loop").tag(RoutePlanRequest.Shape.loop)
            Text("One-way").tag(RoutePlanRequest.Shape.oneWay)
        }
        if dynamicTypeSize.isAccessibilitySize {
            picker.pickerStyle(.menu).frame(minHeight: 44).accessibilityIdentifier("plan-shape-picker")
        } else {
            picker.pickerStyle(.segmented).frame(minHeight: 44).accessibilityIdentifier("plan-shape-picker")
        }
    }

    // MARK: - Run bar

    private var runBar: some View {
        VStack(spacing: 8) {
            if controller.state.isPlanning {
                HStack(spacing: 12) {
                    ProgressView()
                    Text(planningText)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    Button("Cancel") { controller.cancel() }
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityIdentifier("plan-cancel-button")
                }
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("plan-progress")
            } else {
                if let issue = controller.inputs.issues.first {
                    Text(issue.message)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("plan-input-issue")
                }
                Button {
                    controller.plan(area: area, areas: areas, progress: progress, units: units)
                } label: {
                    Text("Plan Hike")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!controller.inputs.isValid)
                .accessibilityIdentifier("plan-run-button")
                .accessibilityHint("Finds the best hike inside your limits.")
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private var planningText: String {
        if case .planning(let s) = controller.state { return s }
        return ""
    }

    // MARK: - Result

    @ViewBuilder
    private var resultSection: some View {
        switch controller.state {
        case .idle, .planning:
            EmptyView()
        case .failed(let message):
            Section("Result") {
                Label(message, systemImage: "exclamationmark.triangle")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("plan-result")
            }
            .id(Self.resultAnchor)
        case .planned(let outcome, let context):
            let display = PlanResultDisplay.make(outcome: outcome, context: context, units: units)
            Section {
                resultBody(display)
            } header: {
                Text("Result")
            }
            .id(Self.resultAnchor)
        }
    }

    @ViewBuilder
    private func resultBody(_ d: PlanResultDisplay) -> some View {
        Text(d.headline)
            .font(.headline)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityAddTraits(.isHeader)
            .accessibilityIdentifier("plan-result")

        if let message = d.message {
            Text(message)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("plan-result-message")
        }

        if let replan = d.replan {
            Button {
                controller.replan(seconds: replan.seconds, area: area, areas: areas, progress: progress, units: units)
            } label: {
                Text(replan.buttonTitle)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("plan-replan-button")
        }

        if let route = d.route { routeView(route, showTitle: false) }
        if let fallback = d.fallback { routeView(fallback, showTitle: true) }

        if !d.alternatives.isEmpty {
            DisclosureGroup {
                ForEach(d.alternatives.indices, id: \.self) { i in
                    alternativeView(d.alternatives[i])
                }
            } label: {
                Text(d.alternatives.count == 1 ? "Show alternatives (1)" : "Show alternatives (\(d.alternatives.count))")
                    .frame(minHeight: 44, alignment: .leading)
            }
            .accessibilityIdentifier("plan-alternatives")
        }

        ForEach(d.notes, id: \.self) { note in
            Label(note, systemImage: "info.circle")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("plan-note")
        }
    }

    private func routeView(_ r: PlanResultDisplay.Route, showTitle: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if showTitle {
                Text(r.title).font(.subheadline.weight(.semibold))
            }
            if !r.trailNames.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Finishes")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    ForEach(r.trailNames.indices, id: \.self) { i in
                        Text(r.trailNames[i]).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            ForEach(r.stats, id: \.label) { stat in
                LabeledContent(stat.label, value: stat.value)
            }
            ForEach(r.flags, id: \.self) { flag in
                Label(flag, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(r.spoken)
        .accessibilityIdentifier(showTitle ? "plan-fallback" : "plan-route")
    }

    private func alternativeView(_ a: PlanResultDisplay.Alternative) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(a.title).font(.subheadline.weight(.semibold))
            Text(a.why).font(.footnote).foregroundStyle(.secondary)
            Text(a.route.stats.map(\.value).joined(separator: " · "))
                .font(.footnote)
            if !a.route.trailNames.isEmpty {
                Text(a.route.trailNames.joined(separator: ", "))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            ForEach(a.route.flags, id: \.self) { flag in
                Label(flag, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.vertical, 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(a.title). \(a.why). \(a.route.spoken)")
        .accessibilityIdentifier("plan-alternative-row")
    }
}
