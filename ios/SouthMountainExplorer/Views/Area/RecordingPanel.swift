import SwiftUI
import CoreLocation

struct RecordingPanel: View {
    let area: Area
    let onStop: (FinishedRecording?) -> Void

    @Environment(RecordingService.self) private var recording
    @Environment(LocationService.self) private var location
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var elapsed: TimeInterval = 0
    @State private var timer: Timer? = nil
    /// Live elevation strip data, refreshed on the 1 s timer tick rather than
    /// recomputed inside `body`. `elevationStats` is a three-pass walk over the
    /// ENTIRE recorded path (haversine per point, plus two array allocations),
    /// and body re-evaluates on every GPS sample and every path append — so the
    /// panel got measurably slower the longer the hike ran.
    @State private var liveElevation: ElevationStats? = nil
    /// Same treatment for the ETA label, which flattened the active trail's
    /// dense geometry and ran three haversine passes per body evaluation.
    @State private var liveEtaLabel: String? = nil
    /// "Back N min" — time to retrace the route walked so far. Refreshed on the
    /// same 1 s tick as the rest; unlike the ETA it needs no trail, so a roam
    /// recording gets it too.
    @State private var liveReturnLabel: String? = nil
    @State private var isStopping = false
    @State private var showStopConfirm = false
    @State private var saveFailureMessage: String? = nil

    /// True when the recording has produced nothing worth persisting — under
    /// ~80 m of movement or too few GPS fixes to draw a route. Saving one of
    /// these wrote a 0.0 mi hike with an empty map into history permanently.
    private var hasNothingToSave: Bool {
        guard let rec else { return true }
        return rec.distanceMi < 0.05 || rec.path.count < 5
    }
    @State private var showDiscardConfirm = false

    private var rec: ActiveRecording? { recording.activeRecording }

    /// Seconds without a fix before we call the signal lost. Long enough that
    /// ordinary sampling jitter (and the deliberate 2 s poll) never trips it.
    private let staleFixSeconds: TimeInterval = 45

    /// Acquiring / lost / good. ALWAYS a value: signal quality is permanent
    /// dashboard state on a one-line slot, never a capsule that pops in and
    /// resizes the card. Recomputed off `elapsed`, which the 1 s timer drives.
    private var gpsStatus: (text: String, tint: Color) {
        guard let last = location.lastFixDate else {
            return ("Waiting for GPS…", .orange)
        }
        if Date().timeIntervalSince(last) > staleFixSeconds {
            return ("GPS signal lost", .red)
        }
        // Have a fix, but not enough points to draw anything yet.
        if (rec?.path.count ?? 0) < 2 {
            return ("Waiting for GPS…", .orange)
        }
        return ("GPS good", .green)
    }

    /// Human-readable ETA to the end of the recording's active
    /// trail, or `nil` when one of the gating conditions in
    /// `TrailETA` short-circuits the math (loop trail, off-trail
    /// user, insufficient pace data, area-mode recording with no
    /// trail id at all). Recomputed on every body eval, which
    /// re-fires whenever location.liveLocation or the recording
    /// path changes — both already publish via @Observable.
    private func computeEtaLabel() -> String? {
        guard let rec, let trailId = rec.trailId else { return nil }
        guard let trail = (area.rawTrails ?? area.trails).first(where: { $0.id == trailId }) else {
            return nil
        }
        guard let coord = location.liveLocation ?? location.userLocation else { return nil }
        let pace = recording.smoothedPaceMetersPerSec()
        // The previous recorded fix gives direction of travel. Taken from the
        // RECORDING path, not from LocationService, so it has already been
        // through GpsIngest's speed and accuracy gates and a wild fix cannot
        // reverse the estimate.
        var prior: CLLocationCoordinate2D? = nil
        if rec.path.count >= 2 {
            let p = rec.path[rec.path.count - 2]
            if p.count >= 2 { prior = CLLocationCoordinate2D(latitude: p[0], longitude: p[1]) }
        }
        guard let seconds = TrailETA.compute(currentLocation: coord,
                                             priorLocation: prior,
                                             trail: trail,
                                             paceMetersPerSec: pace)
        else { return nil }
        return TrailETA.formatLabel(seconds)
    }

    /// Time to retrace the route walked so far, back to where the hike started.
    private func computeReturnLabel() -> String? {
        guard let rec else { return nil }
        guard let seconds = TrailETA.returnToStart(
            walkedMeters: rec.distanceMi * 1609.344,
            paceMetersPerSec: recording.smoothedPaceMetersPerSec()
        ) else { return nil }
        return TrailETA.formatLabel(seconds)
    }

    var body: some View {
        // EVERY slot in this card is permanently reserved. The dashboard sizes
        // the sheet's fit stop, so a block that pops in mid-hike (GPS capsule,
        // elevation strip, ETA line — all former offenders) resizes the sheet
        // under the user's thumb and clips for a beat while the stop catches
        // up. Reserved slots make the card's height a fact, not a feed.
        VStack(spacing: 12) {
            // GPS signal quality — one quiet permanent line, not a colored
            // capsule that appears and disappears. Signal state is the one
            // thing a hiker should always be able to glance at mid-hike.
            HStack(spacing: 6) {
                Circle()
                    .fill(gpsStatus.tint)
                    .frame(width: 7, height: 7)
                Text(gpsStatus.text)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .accessibilityElement(children: .combine)

            // Live elevation strip — the 70pt slot is ALWAYS reserved. Before
            // there are enough altitude samples (the first minutes of a hike,
            // or hardware that returns no altitude) it holds a quiet
            // placeholder instead of not existing.
            Group {
                if let rec, let stats = liveElevation {
                    ElevationProfileView(
                        stats: stats,
                        totalDistanceMeters: rec.distanceMi * 1609.344
                    )
                } else {
                    Text("Elevation appears after a few minutes")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(
                            RoundedRectangle(cornerRadius: 8, style: .continuous)
                                .fill(.quaternary.opacity(0.3))
                        )
                }
            }
            .frame(height: 70)

            HStack(spacing: 12) {
                // The REC badge is gone, and with it a whole column of width.
                // It said "you are recording" on a panel that only EXISTS while
                // you are recording, under a top banner already showing a
                // pulsing record dot, beside a big red stop button. Three
                // statements of the same fact; the other two are better placed.
                // The room it freed is what lets three stat columns breathe.
                statColumn(label: "Distance", value: UnitFormatter.distance(miles: rec?.distanceMi ?? 0, units: units))
                statColumn(label: "Duration", value: formattedElapsed)
                // Live pace from the 60-second smoothed window. Renders
                // "—" until the recording has enough samples (handled
                // inside UnitFormatter.pace), so the column is stable
                // from the first frame instead of popping in.
                statColumn(label: "Pace",
                           value: UnitFormatter.pace(metersPerSecond: recording.smoothedPaceMetersPerSec() ?? 0,
                                                     units: units))

                // Stop button — both states framed identically so tapping
                // Stop cannot wobble the card's height while it saves.
                Button {
                    showStopConfirm = true
                } label: {
                    Group {
                        if isStopping {
                            ProgressView()
                        } else {
                            Image(systemName: "stop.circle.fill")
                                .font(.system(size: 44))
                                .foregroundStyle(.red)
                        }
                    }
                    .frame(width: 56, height: 56)
                }
                .disabled(isStopping || recording.isStopping)
            }

            estimatesLine
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
        .compatibleGlass(in: .rect(cornerRadius: 24))
        .padding(.horizontal, 16)
        .onAppear { startTimer() }
        .onDisappear { timer?.invalidate() }
        // Nothing worth keeping yet: offer Discard instead of Save, so a
        // start-then-immediately-stop doesn't drop a 0.0 mi hike into history
        // that then shows up as "Pick Up Where You Left Off" with an empty map.
        .confirmationDialog(
            hasNothingToSave ? "Nothing to save yet" : "Stop this hike?",
            isPresented: $showStopConfirm,
            titleVisibility: .visible
        ) {
            if hasNothingToSave {
                Button("Discard", role: .destructive) { discardRecording() }
                Button("Keep Recording", role: .cancel) { }
            } else {
                Button("Stop & Save") { stopRecording() }
                Button("Stop & Discard", role: .destructive) { showDiscardConfirm = true }
                Button("Keep Recording", role: .cancel) { }
            }
        } message: {
            Text(hasNothingToSave
                 ? "We haven't got enough GPS to trace a route, so there's nothing to add to your history yet."
                 : stopMessage)
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
        .alert(
            "Couldn't Save Hike",
            isPresented: Binding(
                get: { saveFailureMessage != nil },
                set: { if !$0 { saveFailureMessage = nil } }
            )
        ) {
            Button("Retry Save") { stopRecording() }
            Button("Keep Recording", role: .cancel) { }
        } message: {
            Text(saveFailureMessage ?? "Your active recording is still safe and location observation has resumed.")
        }
    }

    /// "When am I done" and "when am I back", on one caption line beneath the
    /// counters rather than as two more columns.
    ///
    /// Five equal columns squeezed between a badge and a 56pt button is how the
    /// panel got cramped in the first place. These two are a different KIND of
    /// number from distance and duration — estimates, not measurements — so they
    /// read better set apart and quieter than they would fighting for width in
    /// the same row. Each appears only when it has an answer, and the whole line
    /// disappears when neither does — WHICH IS EXACTLY WHY the line's height
    /// is now permanently reserved: estimates arriving a few minutes into a
    /// hike must not resize the card the sheet's fit stop is sized from. The
    /// slot renders a blank line of the same font until it has an answer.
    private var estimatesLine: some View {
        HStack(spacing: 16) {
            if let eta = liveEtaLabel {
                Label("Finish \(eta)", systemImage: "flag.checkered")
                    .accessibilityLabel("About \(eta) to the end of the trail")
            }
            if let back = liveReturnLabel {
                Label("Back \(back)", systemImage: "arrow.uturn.left")
                    .accessibilityLabel("About \(back) to return to where you started")
            }
            if liveEtaLabel == nil && liveReturnLabel == nil {
                Text(" ")
                    .accessibilityHidden(true)
            }
            Spacer(minLength: 0)
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .minimumScaleFactor(0.8)
    }

    private var stopMessage: String {
        let dist = UnitFormatter.distance(miles: rec?.distanceMi ?? 0, units: units)
        return "\(dist) recorded so far. Save adds it to history and updates your trail coverage. Discard throws it away."
    }

    private func statColumn(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.headline.monospacedDigit())
                .lineLimit(1)
                .minimumScaleFactor(0.6)   // shrink to fit, never clip to "…"
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        // Equal share of the row's middle so 3-4 columns distribute
        // instead of getting squeezed until values truncate.
        .frame(maxWidth: .infinity)
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
                    // Refresh the expensive derived values here, once per
                    // second, instead of from `body` on every GPS sample.
                    liveElevation = elevationStats(path: rec.path)
                    liveEtaLabel = computeEtaLabel()
                    liveReturnLabel = computeReturnLabel()
                }
            }
        }
    }

    private func stopRecording() {
        guard !isStopping, !recording.isStopping else { return }
        isStopping = true
        timer?.invalidate()
        Task {
            do {
                // Use raw trails for coverage finalization so the
                // fraction denominator is the dense pre-decimation node
                // count (see AreaView's applyLiveCoverage caller).
                let finished = try await recording.stopRecording(
                    trails: area.rawTrails ?? area.trails
                )
                if let finished { onStop(finished) }
            } catch {
                saveFailureMessage = error.localizedDescription
                startTimer()
            }
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
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var gpxShareURL: IdentifiedURL? = nil
    /// Set when a GPX export throws — see `exportFailureAlert`.
    @State private var exportFailure: String? = nil

    private func trailName(for id: String) -> String {
        trails.first { $0.id == id }?.name ?? id
    }

    private var areaTrailCount: Int { trails.count }
    // Fingerprint-authoritative count (matches the per-row checkmarks), not the
    // raw completions dictionary — the raw count can retain a stale trail id
    // after a data update, showing one more than the checkmarks / Area page.
    private var areaCompletedCount: Int { progress.completionCount(in: finished.areaId, trails: trails) }
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
                        if let stats = elevationStats(path: finished.path) {
                            statCard(title: "Ascent",
                                     value: UnitFormatter.elevationValue(meters: stats.totalAscentMeters, units: units),
                                     unit: UnitFormatter.elevationSuffix(units: units))
                            statCard(title: "Descent",
                                     value: UnitFormatter.elevationValue(meters: stats.totalDescentMeters, units: units),
                                     unit: UnitFormatter.elevationSuffix(units: units))
                        }
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
                        .compatibleGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .padding(.horizontal)
                    }

                    // Newly completed trails
                    if !finished.newlyCompletedTrailIds.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("New Completions")
                                .font(.headline)
                                .padding(.horizontal)

                            ForEach(finished.newlyCompletedTrailIds, id: \.self) { trailId in
                                completedTrailRow(trailId: trailId,
                                                  icon: "checkmark.circle.fill",
                                                  tint: .green)
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
                                completedTrailRow(trailId: trailId,
                                                  icon: "arrow.clockwise.circle.fill",
                                                  tint: .cyan)
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
                            .compatibleGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
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
        .sheet(item: $gpxShareURL) { wrapped in
            ShareSheet(items: [wrapped.url])
        }
        .exportFailureAlert($exportFailure)
    }

    /// Single completed-trail row, used for both "New Completions"
    /// and "Previously Completed" sections. Right-side share button
    /// builds a GPX of the official trail polyline and surfaces the
    /// iOS share sheet — useful for sending the route to a friend
    /// or saving as a Garmin Course right after finishing the hike.
    /// Hidden when the trail id doesn't resolve to a Trail (rare —
    /// would mean we somehow completed a trail not in `trails`).
    @ViewBuilder
    private func completedTrailRow(trailId: String, icon: String, tint: Color) -> some View {
        let trail = trails.first { $0.id == trailId }
        HStack {
            Image(systemName: icon)
                .foregroundStyle(tint)
            Text(trail?.name ?? "Unnamed trail")
                .font(.body)
            Spacer()
            if let trail {
                Button {
                    exportTrailGpx(trail)
                } label: {
                    Image(systemName: "square.and.arrow.up")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Export \(trail.name) as GPX")
            }
        }
        .padding(.horizontal)
    }

    private func exportTrailGpx(_ trail: Trail) {
        do {
            let url = try GpxExport.temporaryFile(trail: trail, areaName: areaName)
            gpxShareURL = IdentifiedURL(url: url)
        } catch {
            exportFailure = ExportFailure.message(for: error,
                                                  what: "\u{201C}\(trail.name)\u{201D}")
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
        .compatibleGlass(in: .rect(cornerRadius: 16))
    }

    private var formattedDuration: String {
        let h = finished.durationSeconds / 3600
        let m = (finished.durationSeconds % 3600) / 60
        let s = finished.durationSeconds % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%02d:%02d", m, s)
    }
}
