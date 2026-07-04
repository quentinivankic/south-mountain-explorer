#if DEBUG
import Foundation

/// DEBUG-only launch hooks that seed a rich, deterministic demo state
/// for **South Mountain Park & Preserve** so the App Store screenshot
/// UI test (and manual local runs) can capture populated Explore /
/// Dex / Stats / recording screens without real hikes or live GPS.
///
/// Compiled OUT of Release / TestFlight / App Store builds entirely
/// (`#if DEBUG`), so none of this — nor the demo data — can ship.
///
/// Activated by launch arguments (set by `ScreenshotTests`):
///   `--uitest-seed`       seed historical hikes + completions + coverage
///   `--uitest-recording`  additionally inject a live active recording
///
/// All seeding writes the same UserDefaults keys / `hike-history.json`
/// a real Import would, then re-hydrates the `@Observable` singletons
/// exactly like `DataBackupManager.performImport` does — so the app
/// sees genuine state, not a bespoke demo path.
enum UITestSupport {
    /// The flagship area — the app is named after it and it's the
    /// densest US area (48 trails), so it screenshots best.
    static let areaId = "south-mountain-park-and-preserve-az"

    private static var args: [String] { ProcessInfo.processInfo.arguments }
    static var isSeedRequested: Bool { args.contains("--uitest-seed") }
    static var isRecordingRequested: Bool { args.contains("--uitest-recording") }

    /// Called once from `SouthMountainExplorerApp.init()`. No-op unless
    /// `--uitest-seed` is present, so normal Debug launches are untouched.
    /// `@MainActor` because it re-hydrates the main-actor-isolated
    /// `@Observable` singletons (App.init is already main-actor-isolated).
    @MainActor
    static func handleLaunch() {
        guard isSeedRequested else { return }
        seedHistoricalState()
        // Re-read every service's in-memory copy from what we just wrote.
        // Mirrors DataBackupManager.performImport's reload block — the
        // singletons were already constructed (as stored properties of
        // the App) and loaded empty state before init()'s body ran.
        ProgressService.shared.reload()
        CoverageService.shared.reload()
        FavoritesService.shared.reload()
        RecordingService.shared.reload()
        // Inject the live recording AFTER reload, and in-memory only, so
        // the restore path (which starts background location tracking →
        // a permission alert that freezes the UI test) never runs.
        if isRecordingRequested {
            RecordingService.shared.injectDemoActiveRecording(makeDemoActiveRecording())
        }
    }

    // MARK: - Historical state

    private static func seedHistoricalState() {
        let ud = UserDefaults.standard
        ud.set(true, forKey: StorageKeys.onboarded)
        ud.set("imperial", forKey: StorageKeys.units)
        // Skip the region waitlist prompt regardless of the sim's locale.
        ud.set(true, forKey: StorageKeys.waitlistJoined)
        // Mark the history-classification migration already done so
        // RecordingService.init doesn't rewrite our hand-authored file.
        ud.set(2, forKey: StorageKeys.hikeHistoryMigrationVersion)

        // The recorded hikes come first — everything else derives from
        // them. Powers Stats totals, the hikes-per-month chart, the
        // distance/dedication Dex badges, and the hike-detail route +
        // elevation profile.
        let hikes = demoHikes()
        if let data = try? JSONEncoder().encode(hikes) {
            try? data.write(to: historyFileURL, options: .atomic)
        }

        // Completions = exactly the trails the seeded hikes completed
        // (12 of 48 ≈ 25%), stamped with the completing hike's end date
        // so the Dex earn dates line up with the history. A quarter done
        // is the sweet spot for the map shot: a satisfying cyan subset
        // against plenty of not-yet-hiked green/orange/red trails —
        // including uncompleted moderates and hards for color variety —
        // instead of a wall of cyan. The "Completionist" crown stays a
        // believable locked badge either way.
        let iso = ISO8601DateFormatter()
        var completions: [String: String] = [:]
        for hike in hikes.sorted(by: { $0.startedAt < $1.startedAt }) {
            for tid in hike.completedTrailIds where completions[tid] == nil {
                completions[tid] = iso.string(from: hike.endedAt)
            }
        }
        writeJSON([areaId: completions], forKey: StorageKeys.completedTrails)

        // Cosmetic: make the trail-detail "% remaining" bars read 100% /
        // 0%-remaining on completed trails.
        let lifetime = Dictionary(uniqueKeysWithValues: completions.keys.map { ($0, 1.0) })
        let since = Dictionary(uniqueKeysWithValues: completions.keys.map { ($0, 0.0) })
        writeJSON([areaId: lifetime], forKey: StorageKeys.coverage)
        writeJSON([areaId: since], forKey: StorageKeys.coverageSinceCompletion)

        // Favorite the area so it's pinned on Explore.
        writeJSON([areaId], forKey: StorageKeys.favorites)
    }

    private static func writeJSON<T: Encodable>(_ value: T, forKey key: String) {
        if let data = try? JSONEncoder().encode(value) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }

    private static var historyFileURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("hike-history.json")
    }

    // MARK: - Active recording (live panel shot)

    /// Build a plausible in-progress recording anchored to *now*, so the
    /// recording panel shows a live pace + elevation strip. Pace needs a
    /// tail of samples within the 60 s window spanning ≥30 s at a walking
    /// speed, so we synthesize a short realistic segment (≈1.3 m/s) whose
    /// last timestamp is the launch instant.
    private static func makeDemoActiveRecording() -> ActiveRecording {
        let now = Date()
        let nowMs = now.timeIntervalSince1970 * 1000
        let origin = trailCoords["national-trail"]?.first ?? [33.342839, -112.044092]

        var path: [GpsPoint] = []
        let count = 150                      // 150 samples × 2 s = 300 s of tail
        for i in 0..<count {
            // ~2.6 m north-east per 2 s step ≈ 1.3 m/s walking pace.
            let lat = origin[0] + 0.0000233 * Double(i)
            let lon = origin[1] + 0.0000120 * Double(i)
            let alt = 430.0 + Double(i) * 0.8            // gentle ~120 m climb
            let ts = nowMs - Double(count - 1 - i) * 2000
            path.append([
                (lat * 1e6).rounded() / 1e6,
                (lon * 1e6).rounded() / 1e6,
                ts,
                (alt * 10).rounded() / 10,
            ])
        }

        return ActiveRecording(
            areaId: areaId,
            mode: .trail,
            trailId: "national-trail",
            startedAt: now.addingTimeInterval(-25 * 60),  // "25:00" elapsed
            path: path,
            distanceMi: 1.24,
            priorCompleteTrailIds: []
        )
    }

    // MARK: - Demo hikes

    private struct HikeSpec {
        let trailId: String
        let distanceMi: Double
        let daysAgo: Int
        let startHour: Int
        let durationMin: Int
        let withPath: Bool
        /// Whether the hike completed its trail. A few hikes are seeded
        /// as non-completing visits so the completion set stays at ~25%
        /// of the area (12 of 48) with uncompleted moderates + hards
        /// left over — the map shot needs orange/red trails alongside
        /// the cyan ones, and "Completionist" should read as far off.
        var completes: Bool = true
    }

    /// ~21 hikes across 13 months. Designed to unlock every history-based
    /// Dex badge: total > 100 mi (Century Club + tiers), a ≥5 mi hike
    /// (Long Hauler), a pre-7 am start (Early Bird), starts spanning all
    /// four meteorological seasons (Four Seasons), and > 10 distinct days
    /// (Regular). The four most recent trace real trail geometry so the
    /// hike-detail route + elevation and the map's cyan halos look real.
    private static let hikeSpecs: [HikeSpec] = [
        // Featured, recent, real paths.
        HikeSpec(trailId: "national-trail",  distanceMi: 15.17, daysAgo: 2,   startHour: 6,  durationMin: 305, withPath: true),
        HikeSpec(trailId: "alta",            distanceMi: 4.60,  daysAgo: 5,   startHour: 8,  durationMin: 150, withPath: true),
        HikeSpec(trailId: "holbert-trail",   distanceMi: 2.60,  daysAgo: 9,   startHour: 7,  durationMin: 95,  withPath: true),
        HikeSpec(trailId: "desert-classic",  distanceMi: 3.88,  daysAgo: 13,  startHour: 9,  durationMin: 120, withPath: true),
        // Recent, real paths (halos), shorter.
        HikeSpec(trailId: "hau-pal-loop-trail",     distanceMi: 2.72, daysAgo: 20, startHour: 7,  durationMin: 88,  withPath: true),
        HikeSpec(trailId: "javelina-canyon-trail",  distanceMi: 2.94, daysAgo: 27, startHour: 8,  durationMin: 96,  withPath: true, completes: false),
        HikeSpec(trailId: "mormon-trail",           distanceMi: 1.36, daysAgo: 34, startHour: 7,  durationMin: 52,  withPath: true),
        HikeSpec(trailId: "kiwanis-trail",          distanceMi: 1.05, daysAgo: 41, startHour: 9,  durationMin: 40,  withPath: true),
        HikeSpec(trailId: "telegraph-pass-trail",   distanceMi: 0.72, daysAgo: 48, startHour: 8,  durationMin: 28,  withPath: true),
        // Older, distance/date only (no path needed for badges + chart).
        HikeSpec(trailId: "national-trail",             distanceMi: 15.17, daysAgo: 70,  startHour: 6,  durationMin: 300, withPath: false),
        HikeSpec(trailId: "alta",                       distanceMi: 4.60,  daysAgo: 95,  startHour: 8,  durationMin: 150, withPath: false),
        HikeSpec(trailId: "desert-classic",             distanceMi: 3.88,  daysAgo: 120, startHour: 9,  durationMin: 120, withPath: false),
        HikeSpec(trailId: "ma-ha-tuak-perimeter-trail", distanceMi: 7.13,  daysAgo: 145, startHour: 7,  durationMin: 230, withPath: false),
        HikeSpec(trailId: "desert-classic-trail",       distanceMi: 4.73,  daysAgo: 170, startHour: 8,  durationMin: 150, withPath: false),
        HikeSpec(trailId: "bursera-trail",              distanceMi: 3.32,  daysAgo: 195, startHour: 9,  durationMin: 110, withPath: false),
        HikeSpec(trailId: "guadalupe-perimeter-trail",  distanceMi: 2.75,  daysAgo: 220, startHour: 8,  durationMin: 92,  withPath: false, completes: false),
        HikeSpec(trailId: "las-lomitas-trail",          distanceMi: 2.79,  daysAgo: 250, startHour: 7,  durationMin: 94,  withPath: false),
        HikeSpec(trailId: "thondum-wihom-trail",        distanceMi: 2.40,  daysAgo: 280, startHour: 9,  durationMin: 82,  withPath: false, completes: false),
        HikeSpec(trailId: "national-trail",             distanceMi: 15.17, daysAgo: 310, startHour: 6,  durationMin: 300, withPath: false),
        HikeSpec(trailId: "javelina-canyon-trail",      distanceMi: 2.94,  daysAgo: 340, startHour: 8,  durationMin: 96,  withPath: false, completes: false),
        HikeSpec(trailId: "hau-pal-loop-trail",         distanceMi: 2.72,  daysAgo: 360, startHour: 7,  durationMin: 88,  withPath: false),
    ]

    private static func demoHikes() -> [SavedRecording] {
        let cal = Calendar.current
        let now = Date()
        var hikes: [SavedRecording] = hikeSpecs.map { spec in
            let day = cal.date(byAdding: .day, value: -spec.daysAgo, to: now) ?? now
            let start = cal.date(bySettingHour: spec.startHour, minute: 0, second: 0, of: day) ?? day
            let path = spec.withPath
                ? densifiedPath(coords: trailCoords[spec.trailId] ?? [], startMs: start.timeIntervalSince1970 * 1000)
                : []
            // Pathed hikes claim the HONEST length of their GPS path,
            // not the trail's official mileage. The hike-detail
            // elevation chart pins its X axis to the hike's distance
            // and plots samples at their path-derived positions — an
            // official 15.17 mi on a ~3 mi path squashed the profile
            // into the first tenth of the chart with dead air after.
            // Duration follows at a believable ~2.6 mph.
            let distanceMi: Double
            let durationSeconds: Int
            if spec.withPath {
                distanceMi = (pathLengthMi(path) * 100).rounded() / 100
                durationSeconds = Int(distanceMi / 2.6 * 3600)
            } else {
                distanceMi = spec.distanceMi
                durationSeconds = spec.durationMin * 60
            }
            return SavedRecording(
                id: "demo-\(spec.trailId)-\(spec.daysAgo)",
                areaId: areaId,
                startedAt: start,
                endedAt: start.addingTimeInterval(Double(durationSeconds)),
                distanceMi: distanceMi,
                durationSeconds: durationSeconds,
                completedTrailIds: spec.completes ? [spec.trailId] : [],
                path: path,
                trailId: spec.trailId,
                revisitedTrailIds: []
            )
        }

        // Honest path lengths shrink the pathed hikes well below their
        // trails' official mileage, which can drop the lifetime total
        // under the 100 mi Century Club badge. Top up with one long
        // roam-mode day sized to the gap so the distance-tier badges
        // stay earned no matter how the sampled paths measure.
        let totalMi = hikes.map(\.distanceMi).reduce(0, +)
        if totalMi < 101 {
            let fillerMi = ((101 - totalMi) * 100).rounded() / 100
            let day = cal.date(byAdding: .day, value: -87, to: now) ?? now
            let start = cal.date(bySettingHour: 6, minute: 30, second: 0, of: day) ?? day
            let durationSeconds = Int(fillerMi / 2.6 * 3600)
            hikes.append(SavedRecording(
                id: "demo-roam-filler",
                areaId: areaId,
                startedAt: start,
                endedAt: start.addingTimeInterval(Double(durationSeconds)),
                distanceMi: fillerMi,
                durationSeconds: durationSeconds,
                completedTrailIds: [],
                path: [],
                trailId: nil,
                revisitedTrailIds: []
            ))
        }
        return hikes
    }

    /// Cumulative haversine length of a GPS path, in miles.
    private static func pathLengthMi(_ path: [GpsPoint]) -> Double {
        guard path.count >= 2 else { return 0 }
        var meters = 0.0
        for i in 1..<path.count {
            meters += MapMath.haversineMeters(
                lat1: path[i - 1][0], lon1: path[i - 1][1],
                lat2: path[i][0], lon2: path[i][1]
            )
        }
        return meters / 1609.344
    }

    /// Interpolate sub-points between the sampled trail vertices and
    /// synthesize a smooth up-and-over altitude profile, so the saved
    /// hike traces the real trail and its elevation chart reads like a
    /// real climb. Timestamps are only cosmetic for a saved hike (the
    /// elevation X axis is cumulative distance, not time).
    private static func densifiedPath(coords: [[Double]], startMs: Double) -> [GpsPoint] {
        guard coords.count >= 2 else { return [] }
        let subdiv = 8
        let total = (coords.count - 1) * subdiv
        var pts: [GpsPoint] = []
        var idx = 0
        for i in 0..<(coords.count - 1) {
            let a = coords[i], b = coords[i + 1]
            for s in 0..<subdiv {
                let t = Double(s) / Double(subdiv)
                let lat = a[0] + (b[0] - a[0]) * t
                let lon = a[1] + (b[1] - a[1]) * t
                let frac = Double(idx) / Double(max(total, 1))
                let alt = 410.0 + 300.0 * sin(.pi * frac) + 12.0 * sin(6 * .pi * frac)
                pts.append([
                    (lat * 1e6).rounded() / 1e6,
                    (lon * 1e6).rounded() / 1e6,
                    startMs + Double(idx) * 2000,
                    (alt * 10).rounded() / 10,
                ])
                idx += 1
            }
        }
        let last = coords[coords.count - 1]
        pts.append([
            (last[0] * 1e6).rounded() / 1e6,
            (last[1] * 1e6).rounded() / 1e6,
            startMs + Double(idx) * 2000,
            410.0,
        ])
        return pts
    }

    /// Real sampled `[lat, lon]` vertices for the trails whose hikes get
    /// full paths. Pulled from the area's R2 geometry (coordinate order is
    /// `[lat, lon]`, NOT GeoJSON), so the recorded path overlays the real
    /// trail on the map.
    private static let trailCoords: [String: [[Double]]] = [
        "national-trail": [
            [33.342839, -112.044092], [33.341944, -112.048245], [33.339225, -112.05032],
            [33.337668, -112.054192], [33.336187, -112.056477], [33.335712, -112.059817],
            [33.33573, -112.063258], [33.33472, -112.066147], [33.335377, -112.069176],
            [33.333162, -112.070048], [33.330885, -112.071134], [33.329756, -112.073583],
        ],
        "holbert-trail": [
            [33.34521, -112.056957], [33.346118, -112.057434], [33.347441, -112.058051],
            [33.348777, -112.059084], [33.349976, -112.058864], [33.350466, -112.058099],
            [33.350769, -112.058195], [33.351475, -112.05714], [33.352531, -112.058252],
            [33.353539, -112.059305], [33.354431, -112.060341], [33.354116, -112.061999],
        ],
        "mormon-trail": [
            [33.366334, -112.030992], [33.364995, -112.030615], [33.364154, -112.029187],
            [33.36307, -112.028405], [33.36241, -112.027965], [33.361736, -112.026938],
            [33.361335, -112.026345], [33.360956, -112.025458], [33.360209, -112.024535],
            [33.358212, -112.023454], [33.358277, -112.022385], [33.357472, -112.021821],
        ],
        "telegraph-pass-trail": [
            [33.329756, -112.073583], [33.329426, -112.072648], [33.32894, -112.072748],
            [33.328422, -112.072077], [33.328015, -112.0713], [33.327544, -112.070676],
            [33.326841, -112.070233], [33.325982, -112.069962], [33.325235, -112.069393],
            [33.324147, -112.068651], [33.323332, -112.067758], [33.322893, -112.066611],
        ],
        "desert-classic": [
            [33.323004, -112.066526], [33.31973, -112.06184], [33.31978, -112.058559],
            [33.319692, -112.055603], [33.321114, -112.052725], [33.322854, -112.050031],
            [33.323754, -112.047252], [33.324529, -112.044295], [33.324324, -112.041353],
            [33.325666, -112.040237], [33.327788, -112.038931], [33.330208, -112.037097],
        ],
        "kiwanis-trail": [
            [33.328249, -112.073642], [33.329227, -112.074084], [33.330303, -112.074577],
            [33.332051, -112.074459], [33.333205, -112.074306], [33.333891, -112.075071],
            [33.334749, -112.07517], [33.335697, -112.074856], [33.336815, -112.074888],
            [33.338044, -112.074889], [33.339714, -112.075166], [33.34081, -112.07623],
        ],
        "hau-pal-loop-trail": [
            [33.344979, -112.094627], [33.343228, -112.094681], [33.343642, -112.096684],
            [33.342638, -112.098487], [33.342377, -112.100791], [33.341744, -112.102769],
            [33.341636, -112.105192], [33.342437, -112.10593], [33.344527, -112.105148],
            [33.346306, -112.104174], [33.348186, -112.102256], [33.350662, -112.101396],
        ],
        "alta": [
            [33.330314, -112.144145], [33.327994, -112.138475], [33.328154, -112.133864],
            [33.330335, -112.129334], [33.332074, -112.127057], [33.334668, -112.124649],
            [33.333882, -112.121337], [33.335369, -112.116635], [33.338848, -112.110851],
            [33.337834, -112.106239], [33.336538, -112.104086], [33.333091, -112.098251],
        ],
        "javelina-canyon-trail": [
            [33.374279, -111.985792], [33.372748, -111.989034], [33.371745, -111.991289],
            [33.372324, -111.992954], [33.371318, -111.993688], [33.369098, -111.993824],
            [33.368058, -111.996059], [33.367502, -111.998721], [33.36629, -112.000461],
            [33.365306, -112.002362], [33.36441, -112.004655], [33.363007, -112.006381],
        ],
    ]
}
#endif
