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
        VStack(spacing: 12) {
            // Live elevation strip. Only renders once `elevationStats`
            // has enough altitude samples to be meaningful (returns
            // nil otherwise, e.g. the first 30s of a recording or any
            // hike taken on a device whose GPS isn't returning
            // altitude). Compact 70pt height — full chart treatment
            // lives in HikeDetailView post-hike.
            if let rec, let stats = elevationStats(path: rec.path) {
                ElevationProfileView(
                    stats: stats,
                    totalDistanceMeters: rec.distanceMi * 1609.344
                )
                .frame(height: 70)
                .transition(.opacity)
            }

            HStack(spacing: 12) {
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

                // Stats. Each column takes an equal share of the middle
                // (statColumn is maxWidth: .infinity) and values shrink to
                // fit rather than truncate — three columns (Distance /
                // Duration / Pace, + ETA in trail mode) were clipping to
                // "0.05…" / "25:4…" at fixed width.
                statColumn(label: "Distance", value: UnitFormatter.distance(miles: rec?.distanceMi ?? 0, units: units))
                statColumn(label: "Duration", value: formattedElapsed)
                // Live pace from the 60-second smoothed window. Renders
                // "—" until the recording has enough samples (handled
                // inside UnitFormatter.pace), so the column is stable
                // from the first frame instead of popping in.
                statColumn(label: "Pace",
                           value: UnitFormatter.pace(metersPerSecond: recording.smoothedPaceMetersPerSec() ?? 0,
                                                     units: units))
                // ETA only renders when the recording is bound to a
                // trail AND the math has enough signal (see TrailETA's
                // gating). For area-mode recordings (no trailId) or
                // loop trails the column simply doesn't appear — better
                // than a permanent "—" that just takes space.
                if let etaLabel {
                    statColumn(label: "ETA", value: etaLabel)
                }

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
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
        .compatibleGlass(in: .rect(cornerRadius: 24))
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
    @AppStorage(StorageKeys.units) private var units: UnitsPreference = .imperial

    @State private var gpxShareURL: IdentifiedURL? = nil

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
            Text(trail?.name ?? trailId)
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
            // Silent — share sheet won't appear; user can retry.
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
