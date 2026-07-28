import Foundation
import OSLog

private let log = Logger(subsystem: "com.trekdex.app", category: "parkingPool")

/// Every qualifying parking lot in the country, owned by nobody.
///
/// WHY THIS EXISTS. Parking ships inside each area's geom, so the pipeline has to
/// decide which area a lot BELONGS to — and that one decision is the source of a
/// long tail of trouble: the `_FED_EDGE_BUFFER_M` tiebreak over which blank area
/// gets an orphan trailhead, NPS overlook lots flowing onto a nested wilderness,
/// and the 2,010 parking-blank areas with no boundary at all, which can never be
/// filled no matter how the buffer is tuned.
///
/// None of that is inherent, because nothing in the app ever asks who owns a lot.
/// `Area.nearestParking` asks one question: what is near THIS trail. The shipped
/// format already disagrees with ownership too — of 39,512 lots only 29,365 are
/// distinct positions, so 4,519 already appear under more than one area.
///
/// So the pool keeps containment as a QUALITY FILTER (a lot is here because it
/// passed the gate somewhere) and drops it as OWNERSHIP. Proximity alone would not
/// do: it cannot tell "inside the park" from "across the road", which is why
/// Thunderbird went from 26 lots to 12 once containment landed.
///
/// Built by `scripts/build-parking-pool.py`, ~0.32 MB gzipped — a quarter of the
/// trail-search index. Best-effort and ADDITIVE: until it loads, and whenever it
/// cannot, `Area.nearestParking` uses the area's own `parking` exactly as before,
/// so behaviour never regresses below today's.
@MainActor
@Observable
final class ParkingPoolService {
    static let shared = ParkingPoolService()

    private(set) var lots: [ParkingLot] = []
    /// Lots bucketed by a ~275 m cell so a lookup scans neighbours rather than
    /// 29k rows. The 805 m display threshold spans at most three cells.
    private var cells: [Cell: [ParkingLot]] = [:]

    struct Cell: Hashable { let i: Int; let j: Int }
    private static let cellSize = 0.0025

    private let cdnURL = URL(string: "https://cdn.trekdex.app/parking.json")!
    private static let etagKey = "parkingPoolETag"
    private let cachedURL: URL = {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("parking.json")
    }()

    private var loading = false

    private init() {}

    /// Read the disk copy off-main, then revalidate. Same shape as
    /// `TrailSearchService.loadIfNeeded` — parsing 29k rows must not block a
    /// frame, and the cached copy makes parking work offline.
    func loadIfNeeded() async {
        guard cells.isEmpty, !loading else { return }
        loading = true
        defer { loading = false }
        let url = cachedURL
        let cached = await Task.detached(priority: .utility) { () -> [ParkingLot] in
            guard let data = try? Data(contentsOf: url) else { return [] }
            return Self.parse(data)
        }.value
        if cells.isEmpty, !cached.isEmpty { install(cached) }
        await revalidate()
    }

    /// Lots within `meters` of any of `points`, nearest first. Returns [] when the
    /// pool has not loaded, which is the signal for the caller to fall back.
    ///
    /// Deduped by POSITION while scanning, so a facility that several trail
    /// endpoints all reach stays one pin at its shortest distance — and the scan
    /// only ever touches the cells around the given points, never the whole pool.
    func lots(near points: [(Double, Double)],
              within meters: Double) -> [(lot: ParkingLot, meters: Double)] {
        guard !cells.isEmpty, !points.isEmpty else { return [] }
        let span = Int(meters / (Self.cellSize * 111_320)) + 1
        var best: [Position: (lot: ParkingLot, meters: Double)] = [:]
        for (lat, lon) in points {
            let ci = Int(lat / Self.cellSize), cj = Int(lon / Self.cellSize)
            for i in (ci - span)...(ci + span) {
                for j in (cj - span)...(cj + span) {
                    for lot in cells[Cell(i: i, j: j)] ?? [] {
                        let d = Area.meters(lat, lon, lot.lat, lot.lon)
                        guard d <= meters else { continue }
                        let key = Position(lat: lot.lat, lon: lot.lon)
                        if let prior = best[key], prior.meters <= d { continue }
                        best[key] = (lot: lot, meters: d)
                    }
                }
            }
        }
        return best.values.sorted { $0.meters < $1.meters }
    }

    private struct Position: Hashable {
        let lat: Double
        let lon: Double
    }

    private func install(_ parsed: [ParkingLot]) {
        guard !parsed.isEmpty else { return }
        lots = parsed
        var built: [Cell: [ParkingLot]] = [:]
        for lot in parsed {
            let key = Cell(i: Int(lot.lat / Self.cellSize), j: Int(lot.lon / Self.cellSize))
            built[key, default: []].append(lot)
        }
        cells = built
    }

    /// Fetch with `If-None-Match`; 304 keeps what we have. Errors are swallowed —
    /// the per-area fallback keeps parking working, so a failed pool load is
    /// invisible rather than a regression.
    func revalidate() async {
        var request = URLRequest(url: cdnURL)
        // Bypass the local HTTP cache so If-None-Match reaches the origin — same
        // fix as AreaIndexService (#356) and TrailSearchService.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let etag = UserDefaults.standard.string(forKey: Self.etagKey) {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return }
            if http.statusCode == 304 { return }
            guard (200..<300).contains(http.statusCode) else {
                log.notice("parking pool: HTTP \(http.statusCode)")
                return
            }
            let parsed = await Task.detached(priority: .utility) { Self.parse(data) }.value
            guard !parsed.isEmpty else {
                log.error("parking pool: parse empty, keeping prior")
                return
            }
            install(parsed)
            try? data.write(to: cachedURL, options: .atomic)
            if let newETag = http.value(forHTTPHeaderField: "ETag") {
                UserDefaults.standard.set(newETag, forKey: Self.etagKey)
            }
            log.notice("parking pool: \(parsed.count) lots")
        } catch {
            log.notice("parking pool: \(error.localizedDescription, privacy: .public)")
        }
    }

    /// The area's own lots plus every pooled lot within `meters` of this trail.
    ///
    /// The one place the pool is read for display, because THREE paths have to
    /// agree about the same trail: the map pins (`MapKitMapView`), the camera
    /// frame (`TrailMapView`), and the expanded row's banner (`TrailListView`).
    /// Wiring the pool into only one of them would have the banner name a lot the
    /// map never draws — which is the bug the pool exists to fix, moved one layer
    /// up.
    func merged(with own: [ParkingLot]?, for trail: Trail,
                within meters: Double = 805) -> [ParkingLot] {
        let pooled = lots(near: Area.trailEndpoints(trail), within: meters).map(\.lot)
        return Area.mergingPool(own, pooled)
    }

    /// Same merge across EVERY trail in the area — backs the "show all parking"
    /// toggle. Without it, turning "show all" on would HIDE a pooled lot the
    /// selected-trail view had just drawn, which reads as a bug whichever way
    /// round you hit it.
    ///
    /// The scan is cell-indexed and bounded by the trails' own endpoints, so a
    /// 400-trail area costs a few thousand dictionary lookups, not a pass over
    /// 29k lots.
    func merged(with own: [ParkingLot]?, forAnyOf trails: [Trail],
                within meters: Double = 805) -> [ParkingLot] {
        guard !cells.isEmpty else { return own ?? [] }
        let pooled = lots(near: trails.flatMap { Area.trailEndpoints($0) },
                          within: meters).map(\.lot)
        return Area.mergingPool(own, pooled)
    }

    /// `[[lat, lon, name, source, trailheadFlag, feeFlag]]`. A malformed row is skipped,
    /// never fatal — one bad row must not cost the whole pool.
    nonisolated static func parse(_ data: Data) -> [ParkingLot] {
        guard let rows = try? JSONDecoder().decode([[JSONValue]].self, from: data) else {
            return []
        }
        var out: [ParkingLot] = []
        out.reserveCapacity(rows.count)
        for r in rows where r.count >= 2 {
            guard let lat = r[0].doubleValue, let lon = r[1].doubleValue else { continue }
            // `fee` rides along as 1/0/absent so a pooled lot keeps the
            // paid/free label a per-area lot has; without it the pool would be a
            // small regression on exactly the detail people care about.
            let feeFlag = r.count > 5 ? r[5].doubleValue : nil
            out.append(ParkingLot(
                lat: lat, lon: lon,
                name: r.count > 2 ? r[2].stringValue : nil,
                fee: feeFlag.map { $0 > 0 },
                trailhead: r.count > 4 ? ((r[4].doubleValue ?? 0) > 0) : nil,
                source: r.count > 3 ? r[3].stringValue : nil))
        }
        return out
    }
}
