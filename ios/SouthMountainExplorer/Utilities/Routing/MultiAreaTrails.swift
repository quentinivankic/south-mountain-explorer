import Foundation

/// One area's trails, as loaded for planning ("Plan a Hike", Task 7).
struct AreaTrailSet: Sendable {
    let areaId: String
    let trails: [Trail]
}

/// Trails from several areas merged into one `TrailNetwork` input.
///
/// Rules (pure, deterministic):
/// - Every trail is keyed by (areaId, trailId). Trail ids collide across
///   unrelated areas, so two trails with the same id in different areas stay
///   distinct.
/// - A hidden alias twin (`canonical(id) != id`) is dropped when its canonical
///   area is in the set. The canonical is a superset, so nothing is lost. The
///   twin and canonical are never both in the network.
/// - A trail whose `completionFingerprint` (identical geometry) already appears
///   in an earlier area is dropped from the network. Order of preference:
///   the primary area, then the other areas in the order given. The dropped copy
///   is recorded as an alias so completing EITHER copy counts for the kept one.
///   Trails inside one area are never deduped against each other.
/// - Out of scope: near-identical geometry that the 5 dp fingerprint misses
///   (a neighbor that traces the same path with different vertices stays a
///   separate trail; the network builder's contact/shared-edge logic merges the
///   overlapping stretch into one edge carrying both trails).
struct MergedTrails: Sendable {
    typealias Key = TrailNetwork.TrailKey
    /// What goes into `TrailNetwork(trails:)`.
    let inputs: [TrailNetwork.Input]
    /// Area ids dropped as hidden alias twins of an included canonical.
    let droppedAliasAreas: [String]
    /// Kept key -> keys of the dropped copies (other areas, or alias twins).
    let copies: [Key: [Key]]
    /// Every trail that took part, kept or dropped, for completion snapshots.
    let allInputs: [TrailNetwork.Input]

    /// A lookup where a kept trail is complete if its own key OR any dropped copy
    /// is complete under `base` (which must be evaluated per area, e.g.
    /// `TrailCompletionLookup.progress` over `allInputs`).
    func completion(_ base: TrailCompletionLookup) -> TrailCompletionLookup {
        let copies = self.copies
        let test = base.isComplete
        return TrailCompletionLookup { key in
            if test(key) { return true }
            return copies[key]?.contains(where: test) ?? false
        }
    }
}

enum MultiAreaTrails {
    /// `areas` order matters: first is the primary, the rest in preference order.
    /// `canonical` maps an area id to its canonical id (identity when not hidden).
    static func merge(areas: [AreaTrailSet], canonical: (String) -> String = { $0 }) -> MergedTrails {
        typealias Key = TrailNetwork.TrailKey
        // One entry per area id (first wins), then split off alias twins.
        var seen = Set<String>()
        var unique: [AreaTrailSet] = []
        for a in areas where seen.insert(a.areaId).inserted { unique.append(a) }
        let present = Set(unique.map(\.areaId))
        var kept: [AreaTrailSet] = []
        var twins: [AreaTrailSet] = []
        for a in unique {
            let c = canonical(a.areaId)
            if c != a.areaId, present.contains(c) { twins.append(a) } else { kept.append(a) }
        }

        var inputs: [TrailNetwork.Input] = []
        var all: [TrailNetwork.Input] = []
        var copies: [Key: [Key]] = [:]
        // fingerprint -> (area, kept key). Cheap signature first, so the SHA only
        // runs where two areas could plausibly share a trail.
        var bySignature: [String: [(areaId: String, key: Key, trail: Trail)]] = [:]
        var fingerprintOf: [Key: String] = [:]
        func fingerprint(_ key: Key, _ trail: Trail) -> String {
            if let f = fingerprintOf[key] { return f }
            let f = trail.completionFingerprint
            fingerprintOf[key] = f
            return f
        }
        var keptByFingerprint: [String: Key] = [:]

        func place(_ set: AreaTrailSet, keep: Bool) {
            for trail in set.trails {
                let input = TrailNetwork.Input(areaId: set.areaId, trail: trail)
                all.append(input)
                guard let sig = signature(trail) else {
                    if keep { inputs.append(input) }
                    continue
                }
                // Look for an earlier copy in a DIFFERENT area.
                var match: Key?
                if let candidates = bySignature[sig] {
                    for c in candidates where c.areaId != set.areaId {
                        if fingerprint(c.key, c.trail) == fingerprint(input.key, trail) {
                            match = keptByFingerprint[fingerprint(c.key, c.trail)] ?? c.key
                            break
                        }
                    }
                }
                if let m = match {
                    copies[m, default: []].append(input.key)
                    continue
                }
                if keep {
                    inputs.append(input)
                    bySignature[sig, default: []].append((set.areaId, input.key, trail))
                    keptByFingerprint[fingerprint(input.key, trail)] = input.key
                }
            }
        }
        for a in kept { place(a, keep: true) }
        // Twins only record copies of trails already kept; they add nothing.
        for a in twins { place(a, keep: false) }
        return MergedTrails(
            inputs: inputs, droppedAliasAreas: twins.map(\.areaId).sorted(),
            copies: copies, allInputs: all)
    }

    /// Cheap pre-filter: point count plus first and last point at ~1 m. nil for
    /// a trail with no geometry (never deduped).
    private static func signature(_ trail: Trail) -> String? {
        var count = 0
        var first: [Double]?
        var last: [Double]?
        for seg in trail.segments {
            for p in seg where p.count >= 2 {
                if first == nil { first = p }
                last = p
                count += 1
            }
        }
        guard let f = first, let l = last else { return nil }
        return String(format: "%d|%.4f,%.4f|%.4f,%.4f", count, f[0], f[1], l[0], l[1])
    }
}
