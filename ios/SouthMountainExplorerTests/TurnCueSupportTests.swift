import Foundation
import Testing
@testable import SouthMountainExplorer

struct TurnCuePhrasingTests {
    @Test func metricRoundsToTenMetres() {
        #expect(TurnCuePhrasing.spokenDistance(meters: 76, units: .metric) == "80 meters")
        #expect(TurnCuePhrasing.spokenDistance(meters: 94, units: .metric) == "90 meters")
        #expect(TurnCuePhrasing.spokenDistance(meters: 3, units: .metric) == "10 meters")
    }

    @Test func imperialRoundsToFiftyFeetWithAHundredFootFloor() {
        #expect(TurnCuePhrasing.spokenDistance(meters: 76, units: .imperial) == "250 feet")
        #expect(TurnCuePhrasing.spokenDistance(meters: 20, units: .imperial) == "100 feet")
        #expect(TurnCuePhrasing.spokenDistance(meters: 100, units: .imperial) == "350 feet")
    }

    @Test func approachSentences() {
        #expect(TurnCuePhrasing.approach(symbol: .keepLeft, meters: 80, trailName: "National Trail", units: .metric)
            == "In 80 meters, keep left to stay on National Trail")
        #expect(TurnCuePhrasing.approach(symbol: .turnRight, meters: 76, trailName: "National Trail", units: .imperial)
            == "In 250 feet, turn right to stay on National Trail")
        #expect(TurnCuePhrasing.approach(symbol: .sharpLeft, meters: 60, trailName: "", units: .metric)
            == "In 60 meters, sharp left")
        #expect(TurnCuePhrasing.near(symbol: .sharpRight) == "Sharp right")
        #expect(TurnCuePhrasing.offTrail(trailName: "") == "You may be off the trail")
        #expect(TurnCuePhrasing.backOnTrail(trailName: "Alta") == "Back on Alta")
    }

    @Test func shortDistancesForTheBanner() {
        #expect(TurnCuePhrasing.shortDistance(meters: 82, units: .metric) == "80 m")
        #expect(TurnCuePhrasing.shortDistance(meters: 4, units: .metric) == "Now")
        #expect(TurnCuePhrasing.shortDistance(meters: 76, units: .imperial) == "250 ft")
        #expect(TurnCuePhrasing.shortDistance(meters: 5, units: .imperial) == "Now")
    }
}

struct TurnCueDispatcherTests {
    private func event(_ kind: TurnCueEvent.Kind = .approach, symbol: TurnCueSymbol = .keepLeft) -> TurnCueEvent {
        TurnCueEvent(kind: kind, junctionId: 1, symbol: symbol, spoken: "Keep left", shortText: "Keep left", distanceMeters: 30)
    }

    private func ctx(voice: Bool = true, haptics: Bool = true, vo: Bool = false, active: Bool = true, call: Bool = false) -> TurnCueDeliveryContext {
        .init(voiceEnabled: voice, hapticsEnabled: haptics, voiceOverRunning: vo, appActive: active, callActive: call)
    }

    @Test func defaultDeliverySpeaksBannersAndBuzzes() {
        let e = event()
        #expect(TurnCueDispatcher.plan(e, ctx()) == [.banner(e), .haptic(.approach), .speak("Keep left")])
    }

    @Test func voiceOverInAppAnnouncesInsteadOfSpeaking() {
        let actions = TurnCueDispatcher.plan(event(), ctx(vo: true, active: true))
        #expect(actions.contains(.announce("Keep left")))
        #expect(!actions.contains(.speak("Keep left")))
    }

    @Test func voiceOverWhileLockedFallsBackToSpeech() {
        let actions = TurnCueDispatcher.plan(event(), ctx(vo: true, active: false))
        #expect(actions.contains(.speak("Keep left")))
        #expect(!actions.contains(.announce("Keep left")))
    }

    @Test func voiceOffMeansNoAudioOfEitherKind() {
        for vo in [false, true] {
            let actions = TurnCueDispatcher.plan(event(), ctx(voice: false, vo: vo))
            #expect(!actions.contains { if case .speak = $0 { true } else if case .announce = $0 { true } else { false } })
            #expect(actions.contains(.haptic(.approach)))
        }
    }

    @Test func hapticsOffMeansNoHaptic() {
        let actions = TurnCueDispatcher.plan(event(), ctx(haptics: false))
        #expect(!actions.contains { if case .haptic = $0 { true } else { false } })
        #expect(actions.contains(.speak("Keep left")))
    }

    @Test func bothOffStillShowsTheBanner() {
        let e = event()
        #expect(TurnCueDispatcher.plan(e, ctx(voice: false, haptics: false)) == [.banner(e)])
    }

    @Test func activeCallBlocksAudioButKeepsHapticAndBanner() {
        let e = event()
        #expect(TurnCueDispatcher.plan(e, ctx(call: true)) == [.banner(e), .haptic(.approach)])
    }

    @Test func hapticPatternsAreDistinctPerCue() {
        #expect(TurnCueDispatcher.hapticPattern(for: event(.atJunction, symbol: .turnLeft)) == .nearLeft)
        #expect(TurnCueDispatcher.hapticPattern(for: event(.atJunction, symbol: .keepRight)) == .nearRight)
        #expect(TurnCueDispatcher.hapticPattern(for: event(.offTrail, symbol: .offTrail)) == .offTrail)
        #expect(TurnCueDispatcher.hapticPattern(for: event(.backOnTrail, symbol: .backOnTrail)) == .backOnTrail)
        #expect(TurnCueDispatcher.hapticPattern(for: event(.gpsWeak, symbol: .gpsWeak)) == nil)
    }

    @Test func settingsDefaultToOnWhenUnset() throws {
        let defaults = try #require(UserDefaults(suiteName: "turn-cue-tests-\(UUID().uuidString)"))
        #expect(TurnCueDispatcher.boolSetting(StorageKeys.turnCueVoice, defaults: defaults))
        #expect(TurnCueDispatcher.boolSetting(StorageKeys.turnCueHaptics, defaults: defaults))
        defaults.set(false, forKey: StorageKeys.turnCueVoice)
        #expect(!TurnCueDispatcher.boolSetting(StorageKeys.turnCueVoice, defaults: defaults))
        #expect(TurnCueDispatcher.boolSetting(StorageKeys.turnCueHaptics, defaults: defaults))
        #expect(!StorageKeys.resetAllKeys.contains(StorageKeys.turnCueVoice))
    }
}

struct TurnCueRouteBuilderTests {
    typealias F = CueFixtures

    @Test func shuffledAndReversedSegmentsStitchIntoOneChain() {
        let a: [F.XY] = [(0, 0), (0, 100)]
        let b: [F.XY] = [(0, 200), (0, 100)] // reversed
        let c: [F.XY] = [(0, 200), (0, 300)]
        let t = F.trail("t", "T", segments: [c, a, b])
        let route = TurnCueRouteBuilder.build(selected: t, others: [])
        #expect(route.chains.count == 1)
        #expect(abs(route.chains[0].length - 300) < 1)
        #expect(!route.chains[0].isLoop)
    }

    @Test func gapsBeyondStitchToleranceStayApart() {
        let t = F.trail("t", "T", segments: [[(0, 0), (0, 100)], [(0, 120), (0, 220)]])
        let route = TurnCueRouteBuilder.build(selected: t, others: [])
        #expect(route.chains.count == 2)
        let near = F.trail("t", "T", segments: [[(0, 0), (0, 100)], [(0, 108), (0, 208)]])
        #expect(TurnCueRouteBuilder.build(selected: near, others: []).chains.count == 1)
    }

    @Test func closedWayIsALoop() {
        let t = F.trail("l", "L", [(0, 0), (0, 100), (100, 100), (100, 0), (3, 0)])
        let route = TurnCueRouteBuilder.build(selected: t, others: [])
        #expect(route.chains[0].isLoop)
    }

    @Test func yAndTBranchBearingsAreCorrect() {
        let tJunction = F.trail("e", "East", [(0, 300), (100, 300)])
        let route = TurnCueRouteBuilder.build(selected: F.main(), others: [tJunction])
        #expect(route.junctions.count == 1)
        let j = route.junctions[0]
        #expect(abs(j.arc - 300) < 8)
        #expect(j.branches.count == 1)
        #expect(abs(TurnCueGeometry.signedAngleDiff(from: j.branches[0].bearing, to: 90)) < 3)
        #expect(abs(TurnCueGeometry.signedAngleDiff(from: j.selectedBearingForward ?? 999, to: 0)) < 2)
        #expect(abs(TurnCueGeometry.signedAngleDiff(from: j.selectedBearingBackward ?? 999, to: 180)) < 2)

        let y = F.trail("y", "Y", [(0, 300), F.offset((0, 300), bearing: 30, meters: 100)])
        let yRoute = TurnCueRouteBuilder.build(selected: F.main(), others: [y])
        #expect(abs(TurnCueGeometry.signedAngleDiff(from: yRoute.junctions[0].branches[0].bearing, to: 30)) < 3)
    }

    @Test func crossingWithoutASharedNodeIsDetected() {
        // Neither trail has a vertex at the crossing.
        let cross = F.trail("x", "X", [(-80, 296), (80, 304)])
        let route = TurnCueRouteBuilder.build(selected: F.main(), others: [cross])
        #expect(route.junctions.count == 1)
        #expect(route.junctions[0].branches.count == 2)
    }

    @Test func sameNameAndTwinTrailsAreNotJunctions() {
        let sameName = F.trail("piece", "Main Trail", [(0, 300), (100, 300)])
        let twin = F.trail("twin", "Twin", F.mainPts)
        let route = TurnCueRouteBuilder.build(selected: F.main(), others: [sameName, twin, F.main()])
        #expect(route.junctions.isEmpty)
    }

    @Test func nearbyCrossingsMergeIntoOneMultiWayJunction() {
        let a = F.trail("a", "A", [(-80, 300), (80, 300)])
        let b = F.trail("b", "B", [(-80, 312), (80, 312)])
        let route = TurnCueRouteBuilder.build(selected: F.main(), others: [a, b])
        #expect(route.junctions.count == 1)
        #expect(route.junctions[0].branches.count >= 3)
    }

    @Test func farAwayTrailsProduceNothing() {
        let far = F.trail("far", "Far", [(500, 0), (500, 300)])
        #expect(TurnCueRouteBuilder.build(selected: F.main(), others: [far]).junctions.isEmpty)
    }

    @Test func displayNameDropsGenericNames() {
        #expect(TurnCueRouteBuilder.displayName(for: F.trail("unnamed-1", "Trail", F.mainPts)) == "")
        #expect(TurnCueRouteBuilder.displayName(for: F.trail("x", "  ", F.mainPts)) == "")
        #expect(TurnCueRouteBuilder.displayName(for: F.trail("x", "Alta", F.mainPts)) == "Alta")
    }
}

@MainActor
struct TurnCueSpeakerTests {
    final class FakeSession: AudioSessionControlling {
        var log: [String] = []
        var failActivate = false
        struct Boom: Error {}
        func activate() throws {
            if failActivate { throw Boom() }
            log.append("activate")
        }
        func deactivate() { log.append("deactivate") }
    }

    final class FakeOutput: SpeechOutput {
        var onFinish: (@MainActor () -> Void)?
        var spoken: [String] = []
        var stops = 0
        func speak(_ text: String) { spoken.append(text) }
        func stop() { stops += 1; onFinish?() }
        func finishCurrent() { onFinish?() }
    }

    private func make() -> (TurnCueSpeaker, FakeSession, FakeOutput) {
        let s = FakeSession(), o = FakeOutput()
        return (TurnCueSpeaker(session: s, output: o, observeInterruptions: false), s, o)
    }

    @Test func activatesBeforeSpeakingAndDeactivatesAfterTheLastUtterance() {
        let (speaker, session, output) = make()
        speaker.speak("one", priority: .far)
        #expect(session.log == ["activate"])
        #expect(output.spoken == ["one"])
        output.finishCurrent()
        #expect(session.log == ["activate", "deactivate"])
    }

    @Test func pendingFarCueIsReplacedAndSpokenAfterTheCurrent() {
        let (speaker, session, output) = make()
        speaker.speak("one", priority: .far)
        speaker.speak("two", priority: .far)
        speaker.speak("three", priority: .far)
        output.finishCurrent()
        #expect(output.spoken == ["one", "three"])
        output.finishCurrent()
        #expect(session.log.last == "deactivate")
        #expect(session.log.filter { $0 == "deactivate" }.count == 1)
    }

    @Test func nearCueInterruptsTheOneBeingSpoken() {
        let (speaker, _, output) = make()
        speaker.speak("far", priority: .far)
        speaker.speak("near", priority: .near)
        #expect(output.stops == 1)
        #expect(output.spoken == ["far", "near"])
    }

    @Test func staleCueIsDroppedBeforeSpeaking() {
        let (speaker, session, output) = make()
        speaker.speak("late", priority: .far) { true }
        #expect(output.spoken.isEmpty)
        #expect(!session.log.contains("activate"))
    }

    @Test func activationFailureSkipsTheCueWithoutCrashing() {
        let (speaker, session, output) = make()
        session.failActivate = true
        speaker.speak("one", priority: .far)
        #expect(output.spoken.isEmpty)
        session.failActivate = false
        speaker.speak("two", priority: .far)
        #expect(output.spoken == ["two"])
    }

    @Test func cancelAllReleasesTheSession() {
        let (speaker, session, output) = make()
        speaker.speak("one", priority: .far)
        speaker.cancelAll()
        #expect(output.stops == 1)
        #expect(session.log.last == "deactivate")
    }
}
