import Foundation
import Testing
@testable import SouthMountainExplorer

/// The pure side of the Plan a Hike sheet ("Plan a Hike", Task 9).
struct PlanHikeViewModelTests {
    typealias K = PlannerKit

    private func lot(_ lat: Double, _ lon: Double, name: String? = nil, source: String? = nil) -> ParkingLot {
        ParkingLot(lat: lat, lon: lon, name: name, fee: nil, trailhead: nil, source: source)
    }

    private func personal(_ factor: Double = 0.9) -> PersonalPaceResult {
        PersonalPaceResult(factor: factor, movingSeconds: 7200, hikesUsed: 3)
    }

    private func pin(_ lat: Double, _ lon: Double) -> StartPoint { .pin(lat: lat, lon: lon) }

    /// A plan with chosen numbers; the steps are empty so flags come from `steps`.
    private func plan(
        names: [String], meters: Double = 4000, seconds: Double = 3600, gainFt: Double = 300,
        newMeters: Double = 3000, connectorMeters: Double = 0, bridgeMeters: Double = 0,
        steps: [RouteStep] = [], kinds: [TrailNetwork.EdgeKind] = []
    ) throws -> RoutePlan {
        let net = try K.view([K.trail("A", [(0, 0), (500, 0)])], start: (0, 0)).network
        var m = try #require(RouteMetrics(network: net, steps: []))
        m.meters = meters; m.seconds = seconds; m.gainFt = gainFt; m.newMeters = newMeters
        m.connectorMeters = connectorMeters; m.bridgeMeters = bridgeMeters
        return RoutePlan(
            steps: steps,
            stepInfo: kinds.map { .init(trailKey: nil, kind: $0, isGoalCover: false) },
            finished: names.enumerated().map {
                .init(key: K.key("T\($0.offset)"), name: $0.element, requiredMeters: 100, completedAtStep: 0)
            },
            metrics: m, shape: .loop, startNodeId: 0, endNodeId: 0, evaluations: 1)
    }

    // MARK: - Defaults and validation

    @Test func defaultValues() {
        let i = PlanHikeInputs()
        #expect(i.start == nil && i.end == nil)
        #expect(i.timeLimitMinutes == 120)
        #expect(i.timeLimitSeconds == 7200)
        #expect(!i.limitClimb)
        #expect(i.pace == .standard)
        #expect(i.shape == .loop)
        #expect(i.maxGainFt(units: .imperial) == nil)
    }

    @Test func timeOptionsAreSteppedAndKeepAnOffGridValue() {
        let o = PlanHikeInputs.baseTimeOptionsMinutes
        #expect(o.first == 15 && o.last == 480)
        #expect(o.contains(45) && o.contains(120) && o.contains(150) && o.contains(180))
        #expect(!o.contains(135) && !o.contains(165))
        #expect(o == o.sorted())
        var i = PlanHikeInputs()
        i.timeLimitMinutes = 95
        #expect(i.timeOptions().contains(95))
        #expect(i.timeOptions() == i.timeOptions().sorted())
        #expect(PlanHikeInputs().timeOptions() == o)
    }

    @Test func validationNeedsStartAndTime() {
        var i = PlanHikeInputs()
        #expect(i.issues == [.noStart])
        #expect(!i.isValid)
        i.start = pin(33.3, -112.1)
        #expect(i.isValid)
        i.timeLimitMinutes = 0
        #expect(i.issues == [.timeOutOfRange])
        i.timeLimitMinutes = 25 * 60
        #expect(i.issues == [.timeOutOfRange])
    }

    @Test func oneWayNeedsADifferentEnd() {
        var i = PlanHikeInputs()
        i.start = pin(33.3, -112.1)
        i.shape = .oneWay
        #expect(i.issues == [.noEnd])
        i.end = pin(33.3, -112.1)
        #expect(i.issues == [.endSameAsStart])
        i.end = pin(33.3, -112.0995)   // about 460 m east
        #expect(i.isValid)
        // Loop ignores the end.
        i.shape = .loop
        i.end = nil
        #expect(i.isValid)
    }

    @Test func climbLimitConvertsUnitsAndNormalizes() {
        var i = PlanHikeInputs()
        i.start = pin(33.3, -112.1)
        i.limitClimb = true
        i.climbValue = 1000
        #expect(i.maxGainFt(units: .imperial) == 1000)
        i.climbValue = 300
        #expect(abs((i.maxGainFt(units: .metric) ?? 0) - 984.252) < 0.01)
        // 1000 is not a metric option: normalizing falls back to the metric default.
        i.climbValue = 1000
        i.normalizeClimb(units: .metric)
        #expect(i.climbValue == 1000)   // 1000 m is a metric option
        i.climbValue = 250
        i.normalizeClimb(units: .metric)
        #expect(i.climbValue == PlanHikeInputs.defaultClimb(units: .metric))
        i.climbValue = 0
        #expect(i.issues == [.climbInvalid])
        #expect(i.maxGainFt(units: .imperial) == nil)
    }

    // MARK: - Pace

    @Test func myPaceIsOfferedOnlyWhenMeasured() {
        #expect(!PlanHikeInputs.offersMyPace(nil))
        #expect(PlanHikeInputs.offersMyPace(personal()))
        var i = PlanHikeInputs()
        i.pace = .mine
        i.reconcilePace(personal: nil)
        #expect(i.pace == .standard)
        i.pace = .mine
        i.reconcilePace(personal: personal())
        #expect(i.pace == .mine)
    }

    @Test func paceFactorFollowsTheChoice() {
        var i = PlanHikeInputs()
        #expect(i.paceFactor(personal: personal(0.8)) == 1.0)
        i.pace = .mine
        #expect(i.paceFactor(personal: personal(0.8)) == 0.8)
        #expect(i.paceFactor(personal: nil) == 1.0)
    }

    // MARK: - Formatting in both unit systems

    @Test func routeFormatsInImperialAndMetric() throws {
        let p = try plan(names: ["Alpha", "Beta"], meters: 8046.72, seconds: 5700, gainFt: 984.252, newMeters: 4828.032)
        let imp = PlanResultDisplay.route(p, title: "Your hike", units: .imperial)
        #expect(imp.stats.map(\.label) == ["Distance", "Time", "Climb", "New miles"])
        #expect(imp.stats.map(\.value) == ["5.00 mi", "1 h 35 m", "984 ft", "3.00 mi"])
        let met = PlanResultDisplay.route(p, title: "Your hike", units: .metric)
        #expect(met.stats.map(\.label) == ["Distance", "Time", "Climb", "New km"])
        #expect(met.stats.map(\.value) == ["8.05 km", "1 h 35 m", "300 m", "4.83 km"])
        #expect(imp.trailNames == ["Alpha", "Beta"])
        #expect(imp.spoken.contains("Finishes 2 trails: Alpha, Beta."))
        #expect(imp.spoken.contains("Distance 5.00 mi"))
    }

    // MARK: - Outcome mapping

    @Test func plannedOutcomeMapsToDisplay() throws {
        let best = try plan(names: ["Alpha", "Beta", "Gamma"], seconds: 6600)
        let alt = try plan(names: ["Alpha"], seconds: 2400)
        let outcome = PlanOutcome.planned(
            best: best, alternatives: [RouteAlternative(label: .shortest, plan: alt, why: "1 h 10 m shorter, 2 fewer trails")])
        let d = PlanResultDisplay.make(outcome: outcome, context: .init(), units: .imperial)
        #expect(d.kind == .planned)
        #expect(d.headline == "Finishes 3 trails in 1 h 50 m")
        #expect(d.route?.trailNames == ["Alpha", "Beta", "Gamma"])
        #expect(d.alternatives.count == 1)
        #expect(d.alternatives[0].title == "Shorter")
        #expect(d.alternatives[0].why == "1 h 10 m shorter, 2 fewer trails")
        #expect(d.alternatives[0].route.trailNames == ["Alpha"])
        #expect(d.replan == nil && d.fallback == nil && d.message == nil)
    }

    @Test func oneTrailHeadlineIsSingular() throws {
        let d = PlanResultDisplay.make(
            outcome: .planned(best: try plan(names: ["Solo"], seconds: 1800), alternatives: []),
            context: .init(), units: .imperial)
        #expect(d.headline == "Finishes 1 trail in 30 min")
    }

    @Test func nothingFitsOffersReplanAndFallback() throws {
        let fallback = try plan(names: [], seconds: 840, newMeters: 600)
        let n = NothingFits(
            limitSeconds: 900,
            closest: .init(key: K.key("B"), name: "Trail B", exactSeconds: 5560, roundedSeconds: 5700),
            fallback: fallback, message: "Nothing fits in 15 min. The closest finish is Trail B in 1 h 35 m.")
        let d = PlanResultDisplay.make(outcome: .nothingFits(n), context: .init(), units: .imperial)
        #expect(d.kind == .nothingFits)
        #expect(d.headline == "Nothing fits in 15 min")
        #expect(d.message == n.message)
        #expect(d.replan?.seconds == 5700)
        #expect(d.replan?.buttonTitle == "Plan for 1 h 35 m")
        #expect(d.fallback?.title == "Closest you can get")
        #expect(d.fallback?.trailNames == [])
        #expect(d.route == nil && d.alternatives.isEmpty)
    }

    @Test func nothingFitsWithoutAClosestTrailHasNoReplan() {
        let n = NothingFits(limitSeconds: 900, closest: nil, fallback: nil, message: "Nothing fits in 15 min.")
        let d = PlanResultDisplay.make(outcome: .nothingFits(n), context: .init(), units: .metric)
        #expect(d.replan == nil && d.fallback == nil)
    }

    @Test func replanAppliesTheRoundedTimeAndKeepsOtherInputs() {
        var i = PlanHikeInputs()
        i.start = pin(33.3, -112.1)
        i.limitClimb = true
        i.timeLimitMinutes = 15
        i.applyReplan(seconds: 5700)
        #expect(i.timeLimitMinutes == 95)
        #expect(i.limitClimb && i.start != nil)
        #expect(i.timeOptions().contains(95))
        i.applyReplan(seconds: 90_000)
        #expect(i.timeLimitMinutes == PlanHikeInputs.maxTimeMinutes)
        i.applyReplan(seconds: 61)
        #expect(i.timeLimitMinutes == 2)
    }

    @Test func noCandidatesShowsThePlannerMessage() {
        let c = NoCandidates(completed: 5, notReachable: 2, message: "There's no trail left to plan from here. 5 already completed, 2 not reachable from the start.")
        let d = PlanResultDisplay.make(outcome: .noCandidates(c), context: .init(), units: .imperial)
        #expect(d.kind == .noCandidates)
        #expect(d.headline == "Nothing to plan from here")
        #expect(d.message == c.message)
        #expect(d.message?.contains("5 already completed") == true)
        #expect(d.route == nil && d.replan == nil && d.alternatives.isEmpty)
    }

    // MARK: - Flags and notes

    @Test func flagsCountDistinctConnectorsAndBridges() throws {
        let steps = [
            RouteStep(edgeId: 0, direction: .forward), RouteStep(edgeId: 7, direction: .forward),
            RouteStep(edgeId: 7, direction: .backward), RouteStep(edgeId: 9, direction: .forward),
            RouteStep(edgeId: 12, direction: .forward),
        ]
        let p = try plan(
            names: ["A"], connectorMeters: 80.4672, bridgeMeters: 16.09344, steps: steps,
            kinds: [.trail, .connector, .connector, .bridge, .bridge])
        let (lines, count) = PlanResultDisplay.flagLines(p, units: .imperial)
        #expect(count == 3)
        #expect(lines == ["1 connector (0.05 mi off-trail)", "2 unverified connections (0.01 mi)"])
        let none = PlanResultDisplay.flagLines(try plan(names: ["A"]), units: .imperial)
        #expect(none.count == 0 && none.lines.isEmpty)
    }

    @Test func notesCoverSmallPartOfflineAndOffTrailStarts() {
        let small = PlanResultContext(startPart: .init(isLargest: false, lengthMeters: 2000, largestLengthMeters: 40_000))
        #expect(small.startIsInSmallPart)
        #expect(PlanResultDisplay.notes(small, units: .imperial)
            == ["Your start connects to a small part of the trail network, so options are limited."])
        let big = PlanResultContext(startPart: .init(isLargest: false, lengthMeters: 30_000, largestLengthMeters: 40_000))
        #expect(!big.startIsInSmallPart)
        let largest = PlanResultContext(startPart: .init(isLargest: true, lengthMeters: 40_000, largestLengthMeters: 40_000))
        #expect(!largest.startIsInSmallPart)
        #expect(PlanResultDisplay.notes(.init(), units: .imperial).isEmpty)

        let ctx = PlanResultContext(notDownloaded: 2, failedToLoad: 1, startOffTrailMeters: 160.9344)
        let notes = PlanResultDisplay.notes(ctx, units: .imperial)
        #expect(notes.count == 3)
        #expect(notes[0] == "Your start is 0.10 mi from the nearest trail. The route walks there in a straight line.")
        #expect(notes[1] == "2 nearby areas aren't downloaded and you're offline, so their trails weren't considered.")
        #expect(notes[2] == "1 nearby area couldn't be loaded, so its trails weren't considered.")
        #expect(PlanResultDisplay.notes(.init(notDownloaded: 1), units: .metric)
            == ["1 nearby area isn't downloaded and you're offline, so its trails weren't considered."])
    }

    @Test func startErrorsAreHonestAndUseUnits() {
        let far = PlanHikeMessages.startError(.tooFarFromTrails(3218.688), isEnd: false, units: .imperial)
        #expect(far == "Your start is 2.00 mi from the nearest trail. Pick a spot within 1.00 mi of a trail.")
        let farEnd = PlanHikeMessages.startError(.tooFarFromTrails(3000), isEnd: true, units: .metric)
        #expect(farEnd == "Your end is 3.00 km from the nearest trail. Pick a spot within 1.61 km of a trail.")
        #expect(PlanHikeMessages.startError(.noTrails, isEnd: false, units: .imperial) == "This area has no trails to plan on.")
        #expect(PlanHikeMessages.startError(.invalidCoordinate, isEnd: true, units: .imperial).contains("end location"))
    }

    // MARK: - Start list

    @Test func spotsNameLotsAndFallBackToNearestTrail() {
        let east = K.trail("E", [(0, 0), (1000, 0)])
        let north = K.trail("N", [(5000, 0), (5000, 1000)])
        let named = lot(K.ll(10, 5)[0], K.ll(10, 5)[1], name: "Bursera Trailhead")
        let nearE = lot(K.ll(500, 40)[0], K.ll(500, 40)[1])
        let nearN = lot(K.ll(5020, 900)[0], K.ll(5020, 900)[1])
        let blm = lot(K.ll(4990, 10)[0], K.ll(4990, 10)[1], source: "blm")
        let spots = PlanSpotBuilder.spots(lots: [nearN, nearE, named, blm], trails: [east, north])
        #expect(spots.count == 4)
        #expect(spots[0].title == "Bursera Trailhead")
        #expect(Set(spots.map(\.title)) == [
            "Bursera Trailhead", "Parking near Trail E", "Parking near Trail N", "Trailhead near Trail N"])
        let th = spots.first { $0.title == "Trailhead near Trail N" }
        if case .trailhead(let name, _, _)? = th?.point { #expect(name == "Trailhead near Trail N") } else { Issue.record("expected trailhead") }
        // Same inputs, same order.
        #expect(PlanSpotBuilder.spots(lots: [blm, named, nearE, nearN], trails: [east, north]) == spots)
    }

    @Test func duplicateSpotTitlesGetNumbers() {
        let t = K.trail("E", [(0, 0), (1000, 0)])
        let a = lot(K.ll(100, 30)[0], K.ll(100, 30)[1])
        let b = lot(K.ll(900, 30)[0], K.ll(900, 30)[1])
        let spots = PlanSpotBuilder.spots(lots: [a, b], trails: [t])
        #expect(spots.map(\.title).sorted() == ["Parking near Trail E", "Parking near Trail E (2)"])
        #expect(Set(spots.map(\.id)).count == 2)
        #expect(PlanSpotBuilder.spots(lots: [a], trails: []).first?.title == "Parking")
        #expect(PlanSpotBuilder.spots(lots: [], trails: [t]).isEmpty)
    }

    @Test func pinSpotDescribesItself() {
        let s = PlanSpotBuilder.pinSpot(lat: 33.30321, lon: -112.10337)
        #expect(s.id == "pin")
        #expect(s.title == "Dropped pin")
        #expect(s.point == .pin(lat: 33.30321, lon: -112.10337))
    }

    // MARK: - v2: friendly start, time chips, summary line

    @Test func timeChipsAreOneToFourHours() {
        #expect(PlanHikeInputs.timeChipMinutes == [60, 120, 180, 240])
        var i = PlanHikeInputs()
        #expect(i.timeIsChip)
        i.timeLimitMinutes = 95
        #expect(!i.timeIsChip)
        i.timeLimitMinutes = 240
        #expect(i.timeIsChip)
    }

    @Test func defaultStartUsesYourLocationNearATrail() throws {
        let t = K.trail("A", [(0, 0), (1000, 0)])
        let c = K.ll(200, 100)    // 100 m off the trail
        let spots = [PlanSpot(id: "t", point: .trailhead(name: "North TH", lat: K.ll(0, 0)[0], lon: K.ll(0, 0)[1]), title: "North TH")]
        let pick = try #require(PlanSpotBuilder.defaultStart(
            user: (c[0], c[1]), spots: spots, trails: [t], areaCenter: (c[0], c[1]), maxTrailMeters: 500))
        #expect(pick.title == "Your location")
        #expect(pick.point == .pin(lat: c[0], lon: c[1], name: "Your location"))
        #expect(pick.point.displayName == "Your location")
    }

    @Test func defaultStartFallsBackToTheNearestTrailhead() throws {
        let t = K.trail("A", [(0, 0), (1000, 0)])
        let far = K.ll(0, 50_000)
        let near = K.ll(900, 0), other = K.ll(0, 0)
        let spots = [
            PlanSpot(id: "p", point: .parking(lot(near[0], near[1], name: "Lot")), title: "Lot"),
            PlanSpot(id: "a", point: .trailhead(name: "Far TH", lat: other[0], lon: other[1]), title: "Far TH"),
            PlanSpot(id: "b", point: .trailhead(name: "Near TH", lat: near[0], lon: near[1]), title: "Near TH"),
        ]
        // No location: nearest trailhead to the area centre.
        let a = try #require(PlanSpotBuilder.defaultStart(
            user: nil, spots: spots, trails: [t], areaCenter: (near[0], near[1]), maxTrailMeters: 500))
        #expect(a.title == "Near TH")
        // Location far from every trail: nearest trailhead to the user.
        let b = try #require(PlanSpotBuilder.defaultStart(
            user: (far[0], far[1]), spots: spots, trails: [t], areaCenter: (other[0], other[1]), maxTrailMeters: 500))
        #expect(b.title == "Far TH" || b.title == "Near TH")
        #expect(PlanSpotBuilder.defaultStart(
            user: nil, spots: [], trails: [t], areaCenter: (near[0], near[1]), maxTrailMeters: 500) == nil)
    }

    @Test func startNamesNeverShowCoordinates() {
        #expect(PlanSpotBuilder.pinSpot(lat: 33.3664, lon: -112.0309).title == "Dropped pin")
        #expect(PlanSpotBuilder.hereSpot(lat: 33.3664, lon: -112.0309).title == "Your location")
    }

    @Test func summaryLineIsTrailDistanceTimeNewMiles() throws {
        let p = try plan(names: ["Kachina Access"], meters: 7.4 * 1609.344, seconds: 2 * 3600 + 57 * 60,
                         gainFt: 1963, newMeters: 1.0 * 1609.344)
        let r = PlanResultDisplay.route(p, title: "Your hike", units: .imperial, isBest: true)
        #expect(r.summary == "Kachina Access · 7.4 mi · 2 h 57 m · 1.0 new mi")
        #expect(r.climbLine == "1963 ft climb")
        #expect(r.lowValueNote?.hasPrefix("Most trails near here are done.") == true)
        let many = try plan(names: ["A", "B", "C"], meters: 4000, newMeters: 3000)
        #expect(PlanResultDisplay.route(many, title: "t", units: .imperial).summary.hasPrefix("A +2 more · "))
        #expect(PlanResultDisplay.route(many, title: "t", units: .imperial).lowValueNote == nil)
        let none = try plan(names: [], meters: 4000, newMeters: 3000)
        #expect(PlanResultDisplay.route(none, title: "t", units: .imperial).summary.hasPrefix("Your hike · "))
    }

    @Test func lowValueNoteMatchesTheBrief() throws {
        let p = try plan(names: ["Kachina Access"], meters: 7.36 * 1609.344, seconds: 10620, newMeters: 0.98 * 1609.344)
        let r = PlanResultDisplay.route(p, title: "Your hike", units: .imperial, isBest: true, offersShorter: true)
        #expect(r.lowValueNote == "Most trails near here are done. Best is 0.98 new mi in 7.4 mi. Try Shorter for a closer option.")
        let metric = PlanResultDisplay.route(p, title: "Your hike", units: .metric, isBest: true)
        #expect(metric.lowValueNote?.contains("new km in") == true)
    }

    // MARK: - End to end through the real planner

    @Test func realPlannerOutcomeMapsToDisplay() throws {
        let trails = [K.trail("A", [(0, 0), (1500, 0)]), K.trail("B", [(500, 0), (500, 600)])]
        let req = PlanOutcomeRequest(
            network: try K.view(trails, start: (0, 0)), trails: K.inputs(trails), timeLimitSeconds: 7200)
        let d = PlanResultDisplay.make(outcome: RoutePlanningService.planOutcome(req), context: .init(), units: .imperial)
        #expect(d.kind == .planned)
        #expect(d.route?.trailNames.count == 2)
        #expect(d.route?.stats.count == 4)

        let tiny = PlanOutcomeRequest(
            network: try K.view(trails, start: (0, 0)), trails: K.inputs(trails), timeLimitSeconds: 1200)
        let n = PlanResultDisplay.make(outcome: RoutePlanningService.planOutcome(tiny), context: .init(), units: .imperial)
        #expect(n.kind == .nothingFits)
        #expect(n.headline == "Nothing fits in 20 min")
        var inputs = PlanHikeInputs()
        inputs.timeLimitMinutes = 20
        let replanSeconds = try #require(n.replan).seconds
        inputs.applyReplan(seconds: replanSeconds)
        #expect(inputs.timeLimitSeconds >= replanSeconds - 59)
        #expect(inputs.timeLimitSeconds > 1200)
    }
}
