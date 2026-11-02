import Foundation

struct RemovalPlan: Equatable, Sendable {
    /// Ids whose cached file may be deleted.
    var delete: [String]
    /// Claimed by this state but protected (saved, opened, held, recording,
    /// pre-existing): the file stays and the claim is dropped.
    var keptProtected: [String]
}

/// Pure safety core for Remove. Deletion needs a claim owned by this exact
/// scope (created only when this feature wrote the file) and the id must not
/// be in the protected set. Unclaimed files are never touched.
enum StateRemovalPlanner {
    static func plan(
        stateKey: String,
        claims: [String: Claim],
        preexisting: Set<String>,
        protected: Set<String>
    ) -> RemovalPlan {
        let blocked = protected.union(preexisting)
        var delete: [String] = []
        var kept: [String] = []
        for (id, claim) in claims where claim.stateKey == stateKey {
            if blocked.contains(id) { kept.append(id) } else { delete.append(id) }
        }
        return RemovalPlan(delete: delete.sorted(), keptProtected: kept.sorted())
    }
    /// Areas of this scope that survive Remove. Pre-existing and user-protected
    /// areas are never claimed (the feature did not write them), so counting
    /// only claimed ids reported "Kept 0" while they stayed on the device.
    static func survivorCount(
        keptClaimed: [String],
        members: [String],
        preexisting: Set<String>,
        protected: Set<String>,
        isOnDisk: (String) -> Bool
    ) -> Int {
        let blocked = protected.union(preexisting)
        var survivors = Set(keptClaimed)
        for id in members where blocked.contains(id) && isOnDisk(id) { survivors.insert(id) }
        return survivors.count
    }
}

/// Everything the user has touched that Remove must never delete.
enum StateProtection {
    static func protectedIDs(
        favorites: Set<String>,
        opened: Set<String>,
        userHeld: Set<String>,
        activeRecordingAreaID: String?,
        canonical: (String) -> String = { $0 }
    ) -> Set<String> {
        var ids = favorites.union(opened).union(userHeld)
        if let activeRecordingAreaID { ids.insert(activeRecordingAreaID) }
        // A favorite/opened hidden twin keeps its canonical area too.
        for id in Array(ids) { ids.insert(canonical(id)) }
        return ids
    }
}

/// Areas the user downloaded through the per-area flows (Download Saved &
/// Recent, Nearby, retries). Persisted so a later state Remove keeps them.
enum UserHeldAreas {
    static func all(defaults: UserDefaults = .standard) -> Set<String> {
        Set(defaults.stringArray(forKey: StorageKeys.offlineStatesUserHeld) ?? [])
    }

    static func add(_ ids: [String], defaults: UserDefaults = .standard) {
        guard !ids.isEmpty else { return }
        let merged = all(defaults: defaults).union(ids)
        defaults.set(Array(merged).sorted(), forKey: StorageKeys.offlineStatesUserHeld)
    }
}
