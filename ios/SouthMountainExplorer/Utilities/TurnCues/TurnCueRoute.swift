import Foundation

/// A fork or crossing on the selected trail, derived geometrically from the
/// other trails in the same area.
struct TurnCueJunction: Sendable, Equatable {
    struct Branch: Sendable, Equatable {
        let trailId: String
        let trailName: String
        /// Compass bearing (deg) of the other trail leaving the junction.
        let bearing: Double
    }

    let id: Int
    let chain: Int
    /// Metres along the chain.
    let arc: Double
    let branches: [Branch]
    /// Bearing of the chain leaving the junction in the stored direction
    /// (25 m out, or as far as 8+ m allows). `nil` when the chain ends here.
    let selectedBearingForward: Double?
    /// Bearing of the chain leaving the junction against the stored direction.
    let selectedBearingBackward: Double?
}

/// Stitched, ordered geometry of the selected trail plus its junctions.
struct TurnCueRoute: Sendable, Equatable {
    struct Chain: Sendable, Equatable {
        let pts: [LocalPt]
        let cum: [Double]
        let isLoop: Bool

        init(pts: [LocalPt], isLoop: Bool) {
            self.pts = pts
            self.cum = TurnCueGeometry.cumulative(pts)
            self.isLoop = isLoop
        }

        var length: Double { cum.last ?? 0 }

        func normalized(_ arc: Double) -> Double {
            let l = length
            guard l > 0 else { return 0 }
            if isLoop {
                var a = arc.truncatingRemainder(dividingBy: l)
                if a < 0 { a += l }
                return a
            }
            return max(0, min(l, arc))
        }

        func point(at arc: Double) -> LocalPt {
            guard pts.count > 1 else { return pts.first ?? LocalPt(x: 0, y: 0) }
            let a = normalized(arc)
            var lo = 0, hi = cum.count - 1
            while hi - lo > 1 {
                let mid = (lo + hi) / 2
                if cum[mid] <= a { lo = mid } else { hi = mid }
            }
            let segLen = cum[hi] - cum[lo]
            let t = segLen > 1e-9 ? (a - cum[lo]) / segLen : 0
            return LocalPt(
                x: pts[lo].x + (pts[hi].x - pts[lo].x) * t,
                y: pts[lo].y + (pts[hi].y - pts[lo].y) * t
            )
        }

        /// Bearing leaving `arc` along (`sign` +1) or against (-1) the chain,
        /// sampled up to `span` metres out. `nil` when less than `minSpan`
        /// of chain exists that way.
        func bearing(from arc: Double, sign: Double, span: Double = 25, minSpan: Double = 8) -> Double? {
            let l = length
            let available: Double
            if isLoop {
                available = l
            } else {
                available = sign > 0 ? l - arc : arc
            }
            let use = min(span, available)
            guard use >= minSpan else { return nil }
            let from = point(at: arc)
            let to = point(at: arc + sign * use)
            guard TurnCueGeometry.distance(from, to) > 0.5 else { return nil }
            return TurnCueGeometry.bearing(from: from, to: to)
        }

        /// Nearest point on the chain, optionally restricted to an unwrapped
        /// arc window `[lo, hi]`. Loops accept windows that cross the seam.
        func project(_ p: LocalPt, window: ClosedRange<Double>? = nil) -> TurnCueGeometry.Projection? {
            guard pts.count > 1 else { return nil }
            let l = length
            var best: TurnCueGeometry.Projection?
            let shifts: [Double] = isLoop && window != nil ? [-l, 0, l] : [0]
            for i in 0..<(pts.count - 1) {
                let segLen = cum[i + 1] - cum[i]
                for shift in shifts {
                    let s0 = cum[i] + shift, s1 = cum[i + 1] + shift
                    if let w = window, s1 < w.lowerBound || s0 > w.upperBound { continue }
                    let r = TurnCueGeometry.projectOnSegment(p, pts[i], pts[i + 1])
                    if best == nil || r.dist < best!.perpendicular {
                        best = .init(arc: cum[i] + r.t * segLen, perpendicular: r.dist)
                    }
                    break
                }
            }
            return best
        }
    }

    let trailId: String
    /// Empty when the trail has no usable name; phrasing then omits "to stay on".
    let trailName: String
    let origin: TurnCueProjector
    let chains: [Chain]
    let junctions: [TurnCueJunction]

    var isEmpty: Bool { chains.isEmpty }

    func junctions(onChain chain: Int) -> [TurnCueJunction] {
        junctions.filter { $0.chain == chain }
    }
}

enum TurnCueRouteBuilder {
    static let stitchGapMeters = 12.0
    static let contactMeters = 6.0
    static let resampleMeters = 8.0
    static let runClusterMeters = 40.0
    static let coAlignedMeters = 60.0
    static let junctionClusterMeters = 25.0
    static let branchSpanMeters = 25.0
    static let minBranchOffChainDegrees = 15.0

    /// Builds the route for `selected` against `others` (the rest of the
    /// area's trails). Pure and deterministic; run it off the main actor.
    static func build(selected: Trail, others: [Trail]) -> TurnCueRoute {
        let name = displayName(for: selected)
        guard let first = selected.segments.lazy.flatMap({ $0 }).first(where: { $0.count >= 2 }) else {
            return TurnCueRoute(
                trailId: selected.id, trailName: name,
                origin: TurnCueProjector(originLat: 0, originLon: 0), chains: [], junctions: []
            )
        }
        let proj = TurnCueProjector(originLat: first[0], originLon: first[1])
        let chains = stitch(segments: localSegments(selected, proj))
        guard !chains.isEmpty else {
            return TurnCueRoute(trailId: selected.id, trailName: name, origin: proj, chains: [], junctions: [])
        }

        let selectedFingerprint = selected.completionFingerprint
        let candidates = others.filter {
            $0.id != selected.id
                && $0.name != selected.name
                && $0.completionFingerprint != selectedFingerprint
        }

        var raw: [RawJunction] = []
        for (ci, chain) in chains.enumerated() {
            let resampledChain = chainSamples(chain)
            for other in candidates {
                raw.append(contentsOf: contacts(
                    chainIndex: ci, chain: chain, chainSamples: resampledChain,
                    other: other, projector: proj
                ))
            }
        }
        let junctions = merge(raw: raw, chains: chains)
        return TurnCueRoute(trailId: selected.id, trailName: name, origin: proj, chains: chains, junctions: junctions)
    }

    static func displayName(for trail: Trail) -> String {
        let trimmed = trail.name.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return "" }
        if trail.id.lowercased().hasPrefix("unnamed") || trimmed.lowercased().hasPrefix("unnamed") { return "" }
        return trimmed
    }

    // MARK: Stitching

    private static func localSegments(_ trail: Trail, _ proj: TurnCueProjector) -> [[LocalPt]] {
        trail.segments.compactMap { seg in
            var pts: [LocalPt] = []
            for p in seg where p.count >= 2 && p[0].isFinite && p[1].isFinite {
                let lp = proj.local(lat: p[0], lon: p[1])
                if let last = pts.last, TurnCueGeometry.distance(last, lp) < 0.01 { continue }
                pts.append(lp)
            }
            return pts.count >= 2 ? pts : nil
        }
    }

    static func stitch(segments: [[LocalPt]]) -> [TurnCueRoute.Chain] {
        var remaining = segments
        var chains: [[LocalPt]] = []
        while !remaining.isEmpty {
            let startIdx = remaining.indices.max {
                TurnCueGeometry.cumulative(remaining[$0]).last! < TurnCueGeometry.cumulative(remaining[$1]).last!
            }!
            var chain = remaining.remove(at: startIdx)
            while true {
                var bestIdx: Int?
                var bestDist = stitchGapMeters
                var bestMode = 0 // 0 append, 1 append reversed, 2 prepend, 3 prepend reversed
                for (i, seg) in remaining.enumerated() {
                    let cands: [(Double, Int)] = [
                        (TurnCueGeometry.distance(chain.last!, seg.first!), 0),
                        (TurnCueGeometry.distance(chain.last!, seg.last!), 1),
                        (TurnCueGeometry.distance(chain.first!, seg.last!), 2),
                        (TurnCueGeometry.distance(chain.first!, seg.first!), 3),
                    ]
                    for (d, m) in cands where d <= bestDist {
                        bestDist = d; bestIdx = i; bestMode = m
                    }
                }
                guard let idx = bestIdx else { break }
                let seg = remaining.remove(at: idx)
                switch bestMode {
                case 0: chain.append(contentsOf: seg)
                case 1: chain.append(contentsOf: seg.reversed())
                case 2: chain = seg + chain
                default: chain = seg.reversed() + chain
                }
            }
            chains.append(chain)
        }
        let built: [TurnCueRoute.Chain] = chains.map { pts in
            var pts = pts
            var loop = false
            if pts.count >= 3, TurnCueGeometry.distance(pts.first!, pts.last!) <= 10 {
                loop = true
                if TurnCueGeometry.distance(pts.first!, pts.last!) > 0.5 { pts.append(pts.first!) }
            }
            return TurnCueRoute.Chain(pts: pts, isLoop: loop)
        }
        return built.sorted { $0.length > $1.length }
    }

    // MARK: Contacts

    private struct Sample {
        var pt: LocalPt
        var arc: Double
    }

    private static func chainSamples(_ chain: TurnCueRoute.Chain) -> [Sample] {
        var out: [Sample] = []
        for i in 0..<(chain.pts.count - 1) {
            let a = chain.pts[i], b = chain.pts[i + 1]
            let d = chain.cum[i + 1] - chain.cum[i]
            let n = max(1, Int((d / resampleMeters).rounded(.up)))
            for k in 0..<n {
                let t = Double(k) / Double(n)
                out.append(Sample(
                    pt: LocalPt(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t),
                    arc: chain.cum[i] + d * t
                ))
            }
        }
        out.append(Sample(pt: chain.pts.last!, arc: chain.length))
        return out
    }

    /// One resampled polyline of another trail.
    private struct OtherLine {
        var pts: [LocalPt]
    }

    private struct RawJunction {
        let chain: Int
        let arc: Double
        let branches: [TurnCueJunction.Branch]
    }

    private struct GridKey: Hashable {
        let r: Int
        let c: Int
    }

    private static func contacts(
        chainIndex: Int,
        chain: TurnCueRoute.Chain,
        chainSamples: [Sample],
        other: Trail,
        projector: TurnCueProjector
    ) -> [RawJunction] {
        let lines: [OtherLine] = localSegments(other, projector).map {
            OtherLine(pts: TurnCueGeometry.resample($0, every: resampleMeters))
        }
        guard !lines.isEmpty else { return [] }

        // Cheap bbox reject.
        var minX = Double.infinity, maxX = -Double.infinity, minY = Double.infinity, maxY = -Double.infinity
        for l in lines { for p in l.pts {
            minX = min(minX, p.x); maxX = max(maxX, p.x); minY = min(minY, p.y); maxY = max(maxY, p.y)
        } }
        let margin = contactMeters + 4
        let overlaps = chain.pts.contains { $0.x >= minX - margin && $0.x <= maxX + margin && $0.y >= minY - margin && $0.y <= maxY + margin }
        // Chain vertices alone can miss a crossing between far vertices; also
        // test the chain bbox.
        var cMinX = Double.infinity, cMaxX = -Double.infinity, cMinY = Double.infinity, cMaxY = -Double.infinity
        for p in chain.pts { cMinX = min(cMinX, p.x); cMaxX = max(cMaxX, p.x); cMinY = min(cMinY, p.y); cMaxY = max(cMaxY, p.y) }
        let bboxOverlap = cMaxX >= minX - margin && cMinX <= maxX + margin && cMaxY >= minY - margin && cMinY <= maxY + margin
        guard overlaps || bboxOverlap else { return [] }

        let cell = 10.0
        var grid: [GridKey: [(line: Int, idx: Int)]] = [:]
        for (li, line) in lines.enumerated() {
            for (pi, p) in line.pts.enumerated() {
                grid[GridKey(r: Int((p.y / cell).rounded(.down)), c: Int((p.x / cell).rounded(.down))), default: []]
                    .append((li, pi))
            }
        }

        struct Contact { var arc: Double; var line: Int; var idx: Int; var dist: Double }
        var contactList: [Contact] = []
        for s in chainSamples {
            let r = Int((s.pt.y / cell).rounded(.down)), c = Int((s.pt.x / cell).rounded(.down))
            var best: Contact?
            for dr in -1...1 { for dc in -1...1 {
                for ref in grid[GridKey(r: r + dr, c: c + dc)] ?? [] {
                    let d = TurnCueGeometry.distance(s.pt, lines[ref.line].pts[ref.idx])
                    if d <= contactMeters, best == nil || d < best!.dist {
                        best = Contact(arc: s.arc, line: ref.line, idx: ref.idx, dist: d)
                    }
                }
            } }
            if let b = best { contactList.append(b) }
        }
        guard !contactList.isEmpty else { return [] }

        // Cluster by arc proximity.
        var runs: [[Contact]] = []
        for ct in contactList {
            if var lastRun = runs.last, ct.arc - lastRun.last!.arc <= runClusterMeters {
                lastRun.append(ct)
                runs[runs.count - 1] = lastRun
            } else {
                runs.append([ct])
            }
        }

        var out: [RawJunction] = []
        for run in runs {
            let sStart = run.first!.arc, sEnd = run.last!.arc
            if sEnd - sStart > coAlignedMeters {
                // Co-aligned stretch: one junction at each end.
                for (isStart, ct) in [(true, run.first!), (false, run.last!)] {
                    let otherEnd = isStart ? run.last! : run.first!
                    var dirs: [Int] = [-1, 1]
                    if ct.line == otherEnd.line, ct.idx != otherEnd.idx {
                        // Leave away from the overlap.
                        dirs = [ct.idx < otherEnd.idx ? -1 : 1]
                    }
                    let branches = branchBearings(
                        at: ct.arc, chain: chain, line: lines[ct.line], index: ct.idx, directions: dirs, other: other
                    )
                    out.append(RawJunction(chain: chainIndex, arc: ct.arc, branches: branches))
                }
            } else {
                // Use the closest contact in the run as the representative.
                let rep = run.min { $0.dist < $1.dist }!
                let arc = (sStart + sEnd) / 2
                let branches = branchBearings(
                    at: arc, chain: chain, line: lines[rep.line], index: rep.idx, directions: [-1, 1], other: other
                )
                out.append(RawJunction(chain: chainIndex, arc: arc, branches: branches))
            }
        }
        return out
    }

    private static func branchBearings(
        at arc: Double,
        chain: TurnCueRoute.Chain,
        line: OtherLine,
        index: Int,
        directions: [Int],
        other: Trail
    ) -> [TurnCueJunction.Branch] {
        let origin = line.pts[index]
        let chainForward = chain.bearing(from: arc, sign: 1)
        let chainBackward = chain.bearing(from: arc, sign: -1)
        var out: [TurnCueJunction.Branch] = []
        for dir in directions {
            // Walk along the other line until branchSpan metres or the end.
            var i = index
            var travelled = 0.0
            var last = origin
            while true {
                let n = i + dir
                guard n >= 0, n < line.pts.count else { break }
                travelled += TurnCueGeometry.distance(last, line.pts[n])
                last = line.pts[n]
                i = n
                if travelled >= branchSpanMeters { break }
            }
            guard travelled >= 12, TurnCueGeometry.distance(origin, last) >= 8 else { continue }
            let bearing = TurnCueGeometry.bearing(from: origin, to: last)
            // Drop branches that run along the chain itself.
            if let f = chainForward, abs(TurnCueGeometry.signedAngleDiff(from: f, to: bearing)) < minBranchOffChainDegrees { continue }
            if let b = chainBackward, abs(TurnCueGeometry.signedAngleDiff(from: b, to: bearing)) < minBranchOffChainDegrees { continue }
            out.append(.init(trailId: other.id, trailName: other.name, bearing: bearing))
        }
        return out
    }

    // MARK: Merge

    private static func merge(raw: [RawJunction], chains: [TurnCueRoute.Chain]) -> [TurnCueJunction] {
        var out: [TurnCueJunction] = []
        var nextId = 0
        for ci in chains.indices {
            let chain = chains[ci]
            let sorted = raw.filter { $0.chain == ci && !$0.branches.isEmpty }.sorted { $0.arc < $1.arc }
            var clusters: [[RawJunction]] = []
            for j in sorted {
                if var last = clusters.last, j.arc - last.last!.arc <= junctionClusterMeters {
                    last.append(j)
                    clusters[clusters.count - 1] = last
                } else {
                    clusters.append([j])
                }
            }
            for cluster in clusters {
                let arc = cluster.map(\.arc).reduce(0, +) / Double(cluster.count)
                var branches: [TurnCueJunction.Branch] = []
                for b in cluster.flatMap(\.branches) {
                    let dup = branches.contains {
                        $0.trailId == b.trailId && abs(TurnCueGeometry.signedAngleDiff(from: $0.bearing, to: b.bearing)) < 10
                    }
                    if !dup { branches.append(b) }
                }
                out.append(TurnCueJunction(
                    id: nextId, chain: ci, arc: arc, branches: branches,
                    selectedBearingForward: chain.bearing(from: arc, sign: 1),
                    selectedBearingBackward: chain.bearing(from: arc, sign: -1)
                ))
                nextId += 1
            }
        }
        return out
    }
}
