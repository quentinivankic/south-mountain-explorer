import SwiftUI

/// "Plan a Hike": a start, a time, and one Plan button. Extras (climb limit,
/// pace, loop or one-way) sit under "More options". A finished plan replaces
/// the form with `PlanRouteCard` and is drawn on the map right away.
struct PlanHikeSheet: View {
    let area: Area
    let controller: PlanHikeController
    /// Closes the sheet and lets the user tap the map for a start or end.
    let onDropPin: (PlanPinTarget) -> Void
    /// Starts a normal recording (Task 10). The caller closes the sheet if it began.
    let onStartHike: () -> Void
    let onClose: () -> Void

    @Environment(AreaDataService.self) private var areas
    @Environment(ProgressService.self) private var progress
    @Environment(RecordingService.self) private var recording
    @Environment(LocationService.self) private var location
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial
    @State private var showMore = false
    @State private var customTime = false

    private static let dropID = "drop"

    var body: some View {
        NavigationStack {
            Group {
                if controller.preview.isShowing, case .planned(let outcome, let context) = controller.state {
                    PlanRouteCard(
                        controller: controller,
                        display: PlanResultDisplay.make(outcome: outcome, context: context, units: units),
                        gate: PlanStartGate.evaluate(
                            hasActiveRecording: recording.activeRecording != nil,
                            hasRecoveryIssue: recording.recoveryIssue != nil),
                        onStart: onStartHike)
                } else {
                    formView
                }
            }
            .navigationTitle(controller.preview.isShowing ? "Your route" : "Plan a Hike")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { onClose() }
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityIdentifier("plan-done-button")
                }
            }
        }
        .background(WindowFrameReader { controller.updateCardFrame($0) })
        .task {
            let here = location.userLocation.map { (lat: $0.latitude, lon: $0.longitude) }
            await controller.prepare(area: area, recording: recording, units: units, userLocation: here)
        }
        .onChange(of: controller.inputs) { _, _ in controller.inputsChanged() }
        .onDisappear {
            controller.cancel()
            // Closing the plan removes the route, unless Start hike began a
            // recording (then it stays as a passive guide).
            if !controller.hikeStarted { controller.clearRoute() }
        }
    }

    private var formView: some View {
        Form {
            Section {
                startRow
                timeRow
            }
            Section {
                DisclosureGroup(isExpanded: $showMore) {
                    climbRows
                    paceRows
                    shapeRows
                } label: {
                    Text("More options")
                        .frame(minHeight: 44, alignment: .leading)
                }
                .accessibilityIdentifier("plan-more-options")
            }
            outcomeSection
        }
        .contentMargins(.top, 4, for: .scrollContent)
        .safeAreaInset(edge: .bottom, spacing: 0) { runBar }
    }

    // MARK: - Start

    private var startRow: some View {
        spotPicker(
            title: "Start", id: "plan-start-picker",
            specials: [controller.hereSpot, controller.startPin].compactMap { $0 }, target: .start,
            selection: controller.inputs.start,
            set: { controller.inputs.start = $0 })
    }

    /// `specials` are the options that are not in the lot list: your location
    /// and the dropped pin.
    private func spotPicker(
        title: String, id: String, specials: [PlanSpot], target: PlanPinTarget,
        selection: StartPoint?, set: @escaping (StartPoint?) -> Void
    ) -> some View {
        let all = specials + controller.spots
        let current: String? = {
            guard let selection else { return nil }
            return all.first { $0.point == selection }?.id
        }()
        let hasPin = specials.contains { $0.id == "pin" }
        let binding = Binding<String?>(
            get: { current },
            set: { new in
                switch new {
                case Self.dropID?: onDropPin(target)
                case let spotID?: set(all.first { $0.id == spotID }?.point)
                case nil: set(nil)
                }
            })
        return Picker(title, selection: binding) {
            Text("Choose…").tag(String?.none)
            // Special options first: the lot list can run to dozens of rows.
            ForEach(specials) { spot in Text(spot.title).tag(Optional(spot.id)) }
            Text(hasPin ? "Move the pin…" : "Drop a pin…").tag(Optional(Self.dropID))
            ForEach(controller.spots) { spot in Text(spot.title).tag(Optional(spot.id)) }
        }
        .pickerStyle(.menu)
        .frame(minHeight: 44)
        .accessibilityIdentifier(id)
        .accessibilityHint("Choose a trailhead, a parking spot, or drop a pin on the map.")
    }

    // MARK: - Time

    private var timeRow: some View {
        @Bindable var controller = controller
        let current = controller.inputs.timeLimitMinutes
        let customSelected = customTime || !controller.inputs.timeIsChip
        let layout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 8))
        return VStack(alignment: .leading, spacing: 8) {
            Text("Time")
            layout {
                ForEach(PlanHikeInputs.timeChipMinutes, id: \.self) { m in
                    PlanChip(
                        title: PlanText.duration(Double(m) * 60),
                        isSelected: !customSelected && current == m,
                        spokenLabel: "\(PlanText.duration(Double(m) * 60)) time limit",
                        identifier: "plan-time-\(m)",
                        fillsWidth: dynamicTypeSize.isAccessibilitySize
                    ) {
                        customTime = false
                        controller.inputs.timeLimitMinutes = m
                    }
                }
                PlanChip(
                    title: "Custom", isSelected: customSelected,
                    spokenLabel: "Custom time limit", identifier: "plan-time-custom",
                    fillsWidth: dynamicTypeSize.isAccessibilitySize
                ) { customTime = true }
            }
            if customSelected {
                Picker("Custom time", selection: $controller.inputs.timeLimitMinutes) {
                    ForEach(controller.inputs.timeOptions(), id: \.self) { m in
                        Text(PlanText.duration(Double(m) * 60)).tag(m)
                    }
                }
                .pickerStyle(.menu)
                .frame(minHeight: 44)
                .accessibilityIdentifier("plan-time-limit")
            }
        }
        .padding(.vertical, 4)
    }

    // MARK: - More options

    @ViewBuilder
    private var climbRows: some View {
        @Bindable var controller = controller
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
    }

    @ViewBuilder
    private var paceRows: some View {
        @Bindable var controller = controller
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
            Text("\"My pace\" appears after a few recorded hikes.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var shapeRows: some View {
        @Bindable var controller = controller
        shapePicker($controller.inputs.shape)
        if controller.inputs.shape == .oneWay {
            spotPicker(
                title: "End", id: "plan-end-picker",
                specials: [controller.endPin].compactMap { $0 }, target: .end,
                selection: controller.inputs.end,
                set: { controller.inputs.end = $0 })
        }
        Text(controller.inputs.shape == .loop
             ? "Loop returns to where you started."
             : "One-way finishes at the end you pick.")
            .font(.footnote)
            .foregroundStyle(.secondary)
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

    // MARK: - Outcome without a route

    /// Errors, "nothing fits" and "nothing to plan". A planned route is shown
    /// by `PlanRouteCard` instead.
    @ViewBuilder
    private var outcomeSection: some View {
        switch controller.state {
        case .idle, .planning:
            EmptyView()
        case .failed(let message):
            Section {
                Label(message, systemImage: "exclamationmark.triangle")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("plan-result")
            }
        case .planned(let outcome, let context):
            let d = PlanResultDisplay.make(outcome: outcome, context: context, units: units)
            Section {
                outcomeBody(d)
            }
        }
    }

    @ViewBuilder
    private func outcomeBody(_ d: PlanResultDisplay) -> some View {
        // The message already opens with the headline; show one or the other.
        if let message = d.message {
            Text(message)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("plan-result-message")
        } else {
            Text(d.headline)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("plan-result")
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

        if let fallback = d.fallback {
            VStack(alignment: .leading, spacing: 4) {
                Text(fallback.title).font(.subheadline.weight(.semibold))
                Text(fallback.summary).font(.subheadline)
                Text(fallback.climbLine).font(.footnote).foregroundStyle(.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(fallback.spoken)
            .accessibilityIdentifier("plan-fallback")
        }

        ForEach(d.notes, id: \.self) { note in
            Label(note, systemImage: "info.circle")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("plan-note")
        }
    }
}

/// Reports its view's frame in WINDOW coordinates. SwiftUI's `.global` space is
/// relative to the sheet's own container, so it cannot say where the sheet sits
/// on screen; converting the hosting view's bounds to the window can. A display
/// link polls while the view is on screen so a detent animation is followed.
struct WindowFrameReader: UIViewRepresentable {
    let onChange: (CGRect) -> Void

    func makeUIView(context: Context) -> ProbeView {
        let v = ProbeView()
        v.isUserInteractionEnabled = false
        v.onChange = onChange
        return v
    }

    func updateUIView(_ view: ProbeView, context: Context) { view.onChange = onChange }

    final class ProbeView: UIView {
        var onChange: ((CGRect) -> Void)?
        private var link: CADisplayLink?
        private var last: CGRect = .null

        override func didMoveToWindow() {
            super.didMoveToWindow()
            link?.invalidate()
            link = nil
            guard window != nil else { return }
            let l = CADisplayLink(target: self, selector: #selector(poll))
            l.preferredFrameRateRange = CAFrameRateRange(minimum: 10, maximum: 30)
            l.add(to: .main, forMode: .common)
            link = l
            poll()
        }

        @objc private func poll() {
            guard window != nil else { return }
            let r = convert(bounds, to: nil)
            guard r != last, r.width > 1, r.height > 1 else { return }
            last = r
            onChange?(r)
        }

    }
}
