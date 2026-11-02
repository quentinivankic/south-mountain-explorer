import Foundation

/// Builds a `TrailNetwork`. Phases:
///
/// 1. pieces: project every polyline to local metres, chain a trail's pieces
///    the way the elevation pipeline did (for profile mapping)
/// 2. contacts: find where pieces touch (endpoint contacts, mid-trail vertex
///    contacts, bridges, X crossings) and record cut points + merge requests
/// 3. vertices: insert the cut points, give every vertex a global id
/// 4. nodes: union-find merges touching vertices into nodes
/// 5. fine edges: one per vertex pair, deduplicated (a shared stretch becomes
///    one fine edge carrying both trails)
/// 6. simplify: chains of fine edges between real junctions become edges
struct TrailNetworkBuilder {
    typealias Key = TrailNetwork.TrailKey
    typealias Coordinate = TrailNetwork.Coordinate

    struct TrailInfo {
        let key: Key
        let profile: [Int]?
        var pieces: [Int] = []
        var chainTotal = 0.0
        var elevationMapped = false
        var maxGap = 0.0
    }

    struct Piece {
        let trail: Int
        var pts: [LocalPt]
        var ll: [Coordinate]
        var cum: [Double]
        var chainOffset = 0.0
        var flipped = false
        var length: Double { cum.last ?? 0 }
    }

    enum VRef: Hashable {
        case orig(Int, Int)   // piece, vertex index
        case cut(Int)
    }

    struct Cut {
        let piece: Int
        let seg: Int
        let t: Double
    }

    struct Vertex {
        var p: LocalPt
        var c: Coordinate
        var piece: Int
        /// Arc length along its piece.
        var arc: Double
        /// 1 for a piece endpoint, else 0. Interior vertices anchor a node's
        /// position so a snapped endpoint never bends the trail it joined.
        var rank: Int
    }

    struct FineKey: Hashable {
        let a: Int
        let b: Int
        let bridge: Bool
    }

    struct FineEdge {
        let a: Int
        let b: Int
        let bridge: Bool
        var trails: [Int]
        var elevA: Double?
        var elevB: Double?
    }

    let options: TrailNetwork.Options
    var projector: TurnCueProjector
    var trails: [TrailInfo] = []
    var pieces: [Piece] = []

    // Contact bookkeeping (phase 2).
    var grid = SegmentGrid(cell: 40)
    var segPiece: [Int32] = []
    var segIndex: [Int32] = []
    var segStart: [Int] = []
    var stamps: [Int32] = []
    var stampClock: Int32 = 0
    var cuts: [Cut] = []
    var cutsBySeg: [Int: [(t: Double, id: Int)]] = [:]
    var unions: [(VRef, VRef)] = []
    var bridges: [(VRef, VRef)] = []

    init(inputs: [TrailNetwork.Input], options: TrailNetwork.Options) {
        self.options = options
        // Group by key (duplicates concatenate), sorted for determinism.
        var order: [Key] = []
        var segments: [Key: [[[Double]]]] = [:]
        var profiles: [Key: [Int]] = [:]
        for input in inputs {
            let key = input.key
            if segments[key] == nil {
                order.append(key)
                segments[key] = []
            }
            segments[key]! += input.trail.segments
            if profiles[key] == nil, let p = input.trail.profileFt, !p.isEmpty { profiles[key] = p }
        }
        order.sort()

        // One projection origin per network: centre of the bounding box.
        var minLat = Double.infinity, maxLat = -Double.infinity
        var minLon = Double.infinity, maxLon = -Double.infinity
        for key in order {
            for seg in segments[key]! {
                for p in seg where p.count >= 2 && p[0].isFinite && p[1].isFinite {
                    minLat = min(minLat, p[0]); maxLat = max(maxLat, p[0])
                    minLon = min(minLon, p[1]); maxLon = max(maxLon, p[1])
                }
            }
        }
        if minLat > maxLat { minLat = 0; maxLat = 0; minLon = 0; maxLon = 0 }
        projector = TurnCueProjector(
            originLat: (minLat + maxLat) / 2, originLon: (minLon + maxLon) / 2)

        for key in order {
            var info = TrailInfo(key: key, profile: profiles[key])
            let trailIndex = trails.count
            for seg in segments[key]! {
                var pts: [LocalPt] = []
                var ll: [Coordinate] = []
                for p in seg where p.count >= 2 && p[0].isFinite && p[1].isFinite {
                    let lp = projector.local(lat: p[0], lon: p[1])
                    if let last = pts.last, TurnCueGeometry.distance(last, lp) < 0.01 { continue }
                    pts.append(lp)
                    ll.append(Coordinate(lat: p[0], lon: p[1]))
                }
                guard pts.count >= 2 else { continue }
                info.pieces.append(pieces.count)
                pieces.append(Piece(trail: trailIndex, pts: pts, ll: ll, cum: TurnCueGeometry.cumulative(pts)))
            }
            chain(&info)
            trails.append(info)
        }
    }

    /// Orders a trail's pieces like the elevation pipeline and records each
    /// piece's offset in that chain.
    private mutating func chain(_ info: inout TrailInfo) {
        guard !info.pieces.isEmpty else { return }
        let heads = info.pieces.map { pieces[$0].pts.first! }
        let tails = info.pieces.map { pieces[$0].pts.last! }
        let result = TrailNetworkChain.order(heads: heads, tails: tails)
        var offset = 0.0
        for link in result.links {
            let idx = info.pieces[link.piece]
            pieces[idx].chainOffset = offset
            pieces[idx].flipped = link.flipped
            offset += pieces[idx].length
        }
        info.chainTotal = offset
        info.maxGap = result.maxGap
        info.elevationMapped = result.exhaustive && info.profile != nil && offset > 0
    }

    static func coordinate(of p: LocalPt, projector: TurnCueProjector) -> Coordinate {
        let mpd = TurnCueProjector.metersPerDegree
        let cosLat = cos(projector.originLat * .pi / 180)
        return Coordinate(
            lat: projector.originLat + p.y / mpd,
            lon: projector.originLon + p.x / (cosLat * mpd))
    }
}

/// Uniform grid over segments, keyed by cell.
struct SegmentGrid {
    let cell: Double
    var cells: [Int64: [Int32]] = [:]

    init(cell: Double) { self.cell = cell }

    static func key(_ cx: Int, _ cy: Int) -> Int64 {
        (Int64(cx) << 32) ^ (Int64(cy) & 0xffff_ffff)
    }

    func cellRange(_ lo: Double, _ hi: Double) -> ClosedRange<Int> {
        Int((lo / cell).rounded(.down))...Int((hi / cell).rounded(.down))
    }

    mutating func insert(_ id: Int32, a: LocalPt, b: LocalPt) {
        for cx in cellRange(min(a.x, b.x), max(a.x, b.x)) {
            for cy in cellRange(min(a.y, b.y), max(a.y, b.y)) {
                cells[Self.key(cx, cy), default: []].append(id)
            }
        }
    }
}
