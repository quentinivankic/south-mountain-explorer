import SwiftUI

struct RecordingPanel: View {
    let area: Area
    let onStop: (FinishedRecording?) -> Void

    @Environment(RecordingService.self) private var recording

    @State private var elapsed: TimeInterval = 0
    @State private var timer: Timer? = nil
    @State private var isStopping = false
    @State private var showStopConfirm = false

    private var rec: ActiveRecording? { recording.activeRecording }

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
            statColumn(label: "Distance", value: String(format: "%.2f mi", rec?.distanceMi ?? 0))
            statColumn(label: "Duration", value: formattedElapsed)

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
            "Stop and save this hike?",
            isPresented: $showStopConfirm,
            titleVisibility: .visible
        ) {
            Button("Stop & Save", role: .destructive) { stopRecording() }
            Button("Keep Recording", role: .cancel) { }
        } message: {
            Text(stopMessage)
        }
    }

    private var stopMessage: String {
        let dist = String(format: "%.2f mi", rec?.distanceMi ?? 0)
        return "\(dist) recorded so far. We'll save the hike and update your trail coverage."
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
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            if let rec = recording.activeRecording {
                elapsed = Date().timeIntervalSince(rec.startedAt)
            }
        }
    }

    private func stopRecording() {
        isStopping = true
        timer?.invalidate()
        Task {
            let finished = await recording.stopRecording(trails: area.trails)
            onStop(finished)
            isStopping = false
        }
    }
}

struct RecordingSummarySheet: View {
    let finished: FinishedRecording
    let areaName: String
    @Environment(\.dismiss) private var dismiss

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
                        statCard(title: "Distance", value: String(format: "%.2f", finished.distanceMi), unit: "mi")
                        statCard(title: "Duration", value: formattedDuration, unit: "")
                    }
                    .padding(.horizontal)

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
                                    Text(trailId)
                                        .font(.body)
                                }
                                .padding(.horizontal)
                            }
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
