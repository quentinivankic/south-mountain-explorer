import SwiftUI

struct RecordingPanel: View {
    let area: Area
    let onStop: (FinishedRecording?) -> Void

    @Environment(RecordingService.self) private var recording
    @Environment(LocationService.self) private var location
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var elapsed: TimeInterval = 0
    @State private var timer: Timer? = nil
    @State private var isStopping = false
    @State private var showStopConfirm = false
    @State private var showDiscardConfirm = false

    private var rec: ActiveRecording? { recording.activeRecording }

    /// Human-readable ETA to the end of the recording's active
    /// trail, or `nil` when one of the gating conditions in
    /// `TrailETA` short-circuits the math (loop trail, off-trail
    /// user, insufficient pace data, area-mode recording with no
    /// trail id at all). Recomputed on every body eval, which
    /// re-fires whenever location.liveLocation or the recording
    /// path changes — both already publish via @Observable.
    private var etaLabel: String? {
        guard let rec, let trailId = rec.trailId else { return nil }
        guard let trail = (area.rawTrails ?? area.trails).first(where: { $0.id == trailId }) else {
            return nil
        }
        guard let coord = location.liveLocation ?? location.userLocation else { return nil }
        let pace = recording.smoothedPaceMetersPerSec()
        guard let seconds = TrailETA.compute(currentLocation: coord, trail: trail, paceMetersPerSec: pace)
        else { return nil }
        return TrailETA.formatLabel(seconds)
    }

    var body: some View {
        HStack(spacing: 20) {
            // Recording indicator
            VStack(spacing: 2) {
                Image(systemName: "record.circle.fill")
                    .foregroundStyle(.red)
                    .font(.title2)
                    .symbolEffect(.pulse)
                Text("REC")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.red)
            }

            Divider().frame(height: 40)

            // Stats
            statColumn(label: "Distance", value: UnitFormatter.distance(miles: rec?.distanceMi ?? 0, units: units))
            statColumn(label: "Duration", value: formattedElapsed)
            // ETA only renders when the recording is bound to a
            // trail AND the math has enough signal (see TrailETA's
            // gating). For area-mode recordings (no trailId) or
            // loop trails the column simply doesn't appear — better
            // than a permanent "—" that just takes space.
            if let etaLabel {
                statColumn(label: "ETA", value: etaLabel)
            }

            Spacer()

            // Stop button
            Button {
                showStopConfirm = true
            } label: {
                if isStopping {
                    ProgressView()
                        .frame(width: 56, height: 56)
                } else {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 44))
                        .foregroundStyle(.red)
                }
            }
            .disabled(isStopping)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
        .glassEffect(in: .rect(cornerRadius: 24))
        .padding(.horizontal, 16)
        .onAppear { startTimer() }
        .onDisappear { timer?.invalidate() }
        .confirmationDialog(
            "Stop this hike?",
            isPresented: $showStopConfirm,
            titleVisibility: .visible
        ) {
            Button("Stop & Save", role: .destructive) { stopRecording() }
            Button("Stop & Discard", role: .destructive) { showDiscardConfirm = true }
            Button("Keep Recording", role: .cancel) { }
        } message: {
            Text(stopMessage)
        }
        .confirmationDialog(
            "Discard this hike?",
            isPresented: $showDiscardConfirm,
            titleVisibility: .visible
        ) {
            Button("Discard", role: .destructive) { discardRecording() }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("This hike won't be saved to history and your trail coverage won't update. This can't be undone.")
        }
    }

    private var stopMessage: String {
        let dist = UnitFormatter.distance(miles: rec?.distanceMi ?? 0, units: units)
        return "\(dist) recorded so far. Save adds it to history and updates your trail coverage. Discard throws it away."
    }

    private func statColumn(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.headline.monospacedDigit())
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var formattedElapsed: String {
        let h = Int(elapsed) / 3600
        let m = (Int(elapsed) % 3600) / 60
        let s = Int(elapsed) % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%02d:%02d", m, s)
    }

    private func startTimer() {
        if let rec {
            elapsed = Date().timeIntervalSince(rec.startedAt)
        }
        // The Timer fire closure is @Sendable / nonisolated, so hop back to
        // the main actor before touching the @Observable RecordingService or
        // @State elapsed value. Keeps Swift 6 strict concurrency happy.
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            Task { @MainActor in
                if let rec = recording.activeRecording {
                    elapsed = Date().timeIntervalSince(rec.startedAt)
                }
            }
        }
    }

    private func stopRecording() {
        isStopping = true
        timer?.invalidate()
        Task {
            // Use raw trails for coverage finalization so the
            // fraction denominator is the dense pre-decimation node
            // count (see AreaView's applyLiveCoverage caller).
            let finished = await recording.stopRecording(trails: area.rawTrails ?? area.trails)
            onStop(finished)
            isStopping = false
        }
    }

    private func discardRecording() {
        timer?.invalidate()
        recording.discardRecording()
        // Same callback contract as Stop & Save, but with no FinishedRecording
        // so AreaView skips the summary sheet.
        onStop(nil)
    }
}

struct RecordingSummarySheet: View {
    let finished: FinishedRecording
    let areaName: String
    let trails: [Trail]

    @Environment(\.dismiss) private var dismiss
    @Environment(ProgressService.self) private var progress

    private func trailName(for id: String) -> String {
        trails.first { $0.id == id }?.name ?? id
    }

    private var areaTrailCount: Int { trails.count }
    private var areaCompletedCount: Int { progress.completionCount(in: finished.areaId) }
    private var areaCompletionFraction: Double {
        guard areaTrailCount > 0 else { return 0 }
        return Double(areaCompletedCount) / Double(areaTrailCount)
    }

    /// Trails with new partial coverage from this hike — covered ≥5%
    /// (anything less is GPS noise) but not newly completed and not
    /// revisited (those get their own sections).
    private var partialTrails: [(id: String, name: String, coverage: Double)] {
        let exclude = Set(finished.newlyCompletedTrailIds).union(finished.revisitedTrailIds)
        return finished.coverageDelta
            .filter { tid, c in c >= 0.05 && c < 0.9 && !exclude.contains(tid) }
            .map { (id: $0.key, name: trailName(for: $0.key), coverage: $0.value) }
            .sorted { $0.coverage > $1.coverage }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 28) {
                    // Header
                    VStack(spacing: 8) {
                        Image(systemName: finished.newlyCompletedTrailIds.isEmpty ? "figure.hiking" : "trophy.fill")
                            .font(.system(size: 64))
                            .foregroundStyle(finished.newlyCompletedTrailIds.isEmpty ? .blue : .yellow)
                            .symbolEffect(.bounce, value: true)

                        Text(finished.newlyCompletedTrailIds.isEmpty ? "Hike Complete" : "Trails Completed!")
                            .font(.largeTitle)
                            .fontWeight(.bold)

                        Text(areaName)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.top)

                    // Stats grid
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 16) {
                        statCard(title: "Distance", value: UnitFormatter.distanceValue(miles: finished.distanceMi, units: units), unit: UnitFormatter.distanceSuffix(units: units))
                        statCard(title: "Duration", value: formattedDuration, unit: "")
                    }
                    .padding(.horizontal)

                    // Cumulative area progress
                    if areaTrailCount > 0 {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("Area Progress")
                                    .font(.headline)
                                Spacer()
                                Text("\(areaCompletedCount) of \(areaTrailCount) · \(Int((areaCompletionFraction * 100).rounded()))%")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                    .monospacedDigit()
                            }
                            ProgressView(value: areaCompletionFraction)
                                .tint(.cyan)
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .padding(.horizontal)
                    }

                    // Newly completed trails
                    if !finished.newlyCompletedTrailIds.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("New Completions")
                                .font(.headline)
                                .padding(.horizontal)

                            ForEach(finished.newlyCompletedTrailIds, id: \.self) { trailId in
                                HStack {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundStyle(.green)
                                    Text(trailName(for: trailId))
                                        .font(.body)
                                }
                                .padding(.horizontal)
                            }
                        }
                    }

                    // Trails walked again that were already complete
                    if !finished.revisitedTrailIds.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Previously Completed")
                                .font(.headline)
                                .padding(.horizontal)

                            ForEach(finished.revisitedTrailIds, id: \.self) { trailId in
                                HStack {
                                    Image(systemName: "arrow.clockwise.circle.fill")
                                        .foregroundStyle(.cyan)
                                    Text(trailName(for: trailId))
                                        .font(.body)
                                }
                                .padding(.horizontal)
                            }
                        }
                    }

                    // Partial coverage from this hike
                    if !partialTrails.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Made Progress")
                                .font(.headline)
                                .padding(.horizontal)

                            VStack(spacing: 10) {
                                ForEach(partialTrails, id: \.id) { trail in
                                    VStack(alignment: .leading, spacing: 4) {
                                        HStack {
                                            Text(trail.name)
                                                .font(.subheadline)
                                            Spacer()
                                            Text("\(Int((trail.coverage * 100).rounded()))%")
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                                .monospacedDigit()
                                        }
                                        ProgressView(value: trail.coverage)
                                            .tint(.cyan)
                                    }
                                }
                            }
                            .padding(14)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .glassEffect(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                            .padding(.horizontal)
                        }
                    }

                    Button("Done") { dismiss() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .padding(.horizontal)
                }
            }
            .navigationTitle("Summary")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func statCard(title: String, value: String, unit: String) -> some View {
        VStack(spacing: 4) {
            HStack(alignment: .lastTextBaseline, spacing: 2) {
                Text(value)
                    .font(.title.bold().monospacedDigit())
                if !unit.isEmpty {
                    Text(unit)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .glassEffect(in: .rect(cornerRadius: 16))
    }

    private var formattedDuration: String {
        let h = finished.durationSeconds / 3600
        let m = (finished.durationSeconds % 3600) / 60
        let s = finished.durationSeconds % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%02d:%02d", m, s)
    }
}
