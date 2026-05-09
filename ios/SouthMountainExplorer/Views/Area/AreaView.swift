import SwiftUI

struct AreaView: View {
    let areaId: String
    let areaName: String

    @Environment(AreaDataService.self) private var areas
    @Environment(RecordingService.self) private var recording
    @Environment(FavoritesService.self) private var favorites
    @Environment(LocationService.self) private var location
    @Environment(ProgressService.self) private var progress
    @Environment(\.dismiss) private var dismiss

    @State private var area: Area? = nil
    @State private var isLoading = true
    @State private var loadError: String? = nil
    @State private var showTrailList = true
    @State private var trailListHeight: CGFloat = 340
    @State private var dragOffset: CGFloat = 0
    @State private var selectedTrailId: String? = nil
    @State private var finishedRecording: FinishedRecording? = nil
    @State private var showSummary = false
    @State private var showAreaComplete = false
    @State private var pastPaths: [[GpsPoint]] = []

    private let defaultListHeight: CGFloat = 340

    private var isRecording: Bool {
        recording.activeRecording?.areaId == areaId
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            if let area {
                // Full-screen map
                TrailMapView(
                    area: area,
                    activeRecording: isRecording ? recording.activeRecording : nil,
                    pastPaths: pastPaths,
                    selectedTrailId: $selectedTrailId
                )
                .ignoresSafeArea()

                // Trail list sheet
                if showTrailList {
                    trailListSheet(area: area)
                }

                // Recording panel — floats above everything
                if isRecording {
                    VStack {
                        Spacer()
                        RecordingPanel(area: area) { finished in
                            finishedRecording = finished
                            showSummary = finished != nil
                            // Refresh the cyan coverage halo with the
                            // just-finished hike's path.
                            Task { await loadPastPaths() }
                        }
                        .padding(.bottom, (showTrailList ? currentListHeight : 0) + 20)
                    }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }

                // Map/List toggle and record button
                if !isRecording {
                    controlBar(area: area)
                }

            } else if isLoading {
                ProgressView("Loading \(areaName)...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("Area Unavailable",
                    systemImage: "xmark.octagon",
                    description: Text(loadError ?? "Could not load trail data. Check your connection."))
            }
        }
        .navigationBarHidden(true)
        .overlay(alignment: .top) {
            HStack {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .glassEffect(in: .circle)
                }
                Spacer()
                Button {
                    Task { await favorites.toggle(areaId: areaId) }
                } label: {
                    Image(systemName: favorites.isFavorite(areaId) ? "heart.fill" : "heart")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .glassEffect(in: .circle)
                        .foregroundStyle(favorites.isFavorite(areaId) ? .red : .primary)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 8)
            .safeAreaPadding(.top)
        }
        .task {
            let result = await areas.areaWithError(id: areaId)
            area = result.area
            loadError = result.error
            isLoading = false
            await loadPastPaths()
        }
        .task(id: isRecording) {
            // While a recording is active for this area, recompute coverage
            // every 30s so partial progress visibly fills in (trail-list
            // progress bars tick up, trails crossing 90% turn cyan live).
            guard isRecording else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled, isRecording, let area else { break }
                await recording.applyLiveCoverage(trails: area.trails)
            }
        }
        .sheet(isPresented: $showSummary) {
            if let finished = finishedRecording {
                RecordingSummarySheet(
                    finished: finished,
                    areaName: areaName,
                    trails: area?.trails ?? []
                )
            }
        }
        .sheet(isPresented: $showAreaComplete) {
            if let area {
                AreaCompletionView(area: area)
                    .presentationDetents([.large])
            }
        }
        .onChange(of: progress.completionCount(in: areaId)) { old, new in
            // Trigger the celebration when the area transitions into 100%.
            // Suppress while the recording summary is up — the trophy state
            // there is enough acknowledgement, and stacking sheets is messy.
            guard let area, area.resolvedTrailCount > 0 else { return }
            let total = area.resolvedTrailCount
            if old < total && new >= total && !showSummary {
                showAreaComplete = true
            }
        }
    }

    private var currentListHeight: CGFloat {
        max(180, trailListHeight - dragOffset)
    }

    private func loadPastPaths() async {
        let history = await recording.loadHistory()
        pastPaths = history
            .filter { $0.areaId == areaId }
            .map { $0.path }
    }

    private func trailListSheet(area: Area) -> some View {
        GeometryReader { geo in
            let tallHeight = max(geo.size.height - 100, defaultListHeight)
            let clampedHeight = min(currentListHeight, tallHeight)
            VStack(spacing: 0) {
                Spacer()
                VStack(spacing: 0) {
                    // Drag handle — extended hit area for the gesture
                    VStack(spacing: 0) {
                        Capsule()
                            .fill(Color(.tertiaryLabel))
                            .frame(width: 36, height: 4)
                            .padding(.top, 10)
                            .padding(.bottom, 6)

                        Text(areaName)
                            .font(.headline)
                            .padding(.bottom, 4)
                    }
                    .frame(maxWidth: .infinity)
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture()
                            .onChanged { value in
                                dragOffset = value.translation.height
                            }
                            .onEnded { value in
                                let proposed = trailListHeight - value.translation.height
                                let snappedTall = (defaultListHeight + tallHeight) / 2
                                let target: CGFloat
                                if proposed > snappedTall {
                                    target = tallHeight
                                } else {
                                    target = defaultListHeight
                                }
                                withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) {
                                    trailListHeight = target
                                    dragOffset = 0
                                }
                            }
                    )

                    TrailListView(area: area, selectedTrailId: $selectedTrailId)
                }
                .frame(height: clampedHeight)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            }
        }
        .ignoresSafeArea(edges: .bottom)
    }

    private func controlBar(area: Area) -> some View {
        HStack(spacing: 14) {
            // Map/List toggle
            Button {
                withAnimation(.spring()) { showTrailList.toggle() }
            } label: {
                Image(systemName: showTrailList ? "map.fill" : "list.bullet")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .glassEffect(in: .circle)
            }

            Spacer()

            // Record button
            Button {
                if !location.isAuthorized { location.requestPermission(); return }
                recording.startRecording(areaId: areaId, mode: .roam)
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "record.circle")
                        .font(.body.weight(.semibold))
                    Text("Record Hike")
                        .fontWeight(.semibold)
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                .glassEffect(.regular.interactive(), in: .capsule)
            }
        }
        .padding(.horizontal, 20)
        .padding(.bottom, (showTrailList ? currentListHeight : 0) + 20)
    }
}
