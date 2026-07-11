# South Mountain Explorer (TrekDex) — Claude Primer

This file is the source of truth for a fresh Claude session. Read it
first; it tells you what already exists, what's been deliberately
deferred, and what mistakes prior Claudes have made.

---

## ⭐ CURRENT STATE — read this first (updated 2026-07-11)

Much of the app-detail below predates the **trailforge** era. When it
conflicts with this section, this section wins. Verify against the code
before trusting any specific claim (see "Things Claude gets wrong").

**The user's setup — where work happens (pick the right path each time):**
- **Home = homelab.** A Debian/Ubuntu box with the repo cloned at
  `~/south-mountain-explorer`. Runs the full trailforge pipeline directly
  (download extract → prefilter → assemble → publish), dry-runs, and ad-hoc
  probes; has the OSM/Geofabrik/DEM network egress the sandbox lacks.
  `data/` (raw extracts, assembly) is scratch and can vanish between
  sessions — just re-download. Planet-scale / DEM work lives here.
- **Work = GitHub only.** When the user is at work they can't reach the
  homelab, so everything goes through GitHub: they dispatch
  `trailforge-publish*.yml` / `ios-testflight.yml` from the Actions UI,
  review PRs, watch CI. The batch workflow exists so multi-state publishes
  need no homelab. Ask which environment they're in if it matters.
- **You (Claude) run in a cloud sandbox.** It reaches GitHub (MCP tools) and
  the repo, but NOT OSM/Geofabrik/DEM sources, and CANNOT dispatch workflows
  (MCP token 403s) — so hand the user exact Actions-UI steps or homelab
  commands and have them paste output back.
- **Timezone: Arizona (MST, UTC−7).** Git/CI stamps are UTC — shift ~7h when
  reconciling with the user's "today" (a commit at 01:xxZ is the prior
  evening for them).
- **Testing: the user's iPhone via TestFlight.** Data changes show after an
  in-app refresh (R2); app-CODE changes need a fresh TestFlight build.

**Trailforge is the live trail pipeline** (`trailforge/`): OSM extract →
prefilter → assemble (relations-first → name-stitch → spur-attach → merge →
curation) → `serve/publish_areas.py` (per-area boundary clip + convert +
validate) → `public/areas/geom/*.json` + master `public/areas/index.json` →
`scripts/filter-ios-bundle.py` regenerates the app bundle
(`ios/.../Resources/areas-index.json`, NA-only, gated on clean trailforge
geom = no `cached_at`) → push to `main` auto-triggers `sync-geom-to-r2.yml`
→ R2 (`cdn.trekdex.app`) → app. `AreaSilhouetteService`/`AreaIndexService`
fetch from R2 with a bundled fallback.

**13 states live & clean:** Arizona, Utah, Colorado, Washington, Oregon,
New Mexico, Nevada (western batch) + Maine, New Hampshire, Vermont,
Massachusetts, Connecticut, Rhode Island (New England batch) — ~869 areas,
~24.9k trails, ~61k mi. (6 empty 0-trail areas pruned from the index.)
Data-only changes stream via R2 (no app build); app-CODE changes need a
TestFlight build. **Next: the rest of the US** — ~37 states left, ~6 region
batches; the gate is QA eyeball time, not compute (see review tooling below).

**Trail selection/curation policy (all in `trailforge/assemble/model.py`
unless noted), each learned the hard way — DON'T undo without re-reading.**
Curation runs through `_removal_verdict()` → `(category-slug, sentence)`;
`removal_reason`/`removal_category` are thin wrappers so the two can't drift.
Each dropped trail carries `removed_category` for the viewer's review buckets.
- `mtb:scale:imba` is NOT a bike/hike signal (it's a difficulty rating on
  shared-use HIKING trails — it once silently ate all of South Mountain).
- Bike-park flow trails: `_is_nonhiking` = `foot=no` / `mtb:type=flow|downhill`
  / non-hike piste / imba+`oneway=yes` / imba+bike-park-name.
- **Access gate:** `is_access_blocked` drops `access=private`/`access=no`
  (and `foot=no/private`) as category `access` — but `foot=yes/designated`
  WINS (a gated road open to walkers stays).
- Thru-routes: drop `kind=route` that no single park majority-contains
  (containment, in `serve/publish_areas.py::_clip_one`); keep contained
  single-park routes.
- Thru-HIKES (AZT/PCT/CDT/Colorado Trail/Hayduke…): OSM tags them
  inconsistently, so `is_thru_hike_name()` is a **curated name registry** +
  super-relation drop. Tags alone do NOT work — verified. Some are
  **region-gated** (Vermont's bare `Long Trail`, NM's `Skyline Trail` collide
  with local trails elsewhere → only thru-hikes in their own state; the sweep
  derives region from the slug suffix).
- Geometry: trim dangling out-of-park tails but keep in→gap→in connectors
  (`_trim_to_parks`); non-route trails kept WHOLE in each park they touch.
- Motorized/junk: `is_motorized_name` (incl. 4x4/motorcycle), road codes,
  grid addresses, `is_offtrail_name`, ≤2-char stub names.
- **Utility corridors:** `is_utility_corridor_name` — bare powerline /
  pipeline / gas-line / aqueduct ROWs; `…Trail`-suffixed ones kept. NOT 'Row'
  (Stone/Greek/Skid Row are place names).
- **Non-trail features:** `is_nontrail_feature_name` — sidewalks / runways /
  taxiways HARD, ski-lift lines (`Lift 8 Tower`) / bare parking SOFT (a
  `…Trail` path spares them).
- **Named roads (REVIEWABLE bucket):** `is_named_road_name` — bare `… Road /
  Rd / Highway`. Uses ONLY unambiguous road words (NOT Drive/Avenue/Street —
  that auto-keeps `Leif Erikson Drive`, `Park Avenue Trail`); spares `…Trail`
  and Acadia `Carriage Road`s. Drop-by-default but eyeball-to-rescue
  (washed-out roads still hiked).
- `scripts/sweep-geom-names.py` re-applies the NAME filters (not the tag ones —
  published geom has no tags) to published geom — reaches areas the publisher
  SKIPS (no boundary in extract), whose stale geom otherwise keeps old junk
  forever. Always `--dry-run` + eyeball first.

**QA review tooling (built to cut eyeball time — see `trailforge/viewer/`):**
the viewer's "Removed" layer is bucketed **per `removed_category`** (color +
count + checkbox — judge a whole class at once). "Changed since last run"
shows only the diff vs the last assemble (each trail has a stable `ckey` =
sorted member OSM ways; assemble writes `.curation.json` snapshot +
`.curation-diff.json`). "Ingest-filtered (named)" surfaces NAMED ways a TAG
gate dropped BEFORE assembly (`.ingest-dropped.geojson`), bucketed — the
`road-track-name` bucket is the false-negative-risk one to CHECK. `make
assemble REGION=xx` passes region so the viewer matches what ships.

**Publish flow:** CI is the norm — `trailforge-publish.yml` (single state)
and `trailforge-publish-batch.yml` (`states: a,b,c` or `all`). **Default
dry_run=true** — always dry-run + eyeball first, then dry_run=false for the
real write (one commit, one R2 sync). Homelab is the fallback for planet
scale. The MCP GitHub token can't dispatch workflows (403) — the USER
dispatches from the Actions UI. Trailforge paths have NO CI gate; iOS paths
do (`ios-pr-build`, must be green before merge). TestFlight upload is
manual `workflow_dispatch` only — never re-enable auto-trigger.

**Recent app UX (all shipped/merged to main):** tap a trail = highlight only
+ toggles selection; selected row's checkmark becomes a **Record** button;
three-dots GPX export is selection-aware. Tabs are Explore · Browse · **Stats**
· Settings. **Out-of-region waitlist** (`RegionSupport` gates on
`Locale.current.region`; `WaitlistCard` on the Explore/Home tab captures
country + email to **PostHog** — no separate backend): now has a **beta-tester
toggle** (`beta_interest`) and a **"Look around US & Canada"** button that
jumps to Browse (#352/#353). **Browse trail search** results now draw the
single trail's linework, not the whole area's (#354). Faint Tonto trail-mesh
backdrop is merged but **not rendering on device** — deferred/WIP. All these
ship in the NEXT TestFlight build (manual `ios-testflight` dispatch).

**Backlog (see the session task list):** (1) **named roads** — the reviewable
`named-road` bucket now collects 407 candidates; the remaining call is your
viewer eyeball (rescue washed-out-roads-still-hiked like `Dosewallips River
Road`) — plus ditches / stock-driveways per the research decision table;
(2) elevation-based difficulty from a global DEM (Copernicus GLO-30 / AWS
terrarium tiles) — see `trailforge/SPEC.md §6e`. Current difficulty is a
weak length bucket. (3) decide whether to whitelist `Powerline-Gypsum` (5.1mi,
White River NF — dropped by the utility filter, may be a real MTB connector).

**Working rhythm that's proven out:** small PR per change → for trailforge,
merge after unit tests + a homelab/dry-run eyeball; for iOS, wait for
`ios-pr-build` green → verify each state's dry-run before a real publish →
audit the published geom for regressions. The user catches issues by
eyeballing real data; back every claim with a probe, not memory.

---

## What this app is today

iOS hiking-tracker app, distributed via **public TestFlight (live,
active testers right now)**. Lets users browse parks/preserves, record
GPS-tracked hikes, and track completion of every trail in an area.

**Tabs (in order):** Explore · Browse · History · Settings.
History is still a hike list — *no* Stats dashboard tab exists today.
(`ios/SouthMountainExplorer/App/ContentView.swift`)

**Settings sections (top to bottom):** Account (Sign in with Apple) ·
Your Activity (inline stats card: hikes, miles, trails completed,
areas explored) · Appearance (theme) · Display (imperial/metric) ·
Trail Data (refresh, download favorites/nearby, manage) · Data
(Export, Import, Reset All Progress) · Feedback · Backup ·
Developer (debug HUD, Send Diagnostics) · About (version, build,
Privacy Policy link).

**Recording:** trail-mode + roam-mode. App-kill recovery via
`restoreActiveRecording` (12 h window) reading
`StorageKeys.activeRecording`. GPS path persists to
`Documents/hike-history.json` (schema v2 — migration runs at cold
launch). 2 s polling. Background tracking on `UIBackgroundModes:
[location]`.

**Map:** three styles (standard/satellite/hybrid), three-state
tracking (free / follow / follow+heading), pinch-zoom preserved.
Live trail-snapped overlay = the orange "since-completion" run on
the map. Symbols are named `liveTrailSnapped*` (already renamed from
`liveHalo*`).

**Coverage:** lifetime + since-completion buckets, both per
area/trail. 95% completion threshold. CoverageService.mergeCoverage
intentionally does NOT write `sinceCompletion` live — authoritative
sources are `setSinceCompletion` (length-based 10 m math) and
`resetSinceCompletion` (completion event itself).

**Data sourcing:**
- `areas-index.json`: **bundled only** (no R2-served index yet).
  At `ios/SouthMountainExplorer/Resources/areas-index.json`.
- Silhouettes + geom: served from R2 (`cdn.trekdex.app`), cached
  locally. AreaSilhouetteService / AreaDataService handle fetch.

**Units:** imperial/metric toggle is fully wired for distance AND
elevation. `Utilities/UnitFormatter.swift` is the formatter; all
display sites route through it (trail rows, area totals, hike
detail, stats, charts).

**Backup / restore:** Settings → Export Data bundles all
UserDefaults backup keys + `hike-history.json` + `activity-log.json`
into a single JSON blob saved via share sheet. Import restores from
the same file. Reset All Progress also wipes onboarding so the
new-user-experience test cycle works. `DataBackupManager.swift` +
`SettingsView.swift`. All five `@Observable` singletons have
`resetAll()` and `reload()` methods.

**Onboarding:** Single fullScreenCover (`Views/Onboarding/OnboardingView.swift`)
gated on `@AppStorage(StorageKeys.onboarded)`. Cleared by Reset All
Progress so the post-reset cycle re-presents it.

**Not built yet (despite various open PRs claiming otherwise):**
- Stats dashboard (no `Views/Stats/`)
- Live Activity / Dynamic Island / widget extension
- Distance-to-next-turn banner line
- R2-served areas-index (AreaIndexService doesn't exist on main)
- Cloud sync (Settings → Backup copy says "coming in future update"
  — that's aspirational, not a near-term commitment)
- PrivacyInfo.xcprivacy (not on main; public TF accepted without it)
- `docs/privacy.md` GitHub Pages (privacy policy is hosted on Notion;
  URL hardcoded in `SettingsView.swift:16`)

**iOS deployment target:** 18.0. Liquid Glass (`.glassEffect`)
call sites all route through `Utilities/GlassCompat.swift`, which
branches on `#available(iOS 26.0, *)` — iOS 26 renders real glass,
iOS 18 falls back to `.regularMaterial`. New glass surfaces must
use the `.compatibleGlass*` helpers, not `.glassEffect` directly,
or compilation breaks on iOS 18.

## Build / release state

**TestFlight:** Public link is **live in the world right now**.
Active external testers. Do NOT propose privacy-manifest or
Beta-App-Review work as a "blocker to public testing" — that ship
sailed.

**TestFlight uploads are manual-only.** `.github/workflows/ios-testflight.yml`
triggers on `workflow_dispatch` only. Removed the auto-on-push trigger
in #160 to stop burning upload slots on intermediate merges.
**Never propose re-enabling auto-trigger.**

**PR CI:** `.github/workflows/ios-pr-build.yml` runs simulator
compile + unit tests on every PR (paths: `ios/**`,
`public/areas/index.json`, the workflow file itself). Takes ~8-13
min. **Always wait for this to complete and report success before
merging.**

**Trail-index pipeline:** `.github/workflows/build-trail-index.yml`
is dispatch-only. Runs `scripts/seed-areas.py` (3× retry with
60/180/600 s backoff per region) + `scripts/build-trail-counts.py`
(concurrency 6, delay 1.0). `counts-cache.json` persists via Actions
cache. Workflow commits artifacts to main, then optionally dispatches
ios-testflight + sync-geom-to-r2 (the latter auto-runs on push to
`public/areas/geom/**` or `silhouettes/**`).

**R2 sync:** `.github/workflows/sync-geom-to-r2.yml` syncs to
Cloudflare R2 bucket `trekdex-areas`. Already covers geom +
silhouettes. Does NOT yet cover `index.json`.

**Recent main HEAD (as of 2026-07-11):**
- `1112b173` — trailforge: named-road reviewable bucket
- `2f67584f` — trailforge: split ingest track-drops (tag vs name) + per-bucket toggles
- `6d25f509` — trailforge: viewer surfaces named ways dropped by ingest tag-gates
- `fe65a233` — trailforge: access gate + non-trail-feature filter + guard net
- `0be36e26` — trailforge: review-time tooling (per-reason buckets + run-to-run diff)
- `d8293ec1` — trailforge: prune 6 empty areas from the index
- `e87919b7` — Waitlist: beta-tester opt-in + "look around" CTA (#352)
Earlier iOS baseline: `f899bdac` dropped min iOS target 26 → 18 (Liquid Glass
fallback in `Utilities/GlassCompat.swift`).

## Current goals

1. **Coverage expansion** — more regions in the trail index. Dispatch
   `build-trail-index.yml` with new region inputs as the user
   identifies them.

Everything else is opportunistic. PR K (drop iOS minimum to 18) shipped
as #165; the Material fallback for `.glassEffect` lives in
`Utilities/GlassCompat.swift` and needs visual confirmation on an
actual iOS 18 simulator/device when you cut the next TF build.

## Non-goals (don't surface these)

(None explicitly called out by the user — but the user can answer
"not now" to any proposed idea. Default to suggesting work tied to
the goals above.)

## Things Claude gets wrong (regularly)

These are the recurring failure modes from prior sessions. Read
before proposing anything.

### 1. Forgetting that public TestFlight is live

The app is in the world. Real testers use it. **Privacy manifest,
Beta App Review, public-link-generation work is moot** — Apple
already accepted builds without `PrivacyInfo.xcprivacy`. Don't
propose this as a gating task.

What still IS valid: App Store submission prep (different from
TestFlight), where the privacy manifest may matter.

### 2. Merging before CI is green

`ios-pr-build` is the gate. Wait for `get_check_runs` to report
`conclusion: success` on the merge commit before calling
`merge_pull_request`. Do NOT merge immediately after `create_pull_request`
returns — the check run isn't even registered yet.

The right cadence: open PR → poll check status (allow ~8-13 min) →
on green, merge → optionally dispatch `ios-testflight` later when
the user is ready for a build.

### 3. Trusting a stale roadmap as current truth

The plan file `~/.claude/plans/binary-hatching-toucan.md` was authored
in a previous session. The roadmap drifts as work ships. Always
verify codebase state via grep / Read before proposing work,
especially:
- "Is feature X already shipped?" — grep before claiming it isn't.
- "Is this PR still relevant?" — diff branch against current main
  before assuming.
- "What's the latest cadence?" — re-read recent commits, not the
  roadmap.

### 4. Forgetting the user's hike data is irreplaceable

Reset All Progress + Import + force-deletes touch user data that
**cannot be regenerated** (GPS recordings from real hikes). Before
suggesting any destructive flow:
1. Confirm a fresh Export sits in Files / iCloud Drive.
2. Suggest the AirDrop / external copy as belt-and-suspenders.
3. Never run a destructive command on the user's device "to test."

## Open PRs (as of 2026-05-19)

There are 11 open PRs from previous sessions. Their value has drifted:

**Already shipped to main — close these:**
- #130 (cache save-always workflow)
- #140 (sinceCompletion drop + halo rename)
- #142 (workflow concurrency bump)

**Partially shipped (some claims hold, some are stale) — rebase + reassess:**
- #143 (UI knockouts — VStack spacing already 0; offset-vs-padding still applies)
- #145 (R2-served index — workflow exists, `AreaIndexService` doesn't)
- #147 (Live Activity — files exist on branch; nothing wired into main)

**Outstanding (still genuinely missing from main) — valid PRs to revisit:**
- #110 (elevation units — HikeDetailView still hardcodes feet)
- #134 (zoom-aware follow shift)
- #141 (privacy manifest — but see "public TF is live" above; lower priority)
- #144 (distance-to-next-turn)
- #146 (Stats dashboard)

Older PRs (#110, #130, #134) are not drafts and may be ready to act
on. The May 18 batch (#140-#147) are all drafts.

**Rule of thumb:** any draft PR more than a week old is suspect.
Re-verify against current main before merging or recommending.

## Where to look first

- App entry / tabs → `ios/SouthMountainExplorer/App/ContentView.swift`
- Recording → `Services/RecordingService.swift`
- Coverage → `Services/CoverageService.swift`
- Completion state → `Services/ProgressService.swift`
- Favorites → `Services/FavoritesService.swift`
- Map → `Views/Area/MapKitMapView.swift` + `TrailMapView.swift`
- Settings → `Views/Settings/SettingsView.swift`
- Export / Import → `Utilities/DataBackupManager.swift`
- Units → `Utilities/UnitFormatter.swift`
- Storage keys → `Utilities/StorageKeys.swift`
- Build config → `ios/project.yml`
- Workflows → `.github/workflows/`
- Trail-index seeding → `scripts/seed-areas.py`,
  `scripts/build-trail-counts.py`

## When in doubt

Ask before acting. Especially before:
- Merging a PR (always wait for CI green).
- Dispatching a workflow that triggers a TestFlight upload.
- Running anything that touches `Documents/hike-history.json` or
  `Documents/activity-log.json` on the user's device.
- Reverting commits on main.
- Force-pushing to a branch the user is also working on.
