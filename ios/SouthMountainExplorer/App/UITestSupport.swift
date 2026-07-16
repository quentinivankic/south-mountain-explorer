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

    /// `--uitest-completed <n>`: seed n completed trails (the first n real IDs
    /// that aren't in `showcaseIncomplete`) instead of deriving completions
    /// from the demo hikes. Lets each screenshot tell its own story — the map
    /// shot stays a modest ~10 (no arg, hike-derived), while the Dex + Stats
    /// shots pass 70 to look accomplished, leaving the seven difficulty-varied
    /// showcase trails incomplete. Ignored in recording mode (its own override).
    static var uitestCompletedCount: Int? {
        guard let i = args.firstIndex(of: "--uitest-completed"),
              i + 1 < args.count else { return nil }
        return Int(args[i + 1])
    }

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
        // (~10 of 77), stamped with the completing hike's end date so the
        // Dex earn dates line up with the history. A modest fraction is the
        // sweet spot for the MAP shot: a satisfying cyan subset against
        // plenty of not-yet-hiked green/orange/red trails, instead of a wall
        // of cyan. `--uitest-completed N` (below) overrides this for the
        // Dex/Stats shots, which want to look nearly finished.
        let iso = ISO8601DateFormatter()
        var completions: [String: String] = [:]
        for hike in hikes.sorted(by: { $0.startedAt < $1.startedAt }) {
            for tid in hike.completedTrailIds where completions[tid] == nil {
                completions[tid] = iso.string(from: hike.endedAt)
            }
        }
        // `--uitest-completed N`: override to N completed trails, drawn from the
        // real IDs but ALWAYS leaving `showcaseIncomplete` uncompleted, so the
        // Dex + Stats shots read "N of 77" (accomplished) with a difficulty-
        // varied sprinkle of incomplete trails rather than the obscure tail of
        // the list. The default (no arg) stays the modest hike-derived subset
        // for the map shot.
        if let n = uitestCompletedCount {
            completions = [:]
            let pool = allTrailIds.filter { !showcaseIncomplete.contains($0) }
            for (i, tid) in pool.prefix(n).enumerated() {
                let date = Date().addingTimeInterval(-Double(5 + i * 3) * 86_400)
                completions[tid] = iso.string(from: date)
            }
        }
        // The RECORDING shot: the area is nearly finished (70 of 77) with the
        // live hike on Bajada Trail — one of the `showcaseIncomplete` seven.
        // Same difficulty-varied, west→east-spread incomplete set as the Dex /
        // Stats shots, so the completion state is consistent across shots.
        // None of the seven are history-completed, so the launch-time rebuild
        // can't flip them.
        if isRecordingRequested {
            completions = [:]
            for (i, tid) in allTrailIds.filter({ !showcaseIncomplete.contains($0) }).enumerated() {
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
        // Gentle profile: ~1,395 ft foothill base with ~230 ft gained so
        // far — a believable grade for 0.77 mi of bajada, still climbing
        // (no dome; the hike isn't done). The default 290 m dome put
        // ~1,250 ft of range under a sub-mile chart.
        var path = densifiedPath(
            coords: coords,
            startMs: 0,
            baseAltitudeMeters: 425,
            climbMeters: 70,
            domeClimb: false
        )
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
        /// Recency in days — used for the recent/featured hikes that must be
        /// robustly in the past regardless of the run date. Mutually exclusive
        /// with `monthsAgo`/`dayOfMonth`.
        var daysAgo: Int? = nil
        /// Calendar-month anchor for the chart-filler hikes: N months before
        /// the current month, on `dayOfMonth`. The Stats "Hikes per Month"
        /// chart buckets by CALENDAR month, so anchoring by month (not a
        /// rolling day count) is what guarantees every month reads >=2 no
        /// matter which day the screenshot run lands on.
        var monthsAgo: Int? = nil
        var dayOfMonth: Int = 15
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

    /// 27 hikes across the last 12 calendar months — at least TWO per month (a
    /// few months get a third for a livelier chart), so the Stats "Hikes per
    /// Month" chart always reads >= 2. It buckets by CALENDAR month, so the
    /// chart-filler hikes are anchored by `monthsAgo`/`dayOfMonth` (not a
    /// rolling day count, which drifts across calendar boundaries and left
    /// some months at 1). The current month gets the featured hike on daysAgo:2
    /// (most-recent + id `demo-national-trail-2` for the shot-5 lookup) plus two
    /// monthsAgo:0/dayOfMonth:1 companions, which stay in-month for any run day
    /// so the current month reads >= 2 even on a day-1/2 run.
    ///
    /// Preserves every history-based Dex badge: total > 100 mi (Century Club +
    /// tiers — a filler tops up if the sampled path shrinks it), a >= 5 mi hike
    /// (Long Hauler: National 15.17 / Ma-Ha-Tuak 7.13), a pre-7 am start (Early
    /// Bird: startHour 6), starts across all four seasons (12-month spread),
    /// and > 10 distinct days (Four Seasons / Regular). Exactly 11 distinct
    /// trails carry `completes: true` so the map shot reads "11 of 77."
    private static let hikeSpecs: [HikeSpec] = [
        // ---- Current month: the featured hike (the ONLY GPS-pathed one; the
        // map draws a cyan halo along it and shot 5 needs it for the route +
        // elevation) on daysAgo:2 so it's the most-recent row AND keeps the id
        // `demo-national-trail-2` the shot-5 lookup expects. PLUS two companions
        // anchored to monthsAgo:0 dayOfMonth:1, which land in the current
        // calendar month for ANY run day — so the current month reads >=2 even
        // when the screenshot run lands on day 1-2 and the daysAgo:2 featured
        // hike spills back into the previous month.
        HikeSpec(trailId: "national-trail", distanceMi: 15.17, daysAgo: 2, startHour: 6, durationMin: 305, withPath: true),
        HikeSpec(trailId: "alta-trail", distanceMi: 4.60, monthsAgo: 0, dayOfMonth: 1, startHour: 8, durationMin: 150, withPath: false),
        HikeSpec(trailId: "desert-classic-trail", distanceMi: 4.12, monthsAgo: 0, dayOfMonth: 1, startHour: 9, durationMin: 132, withPath: false, completes: false),
        // ---- Older months: distance/date only. 11 distinct completes:true
        // trails (national, alta, ma-ha-tuak, holbert, desert-classic,
        // hau-pal, mormon, kiwanis, telegraph, bursera, las-lomitas); the rest
        // are non-completing visits / repeats.
        HikeSpec(trailId: "ma-ha-tuak-perimeter-trail", distanceMi: 7.13, monthsAgo: 1, dayOfMonth: 22, startHour: 7, durationMin: 230, withPath: false),
        HikeSpec(trailId: "holbert-trail", distanceMi: 2.60, monthsAgo: 1, dayOfMonth: 8, startHour: 7, durationMin: 95, withPath: false),
        HikeSpec(trailId: "desert-classic-trail", distanceMi: 4.73, monthsAgo: 2, dayOfMonth: 22, startHour: 9, durationMin: 150, withPath: false),
        HikeSpec(trailId: "javelina-canyon-trail", distanceMi: 2.94, monthsAgo: 2, dayOfMonth: 9, startHour: 8, durationMin: 96, withPath: false, completes: false),
        HikeSpec(trailId: "hau-pal-loop-trail", distanceMi: 2.72, monthsAgo: 3, dayOfMonth: 19, startHour: 7, durationMin: 88, withPath: false),
        HikeSpec(trailId: "mormon-trail", distanceMi: 1.36, monthsAgo: 3, dayOfMonth: 7, startHour: 7, durationMin: 52, withPath: false),
        HikeSpec(trailId: "kiwanis-trail", distanceMi: 1.05, monthsAgo: 4, dayOfMonth: 21, startHour: 9, durationMin: 40, withPath: false),
        HikeSpec(trailId: "telegraph-pass-trail", distanceMi: 0.72, monthsAgo: 4, dayOfMonth: 10, startHour: 6, durationMin: 28, withPath: false),
        HikeSpec(trailId: "national-trail", distanceMi: 15.17, monthsAgo: 4, dayOfMonth: 15, startHour: 6, durationMin: 300, withPath: false, completes: false),
        HikeSpec(trailId: "bursera-trail", distanceMi: 3.32, monthsAgo: 5, dayOfMonth: 20, startHour: 9, durationMin: 110, withPath: false),
        HikeSpec(trailId: "las-lomitas-trail", distanceMi: 2.79, monthsAgo: 5, dayOfMonth: 8, startHour: 7, durationMin: 94, withPath: false),
        HikeSpec(trailId: "guadalupe-perimeter", distanceMi: 2.75, monthsAgo: 6, dayOfMonth: 22, startHour: 8, durationMin: 92, withPath: false, completes: false),
        HikeSpec(trailId: "thondum-wihom-trail", distanceMi: 2.40, monthsAgo: 6, dayOfMonth: 8, startHour: 9, durationMin: 82, withPath: false, completes: false),
        HikeSpec(trailId: "pima-canyon-loop-trail", distanceMi: 3.24, monthsAgo: 7, dayOfMonth: 20, startHour: 8, durationMin: 110, withPath: false, completes: false),
        HikeSpec(trailId: "desert-classic-trail", distanceMi: 3.88, monthsAgo: 7, dayOfMonth: 7, startHour: 9, durationMin: 120, withPath: false, completes: false),
        HikeSpec(trailId: "ma-ha-tuak-perimeter-trail", distanceMi: 7.13, monthsAgo: 8, dayOfMonth: 21, startHour: 7, durationMin: 230, withPath: false, completes: false),
        HikeSpec(trailId: "javelina-canyon-trail", distanceMi: 2.94, monthsAgo: 8, dayOfMonth: 9, startHour: 8, durationMin: 96, withPath: false, completes: false),
        HikeSpec(trailId: "national-trail", distanceMi: 15.17, monthsAgo: 9, dayOfMonth: 19, startHour: 6, durationMin: 300, withPath: false, completes: false),
        HikeSpec(trailId: "hau-pal-loop-trail", distanceMi: 2.72, monthsAgo: 9, dayOfMonth: 7, startHour: 7, durationMin: 88, withPath: false, completes: false),
        HikeSpec(trailId: "bursera-trail", distanceMi: 3.32, monthsAgo: 10, dayOfMonth: 20, startHour: 9, durationMin: 110, withPath: false, completes: false),
        HikeSpec(trailId: "alta-trail", distanceMi: 4.60, monthsAgo: 10, dayOfMonth: 8, startHour: 8, durationMin: 150, withPath: false, completes: false),
        HikeSpec(trailId: "las-lomitas-trail", distanceMi: 2.79, monthsAgo: 10, dayOfMonth: 14, startHour: 7, durationMin: 94, withPath: false, completes: false),
        HikeSpec(trailId: "desert-classic-trail", distanceMi: 4.73, monthsAgo: 11, dayOfMonth: 21, startHour: 9, durationMin: 150, withPath: false, completes: false),
        HikeSpec(trailId: "holbert-trail", distanceMi: 2.60, monthsAgo: 11, dayOfMonth: 9, startHour: 7, durationMin: 95, withPath: false, completes: false),
    ]

    private static func demoHikes() -> [SavedRecording] {
        let cal = Calendar.current
        let now = Date()
        let thisMonthStart = cal.dateInterval(of: .month, for: now)?.start ?? now
        var hikes: [SavedRecording] = hikeSpecs.map { spec in
            let day: Date
            if let d = spec.daysAgo {
                day = cal.date(byAdding: .day, value: -d, to: now) ?? now
            } else {
                let monthStart = cal.date(byAdding: .month, value: -(spec.monthsAgo ?? 0),
                                          to: thisMonthStart) ?? thisMonthStart
                day = cal.date(byAdding: .day, value: spec.dayOfMonth - 1, to: monthStart) ?? monthStart
            }
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
            let idSuffix = spec.daysAgo.map(String.init) ?? "m\(spec.monthsAgo ?? 0)d\(spec.dayOfMonth)"
            return SavedRecording(
                id: "demo-\(spec.trailId)-\(idSuffix)",
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
    /// `baseAltitudeMeters` / `climbMeters` / `domeClimb` shape the
    /// synthetic profile per hike. The featured National Trail hike
    /// keeps the defaults (a full ~290 m out-and-back dome). The LIVE
    /// recording must NOT reuse them: Bajada's demo track is only
    /// ~0.77 mi walked so far, and the dome profile crammed ~1,250 ft
    /// of elevation range into it — a visibly absurd grade in the
    /// recording panel's chart. It gets a gentle still-climbing ramp
    /// (`domeClimb: false`) from a realistic foothill base instead.
    private static func densifiedPath(
        coords: [[Double]],
        startMs: Double,
        baseAltitudeMeters: Double = 410,
        climbMeters: Double = 290,
        domeClimb: Bool = true
    ) -> [GpsPoint] {
        guard coords.count >= 2 else { return [] }
        let subdiv = 8
        let total = (coords.count - 1) * subdiv
        // ~1e-5 deg ≈ 1.1 m. Wobble amplitude ≈ ±5 m, varying slowly so
        // consecutive points drift together like a real GPS fix.
        func wobble(_ i: Int, _ phase: Double) -> Double {
            0.000045 * sin(Double(i) * 0.9 + phase) + 0.000018 * sin(Double(i) * 2.3 + phase * 1.7)
        }
        // Undulation scales with the main climb so a gentle profile
        // doesn't inherit mountain-sized bumps (floored so even a flat
        // walk still reads as terrain, not a synthetic line).
        let u = max(0.15, climbMeters / 290.0)
        func altitude(_ frac: Double, _ i: Int) -> Double {
            let mainClimb = domeClimb
                ? climbMeters * sin(.pi * frac)              // full-day out-and-back dome
                : climbMeters * frac                          // mid-hike: still on the way up
            return baseAltitudeMeters
                + mainClimb
                + 28.0 * u * sin(5.3 * .pi * frac + 0.8)     // ridgeline undulation
                + 13.0 * u * sin(11.7 * .pi * frac + 2.4)    // switchback bumps
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
    /// The area's REAL 77 trail IDs (from the published geom). Used to seed
    /// completions by count for the screenshots — so "N of 77 completed" reads
    /// exactly, and stale IDs can't be silently filtered out of the count.
    /// (Ordered as the geom lists them — the most iconic trails come first, so
    /// a partial "first N" completion still highlights recognizable trails.)
    private static let allTrailIds: [String] = [
        "national-trail", "desert-classic-trail", "ma-ha-tuak-perimeter-trail", "alta-trail",
        "guadalupe-perimeter", "bajada-trail", "bursera-trail", "pima-canyon-loop-trail",
        "holbert-trail", "gila-trail", "javelina-canyon-trail", "las-lomitas-trail",
        "hau-pal-loop-trail", "cholla-flats-loop", "thondum-wihom-trail", "upper-gila-trail",
        "prospector-loop", "ranger-trail", "beverly-canyon-trail", "max-delta-trail",
        "lost-ranch-trail", "corona-de-loma-trail", "ridgeline-trail", "pima-west-loop-trail",
        "old-man-trail", "pima-wash-trail", "mormon-trail", "ma-ha-tuak-trail",
        "marcos-de-niza-trail", "mormon-loop-trail", "bursera-canyon", "pima-east-loop",
        "beverly-pima-connector-trail", "lower-corona-de-loma-trail", "telegraph-pass-trail", "midlife-crisis",
        "crosscut-trail", "kiwanis-trail", "beacon-hill-trail", "young-man-trail",
        "sidewinder", "thash-kavid-south-trail", "dc-ray-connector", "devestator-trail",
        "pyramid-trail", "thash-kavid-north-trail", "shaughnessey-connector", "west-alta",
        "judith-tunnell-accessible-trail", "judith-tunnell-challenge-trail", "hidden-valley-trail", "t-bone-trail",
        "corona-to-dc-connector", "gila-lookout-connector", "telegraph-pass-trailhead-paved", "alta-to-hau-pal-connector",
        "alta-bajada-connector", "ma-ha-tuak-connector", "ridgeline-connector", "hidden-valley-connector-trail",
        "dobbins-lookout-connector", "lost-ranch-to-cholla", "pcl-to-beacon", "degoba-loop",
        "32nd-st-connector", "javelina-to-national-connector", "corona-to-dc-connector-2-x", "de-las-lomas-access-trail",
        "javelina-to-ridgeline", "prospector-to-national", "bursera-to-gila", "kachina-access-trail",
        "degoba-alt", "35th-ave-access-trail", "thash-kavid-trail", "gpt-to-dc-connector",
        "helipad",
    ]

    /// The 7 trails deliberately left INCOMPLETE in the near-complete shots
    /// (Dex / Stats / recording). Hand-picked to (a) span all three difficulty
    /// colors — Hard (red), Moderate (orange), Easy (green) — and (b) spread
    /// west→east across the park, so the incomplete trails read as a natural
    /// sprinkle among the cyan rather than a same-color cluster. None are
    /// completed by the demo history (so the launch-time rebuild can't flip
    /// them), and `bajada-trail` is here because the live recording is on it.
    /// NOTE post-elevation difficulty: `guadalupe-perimeter` is the only Hard
    /// trail the history doesn't already complete, and `bajada` is Moderate now.
    private static let showcaseIncomplete: Set<String> = [
        "west-alta",              // Easy   — far west
        "gila-trail",             // Moderate — west-central
        "bajada-trail",           // Moderate — central (the live recording)
        "pyramid-trail",          // Easy   — central
        "old-man-trail",          // Moderate — east
        "guadalupe-perimeter",    // Hard   — east (the one red incomplete)
        "pima-canyon-loop-trail", // Moderate — far east
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
