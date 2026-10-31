import AVFoundation
import Foundation
import os

private let cueLog = Logger(subsystem: "com.trekdex.app", category: "turn-cues")

/// Seam over `AVAudioSession` so the activate/deactivate sequence is testable.
@MainActor
protocol AudioSessionControlling: AnyObject {
    func activate() throws
    func deactivate()
}

/// Seam over `AVSpeechSynthesizer`.
@MainActor
protocol SpeechOutput: AnyObject {
    var onFinish: (@MainActor () -> Void)? { get set }
    func speak(_ text: String)
    func stop()
}

/// Navigation-style session: duck music, pause spoken audio, resume after.
/// Activated just before speaking and released after the last utterance,
/// so nothing is held while idle.
@MainActor
final class SystemAudioSession: AudioSessionControlling {
    func activate() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(
            .playback,
            mode: .voicePrompt,
            options: [.duckOthers, .interruptSpokenAudioAndMixWithOthers]
        )
        try session.setActive(true)
    }

    func deactivate() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

@MainActor
final class SystemSpeechOutput: NSObject, SpeechOutput, AVSpeechSynthesizerDelegate {
    var onFinish: (@MainActor () -> Void)?
    private let synthesizer = AVSpeechSynthesizer()

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.current.identifier)
            ?? AVSpeechSynthesisVoice(language: AVSpeechSynthesisVoice.currentLanguageCode())
        synthesizer.speak(utterance)
    }

    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.onFinish?() }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.onFinish?() }
    }
}

/// Speaks cues with queue discipline: one in flight, one pending. A new far
/// cue replaces a pending one; a near cue interrupts the one being spoken.
@MainActor
final class TurnCueSpeaker {
    enum Priority { case far, near }

    private struct Item {
        let text: String
        let isStale: () -> Bool
    }

    private let session: any AudioSessionControlling
    private let output: any SpeechOutput
    private var pending: Item?
    private var inFlight = false
    nonisolated(unsafe) private var interruptionObserver: NSObjectProtocol?

    init(
        session: any AudioSessionControlling = SystemAudioSession(),
        output: any SpeechOutput = SystemSpeechOutput(),
        observeInterruptions: Bool = true
    ) {
        self.session = session
        self.output = output
        output.onFinish = { [weak self] in self?.finished() }
        if observeInterruptions {
            interruptionObserver = NotificationCenter.default.addObserver(
                forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
            ) { [weak self] note in
                let began = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
                    == AVAudioSession.InterruptionType.began.rawValue
                guard began else { return }
                MainActor.assumeIsolated { self?.interruptionBegan() }
            }
        }
    }

    deinit {
        if let interruptionObserver { NotificationCenter.default.removeObserver(interruptionObserver) }
    }

    func speak(_ text: String, priority: Priority, isStale: @escaping () -> Bool = { false }) {
        let item = Item(text: text, isStale: isStale)
        if inFlight {
            pending = item
            if priority == .near { output.stop() }
        } else {
            begin(item)
        }
    }

    /// Drops everything and releases the audio session.
    func cancelAll() {
        pending = nil
        if inFlight {
            inFlight = false
            output.stop()
        }
        session.deactivate()
    }

    private func interruptionBegan() {
        // A call or Siri took the session: drop stale cues rather than replay them.
        pending = nil
        if inFlight {
            inFlight = false
            output.stop()
        }
    }

    private func begin(_ item: Item) {
        guard !item.isStale() else {
            if pending == nil { session.deactivate() }
            return
        }
        do {
            try session.activate()
        } catch {
            cueLog.notice("audio session activate failed: \(String(describing: error), privacy: .public)")
            return
        }
        inFlight = true
        cueLog.notice("spoken: \(item.text, privacy: .public)")
        output.speak(item.text)
    }

    private func finished() {
        // `cancelAll`/interruption already cleared inFlight; ignore their echo.
        let wasInFlight = inFlight
        inFlight = false
        if let next = pending {
            pending = nil
            begin(next)
        } else if wasInFlight {
            session.deactivate()
        }
    }
}
