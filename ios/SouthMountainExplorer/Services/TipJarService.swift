import Foundation
import StoreKit
import Observation

/// The optional **tip jar**. TrekDex is 100% free; tips are consumable
/// In-App Purchases that unlock nothing — a pure "thank you / support
/// development" gesture, which is exactly what Apple's guidelines require
/// tips to be (they must go through IAP and can be given repeatedly).
///
/// Consumables mean there's no entitlement to restore and nothing to gate:
/// we load the products, run the purchase, finish the transaction, and bump
/// a local "tips given" counter so the UI can say thanks. A background
/// `Transaction.updates` listener finishes any transaction that arrives out
/// of band (e.g. an Ask-to-Buy approval) so none are left unfinished.
@MainActor
@Observable
final class TipJarService {
    static let shared = TipJarService()

    /// Product identifiers — must match the consumable IAPs created in App
    /// Store Connect (and the bundled `TrekDex.storekit` config for local
    /// testing). Ordered small → large; the view renders them in this order.
    static let productIDs = [
        "com.southmountainexplorer.app.tip.small",
        "com.southmountainexplorer.app.tip.medium",
        "com.southmountainexplorer.app.tip.large",
    ]

    enum LoadState: Equatable {
        case idle, loading, loaded, failed
    }

    private(set) var products: [Product] = []
    private(set) var loadState: LoadState = .idle
    /// The product id currently being purchased, so the row can show a
    /// spinner and disable re-taps. `nil` when no purchase is in flight.
    private(set) var purchasingID: String?
    /// Set to the just-purchased product's display name after a successful
    /// tip so the view can show a thank-you. Cleared by the view once shown.
    var lastThanksProductName: String?

    /// Total tips left, persisted, so the UI can acknowledge supporters.
    /// Read from UserDefaults so it survives relaunches (and Reset All
    /// Progress, which intentionally leaves it alone).
    var tipsGiven: Int {
        UserDefaults.standard.integer(forKey: StorageKeys.tipsGiven)
    }
    var isSupporter: Bool { tipsGiven > 0 }

    private var updatesTask: Task<Void, Never>?

    private init() {
        // Finish transactions that arrive outside an explicit purchase()
        // (deferred approvals, restores from another device). For a
        // consumable that just means finishing so StoreKit stops replaying.
        // The listener runs for the app's lifetime (this is a `.shared`
        // singleton), so there's no deinit to tear it down.
        updatesTask = Task { [weak self] in
            for await update in Transaction.updates {
                guard let self else { return }
                if case .verified(let transaction) = update {
                    await self.recordTip(named: nil)
                    await transaction.finish()
                }
            }
        }
    }

    /// Fetch the tip products from the App Store. Safe to call repeatedly
    /// (e.g. every time the tip screen appears); a successful load short-
    /// circuits so we don't refetch needlessly.
    func loadProducts() async {
        guard loadState != .loaded, loadState != .loading else { return }
        loadState = .loading
        do {
            let fetched = try await Product.products(for: Self.productIDs)
            // Preserve the small→large intent regardless of StoreKit's
            // return order; fall back to price if an id is missing.
            products = fetched.sorted { a, b in
                let ia = Self.productIDs.firstIndex(of: a.id) ?? Int.max
                let ib = Self.productIDs.firstIndex(of: b.id) ?? Int.max
                return ia == ib ? a.price < b.price : ia < ib
            }
            loadState = products.isEmpty ? .failed : .loaded
        } catch {
            loadState = .failed
        }
    }

    /// Purchase (leave) a tip. Returns true on a completed, verified tip.
    /// Cancellations and pending approvals return false without an error —
    /// they're normal, not failures to surface.
    @discardableResult
    func tip(_ product: Product) async -> Bool {
        guard purchasingID == nil else { return false }
        purchasingID = product.id
        defer { purchasingID = nil }
        do {
            let result = try await product.purchase()
            switch result {
            case .success(let verification):
                guard case .verified(let transaction) = verification else {
                    return false   // failed StoreKit signature check
                }
                await recordTip(named: product.displayName)
                await transaction.finish()
                return true
            case .userCancelled, .pending:
                return false
            @unknown default:
                return false
            }
        } catch {
            return false
        }
    }

    private func recordTip(named name: String?) {
        UserDefaults.standard.set(tipsGiven + 1, forKey: StorageKeys.tipsGiven)
        if let name { lastThanksProductName = name }
    }
}
