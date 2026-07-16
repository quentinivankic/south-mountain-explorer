import SwiftUI
import StoreKit

/// The optional tip jar, pushed from Settings. Makes clear the app is free
/// and a tip is a pure thank-you (no unlock), then lists the consumable tip
/// products with their localized App Store prices. Handles the empty /
/// loading / failed states so a store hiccup degrades gracefully instead of
/// showing dead buttons.
struct TipJarView: View {
    @State private var store = TipJarService.shared
    @State private var showThanks = false

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text("TrekDex is free")
                        .font(.headline)
                    Text("Every trail, every feature — no subscription, no paywall. If the app has earned a spot on your hikes and you'd like to chip in toward its development, you can leave a tip. It unlocks nothing; it just means a lot.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    if store.isSupporter {
                        Label("Thanks for supporting TrekDex ♥", systemImage: "heart.fill")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.pink)
                            .padding(.top, 2)
                    }
                }
                .padding(.vertical, 4)
                .listRowBackground(Color.clear)
                .listRowSeparator(.hidden)
            }

            Section {
                switch store.loadState {
                case .idle, .loading:
                    HStack {
                        ProgressView()
                        Text("Loading…").foregroundStyle(.secondary)
                    }
                case .failed:
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Tips aren't available right now.")
                            .foregroundStyle(.secondary)
                        Button("Try Again") {
                            Task { await reload() }
                        }
                    }
                case .loaded:
                    ForEach(store.products, id: \.id) { product in
                        tipRow(product)
                    }
                }
            } footer: {
                Text("Tips are one-time and can be given as often as you like. Thank you for hiking with TrekDex.")
            }
        }
        .navigationTitle("Leave a Tip")
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadProducts() }
        .onChange(of: store.lastThanksProductName) { _, name in
            if name != nil { showThanks = true }
        }
        .alert("Thank you! ♥", isPresented: $showThanks) {
            Button("You're welcome to hike on") { store.lastThanksProductName = nil }
        } message: {
            Text("Your support genuinely helps keep TrekDex growing.")
        }
    }

    private func tipRow(_ product: Product) -> some View {
        Button {
            Task { await store.tip(product) }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(product.displayName)
                    if !product.description.isEmpty {
                        Text(product.description)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if store.purchasingID == product.id {
                    ProgressView()
                } else {
                    Text(product.displayPrice)
                        .font(.body.weight(.semibold))
                        .monospacedDigit()
                        .foregroundStyle(.tint)
                }
            }
        }
        .disabled(store.purchasingID != nil)
        .accessibilityIdentifier("tip-\(product.id)")
    }

    private func reload() async {
        // loadProducts short-circuits when already loaded/loading; from the
        // failed state it will retry.
        await store.loadProducts()
    }
}
