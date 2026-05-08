import SwiftUI

struct HomeView: View {
    @Environment(AreaDataService.self) private var areas
    @Environment(LocationService.self) private var location
    @Environment(FavoritesService.self) private var favorites
    @Environment(AuthService.self) private var auth

    @State private var selectedArea: AreaSummary? = nil
    @State private var showLocationPrompt = false

    private var nearbyAreas: [AreaSummary] {
        guard let loc = location.userLocation else { return [] }
        return areas.nearby(lat: loc.latitude, lon: loc.longitude, limit: 10)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    if !favorites.favoriteAreas.isEmpty {
                        areaSection(title: "Saved Areas", items: favorites.favoriteAreas)
                    }
                    if !nearbyAreas.isEmpty {
                        areaSection(title: "Near You", items: nearbyAreas)
                    }
                    if favorites.favoriteAreas.isEmpty && nearbyAreas.isEmpty {
                        emptyState
                    }
                }
                .padding()
            }
            .navigationTitle("Explore")
            .toolbar {
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
        }
        .sheet(isPresented: $showLocationPrompt) {
            LocationPromptView()
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
                    ForEach(items) { area in
                        AreaCard(area: area)
                            .onTapGesture { selectedArea = area }
                    }
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 2)
            }
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
