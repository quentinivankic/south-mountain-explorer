import Foundation
import Observation

/// Which end of the hike a dropped pin sets.
enum PlanPinTarget: Equatable {
    case start
    case end
}

/// Runs "Plan a Hike" for the area screen. Owned by `AreaView` so the inputs
/// survive closing the sheet (for example while the user taps the map to drop
/// a pin). All planning work happens off the main actor.
@MainActor
@Observable
final class PlanHikeController {
    enum RunState {
        case idle
        case planning(String)
        case planned(PlanOutcome, PlanResultContext)
        case failed(String)

        var isPlanning: Bool { if case .planning = self { return true } else { return false } }
        var isIdle: Bool { if case .idle = self { return true } else { return false } }
    }

    var inputs = PlanHikeInputs()
    private(set) var state = RunState.idle {
        didSet {
            if case .planned = state { resultVersion &+= 1 } else if case .failed = state { resultVersion &+= 1 }
            // Any new state drops the old route; `plan` loads the new one after.
            preview = PlanPreview()
            hikeStarted = false
            walkThrough = PlanWalkThrough()
        }
    }
    /// "Walk through" (round 7): the numbered leg shown on its own, bright,
    /// with the rest of the route faded. Inactive shows the whole route.
    private(set) var walkThrough = PlanWalkThrough()
    /// Index into the drawn route's `steps` that is highlighted and zoomed to.
    /// nil shows the whole route.
    var selectedStep: Int? { walkThrough.index }
    /// Bumped when Walk through starts, so the sheet can drop to the slim bar
    /// and leave the map in view.
    private(set) var walkThroughStartTick = 0
    /// Which planned route is drawn on the map (Task 10).
    private(set) var preview = PlanPreview()
    /// The Plan sheet's frame in window coordinates, measured by the sheet.
    /// Drives the camera fit insets; nil until the sheet reports it.
    private(set) var cardFrame: CGRect?
    func updateCardFrame(_ frame: CGRect) {
        guard frame.width > 1, frame.height > 1 else { return }
        if let c = cardFrame, abs(c.minX - frame.minX) < 1, abs(c.minY - frame.minY) < 1,
           abs(c.width - frame.width) < 1, abs(c.height - frame.height) < 1 { return }
        cardFrame = frame
    }
    /// Bumped when the camera should fit the drawn route.
    private(set) var fitTick = 0
    /// Set when Start hike began a recording; the route stays up as a passive guide.
    private(set) var hikeStarted = false
    /// Bumped each time a result (or error) lands, so the sheet can scroll to it.
    private(set) var resultVersion = 0
    private(set) var spots: [PlanSpot] = []
    private(set) var personalPace: PersonalPaceResult?
    /// Dropped pins keep their own list entry so the pickers can show them.
    private(set) var startPin: PlanSpot?
    private(set) var endPin: PlanSpot?
    /// The user's own position, offered as a start when it is near a trail.
    private(set) var hereSpot: PlanSpot?

    private var loader: PlanningNetworkLoader?
    private var preparedAreaId: String?
    private var run: Task<Void, Never>?
    private var lastPlanned: PlanHikeInputs?
    /// Saved hikes, read at plan time so only ground no track walked counts as new.
    private weak var recording: RecordingService?

    /// Spot list and personal pace, once per area. Cheap to call on every open.
    func prepare(
        area: Area, recording: RecordingService, units: UnitsPreference,
        userLocation: (lat: Double, lon: Double)? = nil
    ) async {
        inputs.normalizeClimb(units: units)
        self.recording = recording
        if preparedAreaId != area.id {
            preparedAreaId = area.id
            let lots = ParkingPoolService.shared.merged(with: area.parking, forAnyOf: area.trails, within: 400)
            spots = PlanSpotBuilder.spots(lots: lots, trails: area.trails)
            inputs = PlanHikeInputs()
            inputs.normalizeClimb(units: units)
            startPin = nil
            endPin = nil
            hereSpot = nil
            state = .idle
        }
        applyDefaultStart(area: area, userLocation: userLocation)
        let pace = await PersonalPace.load(using: recording)
        personalPace = pace
        inputs.reconcilePace(personal: pace)
    }

    /// Fills an empty start: the user's location if it is near a trail, else
    /// the nearest trailhead. Never replaces a start the user chose.
    func applyDefaultStart(area: Area, userLocation: (lat: Double, lon: Double)?) {
        if let u = userLocation,
           let d = PlanSpotBuilder.nearestTrailMeters(lat: u.lat, lon: u.lon, trails: area.trails),
           d <= Self.maxHereMeters {
            hereSpot = PlanSpotBuilder.hereSpot(lat: u.lat, lon: u.lon)
        } else {
            hereSpot = nil
        }
        guard inputs.start == nil else { return }
        let pick = PlanSpotBuilder.defaultStart(
            user: userLocation, spots: spots, trails: area.trails,
            areaCenter: (area.centerLat, area.centerLon), maxTrailMeters: Self.maxHereMeters)
        inputs.start = pick?.point
    }

    /// The user's position counts as "at the trails" within this distance.
    static let maxHereMeters = 500.0

    func setPin(_ target: PlanPinTarget, lat: Double, lon: Double) {
        let spot = PlanSpotBuilder.pinSpot(lat: lat, lon: lon)
        switch target {
        case .start: startPin = spot; inputs.start = spot.point
        case .end: endPin = spot; inputs.end = spot.point
        }
    }

    /// Selecting a different input drops a result that no longer matches it.
    func inputsChanged() {
        guard let lastPlanned, inputs != lastPlanned, !state.isIdle else { return }
        cancel()
        state = .idle
    }

    // MARK: Route preview

    /// Wait before the second fit, so it lands after the sheet detent animation.
    var settleDelay: Duration = .milliseconds(700)
    private var settleFit: Task<Void, Never>?
    func showOnMap() {
        preview.show()
        guard preview.isShowing else { return }
        fitTick &+= 1
        // Insets assume the sheet's resting height; refit once it has settled.
        settleFit?.cancel()
        settleFit = Task { [weak self] in
            guard let delay = self?.settleDelay else { return }
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled, let self, self.preview.isShowing else { return }
            self.fitTick &+= 1
        }
    }

    /// Back to the inputs from a result: removes the route and keeps the inputs.
    func edit() {
        cancel()
        clearRoute()
        state = .idle
    }

    func selectRoute(id: String) {
        if preview.select(id: id) {
            walkThrough = PlanWalkThrough()
            fitTick &+= 1
        }
    }

    /// Tapping a step in the list: Walk through from that step. nil goes
    /// back to the whole route.
    func selectStep(_ index: Int?) {
        guard let index else { exitWalkThrough(); return }
        startWalkThrough(at: index)
    }

    /// Starts Walk through at `step` (0-based): that leg bright and framed,
    /// the rest faded.
    func startWalkThrough(at step: Int = 0) {
        let wasActive = walkThrough.isActive
        var w = PlanWalkThrough(count: preview.selected?.route.steps.count ?? 0)
        guard w.enter(at: step) else { return }
        walkThrough = w
        refit()
        if !wasActive {
            walkThroughStartTick &+= 1
            // The sheet drops to the bar; frame the leg again once it settles.
            settleRefit()
        }
    }

    func nextStep() { if walkThrough.next() { refit() } }
    func previousStep() { if walkThrough.back() { refit() } }

    /// Leaves Walk through and shows the whole route again.
    func exitWalkThrough() {
        guard walkThrough.isActive else { return }
        walkThrough.exit()
        refit()
    }

    private func refit() {
        settleFit?.cancel()
        fitTick &+= 1
    }

    private func settleRefit() {
        settleFit = Task { [weak self] in
            guard let delay = self?.settleDelay else { return }
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled, let self, self.preview.isShowing else { return }
            self.fitTick &+= 1
        }
    }

    /// The leg the camera should frame: the selected step, else nil (whole route).
    var focusCoords: [PlannedRoute.Coord]? {
        guard let i = selectedStep, let steps = preview.selected?.route.steps, i < steps.count else { return nil }
        return steps[i].coords
    }

    /// Test seam: load route options without running the planner.
    func loadPreviewForTesting(_ options: [PlanPreview.Option]) { preview.load(options) }

    func clearRoute() {
        settleFit?.cancel()
        settleFit = nil
        preview.clear()
        walkThrough = PlanWalkThrough()
        hikeStarted = false
    }

    func markHikeStarted() { hikeStarted = true }

    /// Closing the sheet stops a running plan; a finished result stays.
    func cancel() {
        run?.cancel()
        run = nil
        if state.isPlanning { state = .idle }
    }

    func replan(seconds: Double, area: Area, areas: AreaDataService, progress: ProgressService, units: UnitsPreference) {
        inputs.applyReplan(seconds: seconds)
        plan(area: area, areas: areas, progress: progress, units: units)
    }

    func plan(area: Area, areas: AreaDataService, progress: ProgressService, units: UnitsPreference) {
        guard inputs.isValid, let start = inputs.start else { return }
        cancel()
        let snapshot = inputs
        lastPlanned = snapshot
        let personal = personalPace
        let recording = self.recording
        state = .planning("Loading trails…")
        if loader == nil { loader = PlanningNetworkLoader(areas: areas) }
        let loader = self.loader!
        run = Task { [weak self] in
            let paceFactor = snapshot.paceFactor(personal: personal)
            let input = await loader.load(
                primary: area, startLat: start.lat, startLon: start.lon,
                budgetSeconds: snapshot.timeLimitSeconds, shape: snapshot.shape, paceFactor: paceFactor)
            guard !Task.isCancelled, let self else { return }
            self.state = .planning("Planning your hike…")
            // Cheap value copies on main; the fingerprint hashing runs off main.
            let completions = progress.completions
            let fingerprints = progress.completedFingerprints
            let merged = input.merged
            let history = await recording?.loadHistory() ?? []
            guard !Task.isCancelled else { return }
            let completion = await Task.detached(priority: .userInitiated) {
                // Whole trails marked complete, plus the ground the saved tracks walked
                // (the same tracks the progress bars come from).
                let walked = WalkedGround(paths: history.map(\.path))
                return merged.completion(TrailCompletionLookup.snapshot(
                    completions: completions, fingerprints: fingerprints, trails: merged.allInputs)
                    .with(walked: walked))
            }.value
            guard !Task.isCancelled else { return }
            let request = PlanRun(
                network: input.network, trails: input.merged.inputs, completion: completion,
                start: start, end: snapshot.shape == .oneWay ? snapshot.end : nil,
                limit: snapshot.timeLimitSeconds, maxGainFt: snapshot.maxGainFt(units: units),
                paceFactor: paceFactor, shape: snapshot.shape,
                notDownloaded: input.notDownloaded.count, failed: input.failed.count)
            // Cancel flips the token so the planner loops stop; a cancelled run yields nil.
            guard let result = await PlanCancellation.runDetached({ request.execute() }),
                  !Task.isCancelled else { return }
            switch result {
            case .success(let done):
                self.state = .planned(done.outcome, done.context)
                self.preview.load(done.options)
                // The route goes on the map as soon as it is ready.
                self.showOnMap()
            case .failure(let err): self.state = .failed(PlanHikeMessages.startError(err.error, isEnd: err.isEnd, units: units))
            }
            self.run = nil
        }
    }
}

/// Everything the planning step needs, plain values, so it can run anywhere.
private struct PlanRun: Sendable {
    struct Done: Sendable {
        let outcome: PlanOutcome
        let context: PlanResultContext
        let options: [PlanPreview.Option]
    }
    struct Failure: Error, Sendable {
        let error: TrailNetworkStartError
        let isEnd: Bool
    }

    let network: TrailNetwork
    let trails: [TrailNetwork.Input]
    let completion: TrailCompletionLookup
    let start: StartPoint
    let end: StartPoint?
    let limit: Double
    let maxGainFt: Double?
    let paceFactor: Double
    let shape: RoutePlanRequest.Shape
    let notDownloaded: Int
    let failed: Int

    func execute() -> Result<Done, Failure> {
        let snapper = TrailNetworkSnapper(network: network)
        // Snap each separately first so an error names the right end.
        do { _ = try snapper.snap(start) } catch let e as TrailNetworkStartError { return .failure(Failure(error: e, isEnd: false)) } catch { return .failure(Failure(error: .noTrails, isEnd: false)) }
        if let end {
            do { _ = try snapper.snapEnd(end) } catch let e as TrailNetworkStartError { return .failure(Failure(error: e, isEnd: true)) } catch { return .failure(Failure(error: .noTrails, isEnd: true)) }
        }
        guard let view = try? snapper.insert(start: start, end: end) else {
            return .failure(Failure(error: .noTrails, isEnd: false))
        }
        let request = PlanOutcomeRequest(
            network: view, trails: trails, completion: completion, timeLimitSeconds: limit,
            maxGainFt: maxGainFt, paceFactor: paceFactor, shape: shape)
        let outcome = RoutePlanningService.planOutcome(request)
        let largest = snapper.components.first?.lengthMeters ?? 0
        let part = view.start.snap.component
        let context = PlanResultContext(
            notDownloaded: notDownloaded, failedToLoad: failed,
            startPart: .init(isLargest: part.isLargest, lengthMeters: part.lengthMeters, largestLengthMeters: largest),
            startOffTrailMeters: view.start.connectorMeters,
            endOffTrailMeters: view.end?.connectorMeters ?? 0)
        var options: [PlanPreview.Option] = []
        if case .planned(let best, let alts) = outcome {
            let edges = view.network.edges
            let names = Dictionary(trails.map { ($0.key, $0.trail.name) }, uniquingKeysWith: { first, _ in first })
            func route(_ plan: RoutePlan) -> PlannedRoute {
                PlannedRouteBuilder.build(plan: plan, edges: edges, completion: completion)
                    .with(steps: PlanStepBuilder.steps(plan: plan, network: view.network, names: names))
            }
            options = PlanPreview.options(
                best: route(best),
                alternatives: alts.map { ($0.label.title, route($0.plan)) })
        }
        return .success(Done(outcome: outcome, context: context, options: options))
    }
}
