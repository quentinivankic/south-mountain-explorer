import SwiftUI

private enum LengthFilter: String, CaseIterable, Identifiable {
    case all
    case quick
    case half
    case full

    var id: String { rawValue }

    var label: String {
        switch self {
        case .all:   return "All"
        case .quick: return "Quick · <10 mi"
        case .half:  return "Half-day · 10–30"
        case .full:  return "Full-day · 30+"
        }
    }

    func matches(_ totalMi: Double?) -> Bool {
        if self == .all { return true }
        guard let m = totalMi else { return false }
        switch self {
        case .all:   return true
        case .quick: return m < 10
        case .half:  return (10..<30).contains(m)
        case .full:  return m >= 30
        }
    }
}

struct HomeView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(LocationService.self) private var location
    @Environment(FavoritesService.self) private var favorites
    @Environment(AuthService.self) private var auth
    @Environment(RecordingService.self) private var recording

    @State private var selectedArea: AreaSummary? = nil
    @State private var showLocationPrompt = false
    @State private var showAllAreasMap = false
    @State private var history: [SavedRecording] = []
    @State private var lengthFilter: LengthFilter = .all

    private var visitedAreaIds: Set<String> {
        Set(history.map { $0.areaId })
    }

    private var continueArea: AreaSummary? {
        guard let last = history.first else { return nil }
        return areas.summaries.first { $0.id == last.areaId }
    }

    private var nearbyAreas: [AreaSummary] {
        guard let loc = location.userLocation else { return [] }
        // Pull a wider pool so the length filter still has 10 candidates after filtering.
        let pool = areas.nearby(lat: loc.latitude, lon: loc.longitude, limit: 30)
        return Array(pool.filter { lengthFilter.matches($0.totalMi) }.prefix(10))
    }

    private var unvisitedAreas: [AreaSummary] {
        let visited = visitedAreaIds
        let candidates = areas.summaries.filter { !visited.contains($0.id) }
        guard let loc = location.userLocation else {
            return Array(candidates.prefix(10))
        }
        let sorted = candidates.sorted {
            haversine($0, lat: loc.latitude, lon: loc.longitude)
            < haversine($1, lat: loc.latitude, lon: loc.longitude)
        }
        return Array(sorted.prefix(10))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    if let pickup = continueArea {
                        continueSection(area: pickup)
                    }
                    if !unvisitedAreas.isEmpty {
                        areaSection(title: "Try Something New", items: unvisitedAreas)
                    }
                    if !favorites.favoriteAreas.isEmpty {
                        areaSection(title: "Saved Areas", items: favorites.favoriteAreas)
                    }
                    if location.userLocation != nil {
                        nearYouSection
                    }
                    if continueArea == nil
                        && unvisitedAreas.isEmpty
                        && favorites.favoriteAreas.isEmpty
                        && nearbyAreas.isEmpty {
                        emptyState
                    }
                }
                .padding()
            }
            .navigationTitle("Explore")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showAllAreasMap = true
                    } label: {
                        Image(systemName: "map")
                    }
                    .accessibilityLabel("All Areas Map")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if !auth.isSignedIn {
                        NavigationLink(destination: AuthView()) {
                            Text("Sign In")
                                .fontWeight(.medium)
                        }
                    }
                }
            }
        }
        .onAppear {
            if !location.isAuthorized {
                showLocationPrompt = true
            } else {
                location.startLiveTracking()
            }
            Task { history = await recording.loadHistory() }
        }
        .sheet(isPresented: $showLocationPrompt) {
            LocationPromptView()
        }
        .sheet(isPresented: $showAllAreasMap) {
            AllAreasMapView()
        }
        .sheet(item: $selectedArea) { area in
            AreaView(areaId: area.id, areaName: area.name)
        }
    }

    private func areaSection(title: String, items: [AreaSummary]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title2)
                .fontWeight(.bold)
                .padding(.horizontal, 4)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 14) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, area in
                        // Alternate styles so the user can compare card art treatments.
                        AreaCard(area: area, style: index.isMultiple(of: 2) ? .tight : .glow)
                            .onTapGesture { selectedArea = area }
                    }
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 2)
            }
        }
    }

    private var nearYouSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Near You")
                    .font(.title2)
                    .fontWeight(.bold)
                Spacer()
            }
            .padding(.horizontal, 4)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(LengthFilter.allCases) { filter in
                        Button {
                            withAnimation(.easeInOut(duration: 0.15)) { lengthFilter = filter }
                        } label: {
                            Text(filter.label)
                                .font(.caption.weight(.medium))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                        }
                        .buttonStyle(.plain)
                        .glassEffect(
                            lengthFilter == filter ? .regular.tint(.accentColor).interactive() : .regular.interactive(),
                            in: .capsule
                        )
                        .foregroundStyle(lengthFilter == filter ? .white : .primary)
                    }
                }
                .padding(.horizontal, 4)
            }

            if nearbyAreas.isEmpty {
                Text("No areas match this filter near you.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 4)
                    .padding(.vertical, 12)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 14) {
                        ForEach(Array(nearbyAreas.enumerated()), id: \.element.id) { index, area in
                            AreaCard(area: area, style: index.isMultiple(of: 2) ? .tight : .glow)
                                .onTapGesture { selectedArea = area }
                        }
                    }
                    .padding(.horizontal, 4)
                    .padding(.vertical, 2)
                }
            }
        }
    }

    private func continueSection(area: AreaSummary) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pick Up Where You Left Off")
                .font(.title2)
                .fontWeight(.bold)
                .padding(.horizontal, 4)

            ContinueCard(area: area)
                .onTapGesture { selectedArea = area }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 20) {
            Image(systemName: "mountain.2")
                .font(.system(size: 56))
                .foregroundStyle(.secondary)
            Text("Discover Trails")
                .font(.title2)
                .fontWeight(.semibold)
            Text("Enable location to find trails near you, or browse the full catalog.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Browse All Areas") { }
                .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }

    private func haversine(_ a: AreaSummary, lat: Double, lon: Double) -> Double {
        let R = 3958.8
        let dLat = (a.centerLat - lat) * .pi / 180
        let dLon = (a.centerLon - lon) * .pi / 180
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(lat * .pi / 180) * cos(a.centerLat * .pi / 180)
            * sin(dLon / 2) * sin(dLon / 2)
        return R * 2 * atan2(sqrt(h), sqrt(1 - h))
    }
}

struct LocationPromptView: View {
    @Environment(LocationService.self) private var location
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Image(systemName: "location.circle.fill")
                    .font(.system(size: 72))
                    .foregroundStyle(.green)
                    .symbolEffect(.pulse)

                Text("Find Trails Near You")
                    .font(.title)
                    .fontWeight(.bold)

                Text("Share your location to discover nearby trails and see your position on the map while hiking.")
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)

                VStack(spacing: 12) {
                    Button("Allow Location Access") {
                        location.requestPermission()
                        dismiss()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .frame(maxWidth: .infinity)

                    Button("Not Now") { dismiss() }
                        .foregroundStyle(.secondary)
                }
            }
            .padding(32)
            .presentationDetents([.medium])
            .presentationDragIndicator(.visible)
        }
    }
}
