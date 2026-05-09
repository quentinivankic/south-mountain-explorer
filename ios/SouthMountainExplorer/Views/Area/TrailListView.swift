import SwiftUI

struct TrailListView: View {
    let area: Area
    @Binding var selectedTrailId: String?
    var onRecordTrail: ((Trail) -> Void)? = nil

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

                    // Discoverability nudge for the long-press → "Record
                    // This Trail" context menu. Hidden mid-recording since
                    // the action wouldn't be available anyway.
                    if recording.activeRecording == nil {
                        Label("Tap to highlight on the map · long-press to record just that trail", systemImage: "hand.tap")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                            .padding(.top, 2)
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
                        TrailRow(
                            trail: trail,
                            areaId: area.id,
                            selectedTrailId: $selectedTrailId,
                            onRecordTrail: onRecordTrail
                        )
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
    @Binding var selectedTrailId: String?
    var onRecordTrail: ((Trail) -> Void)? = nil

    @Environment(ProgressService.self) private var progress
    @Environment(CoverageService.self) private var coverage
    @Environment(RecordingService.self) private var recording

    private var isComplete: Bool { progress.isComplete(areaId: areaId, trailId: trail.id) }
    private var coveragePct: Double { coverage.trailCoverage(areaId: areaId, trailId: trail.id) }
    private var isRecordingThis: Bool { recording.activeRecording?.trailId == trail.id }
    private var isSelected: Bool { selectedTrailId == trail.id }

    var body: some View {
        HStack(spacing: 14) {
            // Difficulty / completion indicator
            ZStack {
                Circle()
                    .fill(isComplete ? Color.cyan : Color(.systemFill))
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
                // Outlined checkmark hints the action; fills in cyan when complete.
                // Wrap both branches in AnyShapeStyle so the ternary has a single
                // type — .cyan is a Color, .tertiary is a HierarchicalShapeStyle,
                // and Swift can't unify them otherwise.
                Image(systemName: isComplete ? "checkmark.circle.fill" : "checkmark.circle")
                    .font(.title3)
                    .foregroundStyle(isComplete ? AnyShapeStyle(Color.cyan) : AnyShapeStyle(.tertiary))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal)
        .padding(.vertical, 12)
        .background(isSelected ? Color.accentColor.opacity(0.15) : Color.clear)
        .contentShape(Rectangle())
        .onTapGesture {
            selectedTrailId = isSelected ? nil : trail.id
        }
        .contextMenu {
            // "Record This Trail" appears only when no recording is in
            // flight; AreaView's separate conflict guard handles the
            // bottom-bar Record Hike button. Recording in trail mode
            // labels the saved hike with this trail's name in History
            // and lights it up on the map as a dashed cyan guide line.
            if recording.activeRecording == nil, let onRecordTrail {
                Button {
                    onRecordTrail(trail)
                } label: {
                    Label("Record This Trail", systemImage: "record.circle")
                }
            }
        }
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
