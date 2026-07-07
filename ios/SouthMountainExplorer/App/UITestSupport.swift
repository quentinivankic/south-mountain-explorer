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
        // The RECORDING shot tells the opposite story from the map shot:
        // the area is nearly finished (43 of 48) and the live hike is on
        // Bajada Trail — one of the last five. The remaining trails are
        // picked for color variety on the map: Bajada (red, in
        // progress), two moderates (orange), two easies (green). All of
        // the history-completed trails are inside the 43, so the
        // launch-time rebuild from history can't disturb the count.
        if isRecordingRequested {
            let remaining: Set<String> = [
                "bajada-trail",        // hard — the one being hiked live
                "gila-trail",          // moderate
                "cholla-flats-loop",   // moderate
                "beacon-hill-trail",   // easy
                "young-man-trail",     // easy
            ]
            completions = [:]
            for (i, tid) in allTrailIds.filter({ !remaining.contains($0) }).enumerated() {
                let date = Date().addingTimeInterval(-Double(10 + i * 8) * 86_400)
                completions[tid] = iso.string(from: date)
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

    /// Build a plausible in-progress recording anchored to *now*: a hike
    /// partway along BAJADA TRAIL — the red (hard) trail deliberately
    /// left uncompleted in recording mode, so the shot reads as
    /// "finishing one of my last trails." The path traces the real
    /// geometry (jittered like a genuine GPS track); timestamps are
    /// spaced 2 s apart ending at the launch instant so the live pace
    /// window (last 60 s, ≥30 s span) has real samples to chew on.
    private static func makeDemoActiveRecording() -> ActiveRecording {
        let now = Date()
        // Walk the first ~60% of Bajada — mid-hike, not nearly done.
        let coords = Array((trailCoords["bajada-trail"] ?? [[33.324312, -112.114956], [33.332971, -112.098175]]).prefix(8))
        var path = densifiedPath(coords: coords, startMs: 0)
        // Re-stamp timestamps from CUMULATIVE DISTANCE at a steady
        // 1.3 m/s (~2.9 mph) hiking speed, ending exactly at launch
        // time. The densified points are ~20-30 m apart, so uniform 2 s
        // stamps would imply a 30 mph "hike" — the live pace readout
        // divides real coordinate distance by timestamp span.
        let speedMps = 1.3
        let nowMs = now.timeIntervalSince1970 * 1000
        var cumulative: [Double] = [0]
        for i in 1..<path.count {
            let d = MapMath.haversineMeters(
                lat1: path[i - 1][0], lon1: path[i - 1][1],
                lat2: path[i][0], lon2: path[i][1]
            )
            cumulative.append(cumulative[i - 1] + d)
        }
        let totalMeters = cumulative.last ?? 0
        for i in 0..<path.count {
            path[i][2] = nowMs - (totalMeters - cumulative[i]) / speedMps * 1000
        }
        let distanceMi = (pathLengthMi(path) * 100).rounded() / 100
        let elapsed = totalMeters / speedMps

        return ActiveRecording(
            areaId: areaId,
            mode: .trail,
            trailId: "bajada-trail",
            startedAt: now.addingTimeInterval(-elapsed),
            path: path,
            distanceMi: distanceMi,
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

    /// ~21 hikes across 13 months. `daysAgo` is spaced to give the
    /// Stats "Hikes per Month" chart real month-to-month VARIATION
    /// (buckets of 0–4/month, not a flat run of 1s) — a livelier chart
    /// screenshots better. Still designed to unlock every history-based
    /// Dex badge: total > 100 mi (Century Club + tiers), a ≥5 mi hike
    /// (Long Hauler), a pre-7 am start (Early Bird), starts spanning all
    /// four meteorological seasons (Four Seasons), and > 10 distinct days
    /// (Regular). The most recent traces real trail geometry so the
    /// hike-detail route + elevation and the map's cyan halos look real.
    private static let hikeSpecs: [HikeSpec] = [
        // Featured hike — the ONLY one with a GPS path. The map draws a
        // translucent cyan halo along every recorded path, and synthetic
        // paths that hug their trails exactly rendered as a weird outline
        // around each completed trail in the map shots. One jittered
        // track reads as a real recording; shot 5 needs it for the route
        // map + elevation profile.
        HikeSpec(trailId: "national-trail",  distanceMi: 15.17, daysAgo: 2,   startHour: 6,  durationMin: 305, withPath: true),
        HikeSpec(trailId: "alta",            distanceMi: 4.60,  daysAgo: 5,   startHour: 8,  durationMin: 150, withPath: false),
        HikeSpec(trailId: "holbert-trail",   distanceMi: 2.60,  daysAgo: 12,  startHour: 7,  durationMin: 95,  withPath: false),
        HikeSpec(trailId: "desert-classic",  distanceMi: 3.88,  daysAgo: 22,  startHour: 9,  durationMin: 120, withPath: false),
        HikeSpec(trailId: "hau-pal-loop-trail",     distanceMi: 2.72, daysAgo: 31, startHour: 7,  durationMin: 88,  withPath: false),
        HikeSpec(trailId: "javelina-canyon-trail",  distanceMi: 2.94, daysAgo: 50, startHour: 8,  durationMin: 96,  withPath: false, completes: false),
        HikeSpec(trailId: "mormon-trail",           distanceMi: 1.36, daysAgo: 72, startHour: 7,  durationMin: 52,  withPath: false),
        HikeSpec(trailId: "kiwanis-trail",          distanceMi: 1.05, daysAgo: 82, startHour: 9,  durationMin: 40,  withPath: false),
        HikeSpec(trailId: "telegraph-pass-trail",   distanceMi: 0.72, daysAgo: 92, startHour: 8,  durationMin: 28,  withPath: false),
        // Older, distance/date only (no path needed for badges + chart).
        HikeSpec(trailId: "national-trail",             distanceMi: 15.17, daysAgo: 110, startHour: 6,  durationMin: 300, withPath: false),
        HikeSpec(trailId: "alta",                       distanceMi: 4.60,  daysAgo: 122, startHour: 8,  durationMin: 150, withPath: false),
        HikeSpec(trailId: "desert-classic",             distanceMi: 3.88,  daysAgo: 165, startHour: 9,  durationMin: 120, withPath: false),
        HikeSpec(trailId: "ma-ha-tuak-perimeter-trail", distanceMi: 7.13,  daysAgo: 180, startHour: 7,  durationMin: 230, withPath: false),
        HikeSpec(trailId: "desert-classic-trail",       distanceMi: 4.73,  daysAgo: 200, startHour: 8,  durationMin: 150, withPath: false),
        HikeSpec(trailId: "bursera-trail",              distanceMi: 3.32,  daysAgo: 225, startHour: 9,  durationMin: 110, withPath: false),
        HikeSpec(trailId: "guadalupe-perimeter-trail",  distanceMi: 2.75,  daysAgo: 235, startHour: 8,  durationMin: 92,  withPath: false, completes: false),
        HikeSpec(trailId: "las-lomitas-trail",          distanceMi: 2.79,  daysAgo: 245, startHour: 7,  durationMin: 94,  withPath: false),
        HikeSpec(trailId: "thondum-wihom-trail",        distanceMi: 2.40,  daysAgo: 265, startHour: 9,  durationMin: 82,  withPath: false, completes: false),
        HikeSpec(trailId: "national-trail",             distanceMi: 15.17, daysAgo: 290, startHour: 6,  durationMin: 300, withPath: false),
        HikeSpec(trailId: "javelina-canyon-trail",      distanceMi: 2.94,  daysAgo: 300, startHour: 8,  durationMin: 96,  withPath: false, completes: false),
        HikeSpec(trailId: "hau-pal-loop-trail",         distanceMi: 2.72,  daysAgo: 325, startHour: 7,  durationMin: 88,  withPath: false),
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

    /// Interpolate sub-points between the sampled trail vertices, add a
    /// few meters of deterministic GPS-style wobble, and synthesize a
    /// terrain-like altitude profile, so the saved hike reads as a real
    /// recording rather than a mathematically perfect trace. Jitter
    /// keeps the map's cyan past-hike halo looking like a GPS track
    /// (an exact-fit path rendered as a strange outline around the
    /// trail), and stays far inside the 30 m coverage buffer so
    /// completion math is unaffected. The altitude mixes a main climb
    /// with mid-frequency undulation + fine noise — a perfect sine dome
    /// read as obviously fake in the elevation chart.
    private static func densifiedPath(coords: [[Double]], startMs: Double) -> [GpsPoint] {
        guard coords.count >= 2 else { return [] }
        let subdiv = 8
        let total = (coords.count - 1) * subdiv
        // ~1e-5 deg ≈ 1.1 m. Wobble amplitude ≈ ±5 m, varying slowly so
        // consecutive points drift together like a real GPS fix.
        func wobble(_ i: Int, _ phase: Double) -> Double {
            0.000045 * sin(Double(i) * 0.9 + phase) + 0.000018 * sin(Double(i) * 2.3 + phase * 1.7)
        }
        func altitude(_ frac: Double, _ i: Int) -> Double {
            410.0
                + 290.0 * sin(.pi * frac)                    // the day's main climb
                + 28.0 * sin(5.3 * .pi * frac + 0.8)         // ridgeline undulation
                + 13.0 * sin(11.7 * .pi * frac + 2.4)        // switchback bumps
                + 4.0 * sin(Double(i) * 1.31 + 0.5)          // fine sensor noise
        }
        var pts: [GpsPoint] = []
        var idx = 0
        for i in 0..<(coords.count - 1) {
            let a = coords[i], b = coords[i + 1]
            for s in 0..<subdiv {
                let t = Double(s) / Double(subdiv)
                let lat = a[0] + (b[0] - a[0]) * t + wobble(idx, 0.0)
                let lon = a[1] + (b[1] - a[1]) * t + wobble(idx, 4.2)
                let frac = Double(idx) / Double(max(total, 1))
                pts.append([
                    (lat * 1e6).rounded() / 1e6,
                    (lon * 1e6).rounded() / 1e6,
                    startMs + Double(idx) * 2000,
                    (altitude(frac, idx) * 10).rounded() / 10,
                ])
                idx += 1
            }
        }
        let last = coords[coords.count - 1]
        pts.append([
            (last[0] * 1e6).rounded() / 1e6,
            (last[1] * 1e6).rounded() / 1e6,
            startMs + Double(idx) * 2000,
            (altitude(1.0, idx) * 10).rounded() / 10,
        ])
        return pts
    }

    /// The full 48-trail roster (from the area's R2 geom). Used only by
    /// the recording-mode completion override, which marks everything
    /// complete except the five hand-picked remaining trails.
    private static let allTrailIds: [String] = [
        "alta", "bajada-trail", "beacon-hill-trail", "beverly-pima-connector-trail",
        "bursera-canyon", "bursera-trail", "cholla-flats-loop", "corona-de-loma-trail",
        "crosscut-trail", "dc-ray-connector", "desert-classic", "desert-classic-trail",
        "devestator-trail", "gila-trail", "guadalupe-perimeter", "guadalupe-perimeter-trail",
        "hau-pal-loop-trail", "holbert-trail", "javelina-canyon-trail", "kiwanis-trail",
        "las-lomitas-trail", "lost-ranch-trail", "lower-corona-de-loma-trail",
        "ma-ha-tuak-perimeter-trail", "marcos-de-niza-trail", "max-delta-trail",
        "midlife-crisis", "mormon-trail", "national-trail", "old-man-trail",
        "pima-canyon-loop", "pima-canyon-loop-trail", "pima-wash-trail", "prospector-loop",
        "ranger-trail", "ridgeline-trail", "shaughnessey-connector", "sidewinder",
        "telegraph-pass-trail", "thash-kavid-north-trail", "thash-kavid-south-trail",
        "thondum-wihom-trail", "unnamed-1474825928", "unnamed-494466239",
        "unnamed-977459640", "upper-gila-trail", "west-alta", "young-man-trail",
    ]

    /// Real sampled `[lat, lon]` vertices (coordinate order is `[lat,
    /// lon]`, NOT GeoJSON) pulled from the area's R2 geometry. Only two
    /// trails need geometry: the featured hike's route (shot 5) and the
    /// live recording's trail (shot 3).
    private static let trailCoords: [String: [[Double]]] = [
        "national-trail": [
            [33.342839, -112.044092], [33.341944, -112.048245], [33.339225, -112.05032],
            [33.337668, -112.054192], [33.336187, -112.056477], [33.335712, -112.059817],
            [33.33573, -112.063258], [33.33472, -112.066147], [33.335377, -112.069176],
            [33.333162, -112.070048], [33.330885, -112.071134], [33.329756, -112.073583],
        ],
        "bajada-trail": [
            [33.324312, -112.114956], [33.324925, -112.112983], [33.325376, -112.111695],
            [33.325943, -112.110287], [33.326745, -112.108512], [33.327568, -112.106787],
            [33.328818, -112.105682], [33.330353, -112.104609], [33.330695, -112.103136],
            [33.331584, -112.101736], [33.332433, -112.100021], [33.332971, -112.098175],
        ],
    ]
}
#endif
