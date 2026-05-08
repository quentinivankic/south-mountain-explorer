import SwiftUI

struct TrailListView: View {
    let area: Area

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Summary header
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(area.resolvedTrailCount) trails · \(String(format: "%.1f", area.resolvedTotalMi)) mi total")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)

                    let completed = progress.completionCount(in: area.id)
                    if area.resolvedTrailCount > 0 {
                        Text("\(completed) of \(area.resolvedTrailCount) completed")
                            .font(.caption)
                            .foregroundStyle(completed == area.resolvedTrailCount ? .green : .secondary)
                    }
                }
                Spacer()
            }
            .padding(.horizontal)
            .padding(.vertical, 12)

            Divider()

            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(area.trails) { trail in
                        TrailRow(trail: trail, areaId: area.id)
                        Divider().padding(.leading)
                    }
                }
            }
        }
    }
}

struct TrailRow: View {
    let trail: Trail
    let areaId: String

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording

    private var isComplete: Bool { progress.isComplete(areaId: areaId, trailId: trail.id) }
    private var coveragePct: Double { coverage.trailCoverage(areaId: areaId, trailId: trail.id) }
    private var isRecordingThis: Bool { recording.activeRecording?.trailId == trail.id }

    var body: some View {
        HStack(spacing: 14) {
            // Completion indicator
            ZStack {
                Circle()
                    .fill(isComplete ? Color.green : Color(.systemFill))
                    .frame(width: 32, height: 32)
                Image(systemName: isComplete ? "checkmark" : difficultyIcon)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(isComplete ? .white : difficultyColor)
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(trail.name)
                        .font(.body)
                        .fontWeight(isRecordingThis ? .semibold : .regular)
                    if isRecordingThis {
                        Image(systemName: "record.circle.fill")
                            .foregroundStyle(.red)
                            .symbolEffect(.pulse)
                    }
                }

                HStack(spacing: 8) {
                    Label(String(format: "%.1f mi", trail.distanceMi), systemImage: "figure.walk")
                    Text("·")
                    Text(trail.difficulty.rawValue)
                        .foregroundStyle(difficultyColor)
                }
                .font(.caption)
                .foregroundStyle(.secondary)

                if coveragePct > 0.02 && !isComplete {
                    ProgressView(value: coveragePct)
                        .tint(difficultyColor)
                        .frame(maxWidth: 120)
                }
            }

            Spacer()

            Button {
                Task { await progress.toggleTrail(areaId: areaId, trailId: trail.id) }
            } label: {
                Image(systemName: isComplete ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(isComplete ? .green : .secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal)
        .padding(.vertical, 12)
        .contentShape(Rectangle())
    }

    private var difficultyColor: Color {
        switch trail.difficulty {
        case .easy: return .green
        case .moderate: return .orange
        case .hard: return .red
        }
    }

    private var difficultyIcon: String {
        switch trail.difficulty {
        case .easy: return "leaf"
        case .moderate: return "arrow.up.right"
        case .hard: return "bolt.fill"
        }
    }
}
