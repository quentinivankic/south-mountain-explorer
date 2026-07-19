# South Mountain Explorer (TrekDex) — Claude Primer

Source of truth for a fresh session. Read it first. It's a concise map, not
a changelog — **verify specific claims against the code before trusting them**,
and see the auto-memory files (`MEMORY.md` index) for deep detail on active
threads (parking, the coverage gap). Updated 2026-07-19.

---

## The app

iOS hiking tracker, **live on public TestFlight** with active external testers.
Browse parks/preserves, record GPS-tracked hikes, track completion of every
trail in an area. SwiftUI, iOS 18+ deployment target.

- **Tabs:** Explore · Browse · **Stats** · Settings (`App/ContentView.swift`).
  Stats/Insights dashboards exist (`Views/Stats/`).
- **Recording:** trail-mode + roam-mode; app-kill recovery via
  `restoreActiveRecording` (12 h) from `StorageKeys.activeRecording`; GPS path
  → `Documents/hike-history.json` (schema v2, cold-launch migration); 2 s poll;
  background location.
- **Map** (`Views/Area/MapKitMapView.swift`): standard/satellite/hybrid,
  free/follow/follow+heading tracking, pinch-zoom preserved. Orange
  "since-completion" run = `liveTrailSnapped*` overlay. Apple base-map POIs are
  excluded (`pointOfInterestFilter = .excludingAll`) — every pin is ours.
- **Coverage** (`Services/CoverageService.swift`): lifetime + since-completion,
  per area/trail, 95% completion threshold. `mergeCoverage` does NOT write
  `sinceCompletion` live — `setSinceCompletion` (10 m math) + `resetSinceCompletion`
  are authoritative.
- **Units:** imperial/metric fully wired (distance + elevation) via
  `Utilities/UnitFormatter.swift` — route all display through it.
- **Parking pins** (`MapKitMapView` + `Models/Area.swift`): hidden while
  browsing; tapping a trail shows its ≤3 nearest lots (`Area.nearestParking`)
  and frames the zoom. Federal trailheads draw as a green hiker marker, OSM/NPS
  lots as blue "P". See auto-memory `parking-feature.md`.
- **Backup:** Settings → Export bundles UserDefaults keys + `hike-history.json`
  + `activity-log.json` to one JSON via share sheet; Import restores. All five
  `@Observable` singletons have `resetAll()`/`reload()`. `DataBackupManager.swift`.
- **iOS 18 + Liquid Glass:** `.glassEffect` call sites route through
  `Utilities/GlassCompat.swift` (`#available(iOS 26)` → real glass, else
  `.regularMaterial`). Use `.compatibleGlass*`, never `.glassEffect` directly,
  or iOS-18 compile breaks.

**Index IS R2-served (verified in code 2026-07-19):** `AreaIndexService` fetches
`cdn.trekdex.app/index.json` on launch with ETag revalidation, bundle as
offline fallback; `AreaDataService` prefers the R2 copy and reloads on a newer
one. `sync-geom-to-r2` uploads the bundle index to R2 (300 s TTL), and the
publish workflows regen the bundle (`filter-ios-bundle.py`) + commit it. So NEW
areas reach OLD apps via R2 on next launch — **no build needed for coverage**;
a build is only needed for app CODE changes. (An earlier version of this file
wrongly listed this as "not built" — verify against code, always.)

**Not built (don't propose as if missing by oversight):** Live Activity /
widgets, distance-to-next-turn banner, cloud sync (Backup copy is aspirational),
`PrivacyInfo.xcprivacy` (public TF accepted without it — NOT a blocker).

---

## The data engine — trailforge (`trailforge/`)

Global AllTrails-quality trails from OSM on a modest homelab (filter-first
streaming, never a full planet DB). Flow:

```
OSM extract (Geofabrik/planet) → prefilter (hiking-only PBF) → aoi (bbox cut,
  osmium --strategy=smart so park boundaries stay whole) → assemble (relations
  → name-stitch → spur-attach → merge → curation) → serve/publish_areas.py
  (per-area boundary clip + convert + validate) → public/areas/geom/<id>.json
  + master public/areas/index.json → silhouettes-from-geom.py (card art) →
  filter-ios-bundle.py (ios .../Resources/areas-index.json, NA-only, gated on
  clean geom = no cached_at) → push main → sync-geom-to-r2.yml → R2
  (cdn.trekdex.app) → app fetches geom/silhouettes with a bundled fallback.
```

- **Coverage:** ~8,860 areas ship with clean trail geom (the iOS bundle set).
  The master index has ~29,852 rows; the rest are seeded-but-unpublished or
  non-hikeable.
- **`add-parking.py`** is a post-process that enriches geom with OSM/federal
  parking (see `parking-feature.md`). Publish PRESERVES parking on republish
  (`publish_areas.py` + `merge-published-geom.py` carry it forward).
- **Elevation difficulty** is DEM-sampled inline at publish
  (`serve/elevation.py`, gain-based NPS rating, direction-invariant). AZ baked;
  other states length-based until republished.

### Environments — pick the right path each time
- **Homelab** = a Debian box with the repo at `~/south-mountain-explorer`. Runs
  the full pipeline + has OSM/Geofabrik/DEM/Overpass egress the sandbox lacks.
  `data/` is scratch (regenerable). **A session may be teleported here — then
  you have full homelab access and run the pipeline directly.**
- **Work = GitHub only.** User dispatches `trailforge-publish*.yml` /
  `ios-testflight.yml` from the Actions UI, reviews PRs, watches CI.
- **Cloud sandbox (default Claude):** reaches GitHub + repo, NOT
  OSM/Overpass/DEM, and CANNOT dispatch workflows (MCP token 403s) — hand the
  user Actions-UI steps or homelab commands.
- **Timezone: Arizona (MST, UTC−7).** Git/CI stamps are UTC — shift ~7 h.
- **Homelab `gh`:** browser device-flow fails headless; use
  `export GH_TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill | sed -n 's/^password=//p')`.
- **Testing = user's iPhone via TestFlight.** Data changes show after an in-app
  refresh (R2); app-CODE changes need a fresh TestFlight build.

### Publish flow (three dispatch-only workflows, all default dry_run=true)
Always dry-run + eyeball, then dry_run=false for the real write.
- `trailforge-publish.yml` — one state.
- `trailforge-publish-batch.yml` — `states: a,b,c`|`all`, serial, ONE commit at
  end (keep batches ~5-8 states; a mid-loop timeout loses the run).
- `trailforge-publish-us.yml` — whole-US: 13 parallel region jobs → artifacts →
  `merge-published-geom.py` fan-in → one commit + R2 sync.

**Gotcha:** `publish_areas.py`'s printed skip list is capped at `[:40]` — the
real total is in the `skipped {N}` header, not the visible lines.

---

## Curation policy (`trailforge/assemble/model.py` unless noted)

Each rule was learned from a REAL discovered bad example and verified against a
growing regression list — **don't undo without re-reading**. Verdicts run
through `_removal_verdict()` → `(category, sentence)`; dropped trails carry
`removed_category` for the viewer's review buckets.

- **`mtb:scale:imba` is NOT a bike signal** — it's a difficulty rating on
  shared-use HIKING trails (once ate all of South Mountain).
- **MTB-named trails:** `_BIKEPARK_NAME` vocabulary trusted only WITH an imba
  co-signal; but whole-word `MTB`/`mountain bike` in a name is an unconditional
  drop UNLESS the name has `/` or `' and '` (dual-use like `MTB/Hiking Trail`).
- **Access gate:** `is_access_blocked` drops `access=private|no` / `foot=no`,
  but `foot=yes|designated` WINS (a gated road open to walkers stays).
- **Ownership red flags** (`scripts/_seed_constants.py::red_flag`, auto-excludes
  at SEED time): narrow evidence-backed check for `mine/quarry`,
  `water supply/watershed`, or `hunting`, UNLESS operator reads government or
  name says `Trail(s)`. Catching a specific bad pattern scales; proving
  legitimacy doesn't (v1 required proof → 93% false positives). Full story in
  `scripts/audit-easement-ownership.py`.
- **KEY: contained = keep, cross-park = drop.** A trail living in ONE park is
  that park's, however long (Wonderland 93 mi, Long Trail 244 mi). Only
  genuinely cross-park named routes with no home get dropped (AT/PCT/CDT/…).
  **Never add a length cap** — it cuts the beloved loops. Thru-hikes via curated
  name registry `_THRU_HIKE_RE` (tags alone don't work); `_THRU_HIKE_REGIONAL`
  is empty by policy.
- **Name filters** (each spares `…Trail`): `is_motorized_name` (incl.
  fourwheeler), `is_utility_corridor_name` (bare powerline/pipeline ROWs, NOT
  'Row' place names), `is_nontrail_feature_name` (sidewalks/runways),
  `is_named_road_name` (bare Road/Rd/Highway — REVIEWABLE, eyeball-to-rescue
  washed-out roads), `is_nonhiking_route_name` (narrow: `<bike|climbing|
  glacier|evacuation> route`; most `…Route` are real hikes — Zion Narrows).
- **Degenerate-clip gate** (`publish_areas.py`, `_MIN_AREA_MI=0.1`): skip an
  area whose trails clip to <0.1 mi (self-healing on republish).
- **`scripts/sweep-geom-names.py`** re-applies NAME filters to published geom
  (reaches areas the publisher skips). Always `--dry-run` + eyeball.
- **Tag-based curation is sound; name-based inclusion is a dead end** — verified
  via the viewer. QA tooling in `trailforge/viewer/` (per-category "Removed"
  buckets, "Changed since last run" diff, ingest-filtered layer).

**Operating model:** trust the tested rule, review after the fact (not before).
`is_quality()` + `red_flag()` auto-exclude at seed time;
`audit-easement-ownership.py --all` is an after-the-fact report, not a gate. A
genuinely NEW red-flag category needs a real example + a test before it's
trusted — never speculatively enumerate patterns.

---

## Trail elevation profiles — the direction problem (decided 2026-07-19)

`serve/elevation.py` bakes `Trail.profileFt`: elevations in FEET, evenly spaced
BY DISTANCE (~8/mile, floor 8, cap 64; +7 MB on 430 MB measured). Even spacing
is deliberate — the app maps position→index as `fraction * (count - 1)` and no
distance array ships. One DEM pass (`sample_segments`) feeds both gain and
profile, so the expensive part didn't get more expensive.

**The problem, and it is the whole design.** OSM way order is ARBITRARY —
Humphreys Summit Trail is stored summit→trailhead. So `profileFt[0]` is NOT the
trailhead, and drawing the array raw means half of all trails show a descent for
what is really a climb. `gain_ft` sidesteps this by being direction-invariant
(`max(ascent, descent)`); a CHART cannot.

**DECIDED + SHIPPED in #445: orient by whichever trail end is nearest the
user, always** (`TrailProfile.startIsNearer`) — no distance cutoff. The "you are
here" marker still needs 50 m, but ORIENTATION always has an answer. One code
path, no fallback chain, degrades smoothly: arbitrary-ish at home, right while
driving in, exact at the trailhead. Chosen because every alternative has a hole,
not because it's clever.

**It is LATCHED when the chart opens, and that is load-bearing.** "Which end is
nearer" inverts at the trail's MIDPOINT, so recomputing live would mirror the
chart mid-hike on every point-to-point trail. Compares the two ENDPOINTS, not
the snapped fraction — snapping returns ~0.5 for anyone standing off the middle,
exactly where the answer must be most stable.

**Options rejected — do not re-propose without new evidence:**
- **Trail-network connectivity** ("the dead-end is the destination"). MEASURED
  over 55 random areas / 1,690 trails: only **33%** are spurs with one free end.
  37% have both ends at junctions, 28% both ends free, 2% loops — **~two-thirds
  give NO signal**, so it needs a fallback anyway, and it's the most work of
  anything considered. (The 28% is partly artifact: boundary-clipped trails look
  free-ended.)
- **Nearest parking lot as the anchor.** Built, CI-green, then REVERTED in the
  same PR. Parking exists for AZ only — 106 of 8,973 areas — so it fired for ~1%
  and coupled an elevation feature to the parking rollout.
- **Direction of travel** (flip so "ahead" is always right). Dropped with the
  above: it mirrors the chart when you turn round on an out-and-back, and needed
  `@State` + an `onChange` to damp the flapping. Latched nearest-end is stable
  for the whole hike and needs neither.
- **Low end on the left.** Right for most hikes, wrong for every canyon descent
  (Grand Canyon). A guess wearing a rule's clothes.
- **User drops a pin.** Works mechanically (a pin snaps exactly like a GPS fix),
  but asks the user a question they don't know they have — fine as an OVERRIDE,
  never as the default. Still the best candidate if the default proves wrong.

**What would change the decision:** parking reaching national coverage (makes
the trailhead anchor real), or complaints that browsed profiles read backwards
(then add the pin/flip override, don't swap the default).

App side: `Utilities/TrailProfile.swift` (snap / elevationFt / oriented), chart
in `Views/Area/TrailElevationProfileView.swift`, expanded into the selected row
in `TrailListView`. Kept separate from `ElevationProfileView`, which charts a
RECORDED hike in metres against real cumulative distance — merging them would
mean one view with two unit systems and two meanings for x.

---

## Active threads (2026-07-19) — see auto-memory for depth

- **Parking** (`parking-feature.md`): OSM containment-gated parking live for AZ.
  Federal fallback = NPS parking + USFS trailheads (BLM DROPPED — its "trailhead"
  layer is generic area-POI markers, not trailheads). Road-proximity gate on
  federal points. On-selection display + distinct trailhead marker shipped.
  Stale-geom cache bug FIXED (#424 — corrections now propagate; was the root of
  recurring "ghost pin" reports).
- **Coverage gap** (`coverage-gap-missing-areas.md` + the session task list):
  810 substantial areas don't ship (Great Smoky Mtns, Banff, big National
  Forests). Two causes: Canada never published (360), and US multi-state
  boundaries clipped at state lines in per-state extracts (450). **Fix shipped
  (#430/#432):** `publish_areas.py` fetches a multi-state area's boundary by
  `osm_rel` id via Overpass when PBF assembly fails, only for trail-bearing
  areas. Proven: GSMNP publishes 128 trails. **Remaining: re-publish the
  affected states** (NC staged + dry-run clean), then Canada (separate scope).
- **System-1 purged** (#428): 7,304 pre-trailforge `cached_at` geom deleted
  (never shipped; caused "has trails but doesn't ship" confusion). Nothing
  regenerates them unless the old `build-trail-index.yml` runs — don't.
- **DEFERRED — nested/duplicate areas** (task #37): e.g. "Saguaro" returns 4
  overlapping areas (park + 2 districts + wilderness). Real fix = a containment
  gate at publish (keep the iconic designation, drop redundant nested polygons).

---

## Build / release / CI

- **TestFlight:** public link live; uploads are **manual dispatch only**
  (`ios-testflight.yml`, workflow_dispatch). NEVER re-enable auto-on-push (#160).
- **PR CI:** `ios-pr-build.yml` (simulator compile + unit tests) runs on
  `ios/**`, `public/areas/index.json`, and the workflow file. ~8-13 min.
  **Wait for green before merging** these. Trailforge/scripts paths have NO CI
  gate — dry-run + eyeball is the gate there.
- **Merge authority:** the user authorized merging PRs autonomously once CI is
  green (squash + delete branch). Still eyeball a dry-run for no-CI-gate PRs. Do
  NOT auto-dispatch TestFlight.
- **R2 sync:** `sync-geom-to-r2.yml` auto-runs on push to `public/areas/geom/**`
  or `silhouettes/**` → R2 bucket `trekdex-areas` (`cdn.trekdex.app`). Also
  rebuilds `trail-search.json` + `trail-shapes.json`. Does NOT cover `index.json`.

---

## Things Claude gets wrong (read before proposing)

1. **Public TestFlight is LIVE.** Privacy-manifest / Beta-App-Review work is
   moot (Apple accepted builds without it). Valid only for App Store submission.
2. **Never merge before CI is green** on `ios-*` PRs. Poll `get_check_runs` for
   `conclusion: success` on the merge commit; don't merge right after opening.
3. **Verify state via code, not this file or a roadmap.** Both drift. grep/Read
   before claiming a feature ships or a PR is relevant.
4. **User hike data is irreplaceable** (real GPS recordings). Before any Reset /
   Import / force-delete flow: confirm a fresh Export exists; never run a
   destructive command on the device "to test."
5. **Never claim a PR is merged from memory** — call `pull_request_read` and
   check `merged: true`. (Burned before: #353/#354 claimed merged while still
   open.)

---

## Where to look
- Tabs / entry → `App/ContentView.swift`
- Recording → `Services/RecordingService.swift`; Coverage →
  `Services/CoverageService.swift`; Completion → `Services/ProgressService.swift`
- Map → `Views/Area/MapKitMapView.swift` + `TrailMapView.swift`
- Area data / cache → `Services/AreaDataService.swift`
- Settings / backup → `Views/Settings/SettingsView.swift`,
  `Utilities/DataBackupManager.swift`; Units → `Utilities/UnitFormatter.swift`
- Storage keys → `Utilities/StorageKeys.swift`; build config → `ios/project.yml`
- Pipeline → `trailforge/` (`Makefile`, `assemble/model.py`,
  `serve/publish_areas.py`); seeding/parking → `scripts/`
- Workflows → `.github/workflows/`

## When in doubt
Ask before: merging a PR (wait for CI), dispatching anything that uploads to
TestFlight, touching `Documents/hike-history.json` or `activity-log.json` on the
device, reverting on main, or force-pushing a shared branch. For outward-facing
or hard-to-reverse actions, confirm first unless already authorized.
