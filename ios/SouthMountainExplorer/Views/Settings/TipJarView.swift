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
                VStack(alignment: .leading, spacing: 10) {
                    Text("A note from the developer")
                        .font(.headline)
                    Text("I am a solo developer working hard to make the best hiking app out there. Tips directly support development, including buying a more powerful computer and an Apple Watch to make a companion app. Thank you so much for considering a tip.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text("TrekDex is currently free, but will include a paid tier in the future to support ongoing costs. The free tier will always include the core features of the app.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text("Tips are one-time and can be given as often as you like. Thank you for hiking with TrekDex.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    if store.isSupporter {
                        Label("Thanks for supporting TrekDex", systemImage: "heart.fill")
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
            Text("Your support helps keep TrekDex growing.")
        }
    }

    private func tipRow(_ product: Product) -> some View {
        Button {
            Task { await store.tip(product) }
        } label: {
            HStack {
                Text(product.displayName)
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
