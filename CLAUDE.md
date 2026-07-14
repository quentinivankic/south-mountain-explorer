# South Mountain Explorer (TrekDex) — Claude Primer

This file is the source of truth for a fresh Claude session. Read it
first; it tells you what already exists, what's been deliberately
deferred, and what mistakes prior Claudes have made.

---

## ⭐ CURRENT STATE — read this first (updated 2026-07-14)

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
`scripts/silhouettes-from-geom.py` regenerates the Explore/Browse card art
(`public/areas/silhouettes/*.json`) FROM the clean geom — same no-`cached_at`
gate; before this the silhouettes were stale System-1 Overpass sketches that
still drew every road/utility/junk way trailforge now filters (Acadia: 156
clean geom trails vs a 402-line silhouette) → `scripts/filter-ios-bundle.py`
regenerates the app bundle (`ios/.../Resources/areas-index.json`, NA-only,
gated on clean trailforge geom = no `cached_at`) → push to `main` auto-triggers
`sync-geom-to-r2.yml` → R2 (`cdn.trekdex.app`) → app.
`AreaSilhouetteService`/`AreaIndexService` fetch from R2 with a bundled
fallback. All three publish workflows run the silhouette regen before the
bundle regen, so silhouettes never drift from the geom.

**Coverage — WHOLE US RE-SEEDED + RE-PUBLISHED with the full curation suite
(landed 2026-07-14, commit `236e2ff3` "publish ENTIRE US").** All 50 states +
DC re-seeded (way-vs-relation fix, MTB filter, ownership red-flag exclusion)
then republished via `trailforge-publish-us.yml`'s parallel fan-out. **~9,539
areas now ship with real trail geom** (up from ~5,400). NY/GA/VT were the
hand-vetted pilot; the whole-US run then rolled it to everything. Data streams
via R2 — no app build needed for coverage.

**2026-07-14 SESSION LANDMARKS (verify against code before trusting):**
- **DEM elevation difficulty** (`trailforge/serve/elevation.py` + `serve/add-elevation.py`,
  SPEC §6e). Difficulty is now a real function of elevation gain — the NPS
  numerical rating `sqrt(2·gain·mi)` + a pure-distance floor — replacing the
  length-only bucket. Gain = DEM-sampled (AWS Terrarium tiles), smoothed, and
  **DIRECTION-INVARIANT** (`max(total ascent, total descent)`; OSM way direction
  is arbitrary, so an ascent-only sum read Humphreys as 63 ft — the fix landed
  after calibrating to 99.4% vs AllTrails' ~3,333 ft). `add-elevation.py`
  post-processes published geom per state (`--state az`), writing `gainFt` +
  difficulty; run on the homelab (needs Pillow + DEM tile egress), then commit
  + regen silhouettes + sync. **Only Arizona is baked in so far** — the other 49
  are still length-based until run. Pure math unit-tested (`serve/test_elevation.py`).
  DURABLE FOLLOW-UP: fold sampling into the publish pipeline (it's a post-process
  now, lost on any republish).
- **Global trail-name search** (`TrailSearchService` + `scripts/build-trail-search-index.py`).
  Browse trail search used to match only trails in already-loaded areas (names
  aren't in the area index). Now a compact global index
  `[name,areaId,trailId,mi,difficulty]` is built + served from R2 as
  `trail-search.json` (~1.3 MB gz), so EVERY trail name is searchable.
- **Search-result trail thumbnails** (`TrailShapeService` + `scripts/build-trail-shapes.py`).
  Search rows draw each trail's linework via a SEPARATE, background-loaded
  `trail-shapes.json` (~3 MB gz — Douglas-Peucker to ~11 pts, normalized 0-255
  ints, keyed `areaId/trailId`), kept off the search critical path. (lat/lon
  encoding was rejected: +8.94 MB gz — coordinates don't compress.)
- **New curation filters** (`model.py` / `publish_areas.py`): `is_nonhiking_route_name`
  (bike/glacier/climbing/artifact `…Route` — NARROW, spares Zion Narrows /
  Grand Canyon routes); `fourwheeler` added to `is_motorized_name`; and a
  `_MIN_AREA_MI=0.1` degenerate-clip gate (skips an area whose trails clip to a
  near-zero-length sliver — 97 such live areas swept, commit `31c660c5`).
- **FINDING — tag-based curation is sound; name-based review is a dead end.**
  Built the deferred-review viewer + a road-track-name scanner
  (`trailforge/viewer/`); eyeballing confirmed rail/route-NAMED trails are
  correctly-included `highway=path` footpaths, and road-track-name drops are
  correctly roads. OSM tags are the right signal — don't chase NAME patterns
  for inclusion/exclusion. (Tooling stays for spot-checks.)
- **R2 now serves** `index.json` + per-area geom/silhouettes + `trail-search.json`
  + `trail-shapes.json`. The last two are built fresh + uploaded on every
  `sync-geom-to-r2` run (never committed to the repo).

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
- **MTB-named trails (no imba co-signal needed):** `_is_nonhiking`'s
  `_BIKEPARK_NAME` vocabulary (Downhill/Flow/Slalom/…) is only trusted when
  the way ALSO carries `mtb:scale:imba` — real bike-park trails often carry
  NONE of the tag signals (found via `Party of 5 (MTB)` and its whole
  Adirondack cluster — Venom Flow, Big Foot, Electric Ave…, zero imba/oneway/
  mtb:type tags, just an explicit name). Whole-word `MTB` or `mountain bike`
  in the name is now an unconditional drop UNLESS the name has a `/` or
  `' and '` (dual-use disclosure like `MTB/Hiking Trail`, or a merge artifact
  fusing two ways' names together) — audited 78 real matches, 67 clean drops.
- **Area-level ownership red flags (`scripts/_seed_constants.py::red_flag`,
  called from `is_quality()` — auto-excludes at SEED time, not curation):**
  areas asserting neither public nor private ownership (the tag's just
  missing) get a narrow, evidence-backed check — `mine/mining/quarry` or
  `water supply/watershed` in protection_title/description, or `hunting` in
  the name/title/description — UNLESS the operator reads as government
  (`Department of`/`County`/`City of`/`Town of`/`National Park Service`/…),
  the water-supply flag hits NYC DEP specifically (real public-access
  watershed land, not a closed reservoir buffer), or the name says `Trail(s)`.
  Found via `Bucktown LLC Conservation Easement` — private mining-company
  land (`description=mine`, private LLC operator) that passed every OTHER
  filter. v1 of this (require PROOF of public ownership) flagged 439/2957 NY
  candidates, ~93% false positives (legit county parks / land trusts / NYC
  DEP land the keyword list didn't recognize) — proving legitimacy is
  unbounded; catching the narrow bad pattern is what scales. See
  `scripts/audit-easement-ownership.py`'s docstring for the full story.
- **KEY POLICY — contained = keep, cross-park = drop.** A trail that lives in
  ONE park is that park's trail, HOWEVER LONG → KEPT (Wonderland 93mi, Tonto
  91mi, Northville-Placid 124mi, VT's Long Trail 244mi, Pinhoti 154mi, Lone
  Star 80mi). Only genuinely CROSS-PARK named routes that smear across many
  areas with no home get dropped (AT/PCT/CDT/Colorado Trail/Great Allegheny/
  NET/Cohos…). Do NOT add a length cap — it would cut the beloved loops.
- Thru-routes: drop `kind=route` that no single park majority-contains
  (containment, `serve/publish_areas.py::_clip_one`); keep contained ones.
- Thru-HIKES: `is_thru_hike_name()` is a curated name registry (`_THRU_HIKE_RE`,
  always-match cross-park names) + super-relation drop. Tags alone do NOT work.
  `_THRU_HIKE_REGIONAL` (region-scoped names) is **now EMPTY by policy** — Long
  Trail + Skyline were the only entries and are contained, so they're kept
  (the region hook stays wired for a future must-scope name). NB: the publish
  workflows don't pass `--region`, which is why keeping region-gating empty is
  also the robust choice.
- Geometry: trim dangling out-of-park tails but keep in→gap→in connectors
  (`_trim_to_parks`); non-route trails kept WHOLE in each park they touch.
- Motorized/junk: `is_motorized_name` (incl. 4x4/motorcycle/**fourwheeler**),
  road codes, grid addresses, `is_offtrail_name`, ≤2-char stub names.
- **Non-hiking routes:** `is_nonhiking_route_name` (category `nonhiking-route`)
  — the phrase `<bike|climbing|glacier|evacuation> route` or a dead-way marker
  (`NOT VISIBLE`/`Obliterated`). Deliberately NARROW: most `…Route` names are
  REAL hikes (Zion Narrows, Grand Canyon's Escalante/Royal Arch, Ozark Trail),
  so a blanket `Route` drop is wrong — the ambiguous remainder goes to the
  viewer's review bucket, not an auto-drop.
- **Degenerate-clip gate** (`serve/publish_areas.py`, `_MIN_AREA_MI=0.1`): skip
  an area whose trails only graze its boundary and clip to <0.1 mi total — it
  ships as a broken "1 trail, 0.0 mi" entry. Self-healing (re-publishes if a
  boundary later catches real trail). 97 already-live such areas were swept
  (`31c660c5`); master index keeps their seed rows.
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
`road-track-name` bucket is the false-negative-risk one to CHECK. "Tiny &
isolated (review)" flags KEPT trails <0.15mi that touch no other trail
within ~20m — NOT an auto-drop (blindly dropping isolated tiny trails was
tested and rejected: it would've emptied 6 areas and gutted 30%+ of trails
in 31 more, since plenty of real trails start at a road/parking lot, not
another trail). `make assemble REGION=xx` passes region so the viewer
matches what ships.

**Curation flywheel — trust the tested rule, review after the fact, not
before.** The operating model as of 2026-07-13: `scripts/_seed_constants.py`'s
`is_quality()` + `red_flag()` now auto-exclude at SEED time (mining/hunting/
water-supply red flags, `access`/`ownership=private`) — no more "run an audit,
read a list, manually strip slugs before every publish." The user explicitly
signed off on this trade (session 2026-07-12 night): trust narrow,
real-example-backed exclusion rules completely; stay "somewhat" in the loop
via `scripts/audit-easement-ownership.py` as an after-the-fact report of what
got excluded and why, not a gate to approve beforehand. **The whole point is
reducing manual copy-paste toil while still obsessing over quality** — every
rule in this file earned its way in by catching a REAL discovered bad example
(Bucktown LLC, Party of 5 (MTB)) and was verified against a growing regression
list before being trusted, never speculatively enumerated in advance (that
approach — v1 of the audit script tried to require PROOF of legitimacy —
produced 439 candidates for NY, ~93% false positives; proving something's good
is unbounded, catching a specific bad pattern is not).
`scripts/audit-easement-ownership.py` now supports multi-state / `--all` in
one run (writes ONE combined report, retries a flaky Overpass query 3x before
moving on) — built specifically so this doesn't require 50 individual
one-state investigations. **If a genuinely new red-flag category shows up in
another state** (a logging/ranching/oil-gas easement pattern, something we
haven't seen — GA and VT both came back 0-flagged, which is reassuring but
NOT proof nothing's there, just that nothing matches the categories we've
already found), add it the same way: find a real example, verify it doesn't
false-positive known-good cases, add both the rule AND a test case to
`_seed_constants.py` — never guess at a pattern with no real example behind it.

**Homelab Claude Code — run this flywheel with live tool access, not through
a cloud-sandboxed relay.** The sandboxed Claude session has no Overpass/
GitHub-dispatch egress, so every step (seed, audit, publish, log-pull) had to
round-trip through the user copy-pasting terminal output — that was the real
bottleneck on 2026-07-12 night, not the decision-making. A Claude Code
session run directly on the homelab (`cd ~/south-mountain-explorer && claude`,
auth via `ANTHROPIC_API_KEY` to skip the headless-browser login problem) can
run the full per-state loop unattended: `seed-areas.py --merge <state>` (now
auto-excludes red flags) → push → dispatch `trailforge-publish.yml` dry-run
via `gh` → pull the log directly → sanity-check the verdict (published count
non-trivial, failed-validation ~0, skip rate in a normal range) → dispatch the
real publish or flag an anomaly for the user. Scale to `--all` states with the
same loop. **Known gotcha found 2026-07-12:** `publish_areas.py`'s printed
skip-detail list is capped (`skipped[:40]`) — the REAL total is in the header
line `skipped {len(skipped)} (no trails / no boundary)`, not the visible
detail count. Don't undercount a publish's skip rate by only grep-counting
the individual `no trails touch this area` lines.

**Publish flow.** Three dispatch-only workflows, all **default dry_run=true**
(always dry-run + eyeball, then dry_run=false for the real write):
- `trailforge-publish.yml` — one state.
- `trailforge-publish-batch.yml` — `states: a,b,c`|`all`, serial loop, ONE
  commit at the END (a mid-loop timeout loses the whole run → keep batches
  small, ~5-8 states). Concurrency group `trailforge-publish` serializes
  back-to-back dispatches safely.
- `trailforge-publish-us.yml` (**the whole-US mechanism**) — one dispatch →
  13 PARALLEL region jobs, each publishes its states' geom to an ARTIFACT (no
  commit) → a `merge` fan-in job (`scripts/merge-published-geom.py`) copies all
  geom + refreshes the index counts from the geom → ONE commit + R2 sync.
  Sidesteps the 350-min per-job cap AND commit races. Matrix = all 50 + DC.
  Remember the **dry_run checkbox is checked by default** (uncheck to publish).
The MCP GitHub token can't dispatch workflows (403) — the USER dispatches from
the Actions UI, or homelab (planet-scale fallback). Trailforge paths have NO
CI gate; iOS paths do (`ios-pr-build`). TestFlight is manual-dispatch only.

**Homelab GitHub CLI:** browser device-flow fails headless; use
`export GH_TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' | git
credential fill | sed -n 's/^password=//p')` (reuses the stored push token) or
a PAT. Then pull run summaries with `gh run view <id> -R <repo> --log | grep
-aE "SUMMARY|published:|failed:|!!|assembled [0-9,]+ trails|KEEP +[0-9]"`.

**Recent app UX (verified merged on main as of 2026-07-12 — see the "PR merge
status" lesson below):** tap a trail = highlight only + toggles selection;
selected row's checkmark becomes a **Record** button; three-dots GPX export
is selection-aware. Tabs are Explore · Browse · **Stats** · Settings.
**Out-of-region waitlist** (`RegionSupport` gates on `Locale.current.region`;
`WaitlistCard` on the Explore/Home tab captures country + email to
**PostHog** — no separate backend): has a **beta-tester toggle**
(`beta_interest`) and a **"Look around US & Canada"** button that jumps to
Browse (#352), plus copy tweaks — "You're on the list, thanks!" + a
still-served-while-traveling note (#353). **Browse trail search** results
draw the single trail's linework, not the whole area's (#354).
**Report-a-problem-with-this-trail** form (`ReportTrailView`, from the area
three-dots menu when a trail is selected): reason code + free text →
`trail_reported` PostHog event with trail/area ids — actionable, often
points at an OSM fix (#355). Faint Tonto trail-mesh backdrop is merged but
**not rendering on device** — WIP. **Silhouette cache now revalidates**
against R2 in the background instead of caching terminally (#356) — fixes
regenerated card art never reaching a device that already cached the old
sketch.

**iOS build state (2026-07-14).** A TF build carrying #358 (null-decode /
Otter Creek), #359 (trail-mesh visible + centered), #360 (elevation gain shown
next to distance), and #361 (global trail search) was cut and tested on-device
— all confirmed working. **Merged AFTER that build, so they need the NEXT one:**
#362 (search result scrolls the trail list to the tapped trail) and #363
(search-result trail thumbnails via `trail-shapes.json`). #363 also needs an
R2 sync dispatched to put `trail-shapes.json` on the CDN before it draws
anything. TestFlight is still manual-dispatch only — never re-enable auto.

**DONE — nationwide rollout of the curation fixes (finished 2026-07-14).** The
way-vs-relation seed fix, MTB name filter, and ownership red-flag exclusion
were rolled to ALL 50 states + DC: re-seeded (`seed-areas.py --merge`, all 48
remaining after the NY/GA/VT pilot in one run), dry-run verified clean, then
real-published via `trailforge-publish-us.yml` (commit `236e2ff3`). Plus the
`nonhiking-route` filter and the degenerate-clip gate landed in the same pass.
Coverage went ~5,400 → 9,539 areas with real geom. **Still NOT retroactively
purged: whole bad AREAS** already in the index from the pre-red-flag-gate bulk
seed — `--merge` is additive, never removes existing rows, and the tag-based
`red_flag()` only screens FRESH Overpass candidates. Follow-up (task #27): run
`scripts/audit-easement-ownership.py --all` after the rollout to see what stale
red-flagged areas are still shipping, then remove deliberately (like the 7 NY
orphans). NY/GA/VT area-name scan came back essentially clean.

**Backlog (see the session task list):** (1) **named roads** — the reviewable
`named-road` bucket now collects 407 candidates; the remaining call is your
viewer eyeball (rescue washed-out-roads-still-hiked like `Dosewallips River
Road`) — plus ditches / stock-driveways per the research decision table;
(2) elevation-based difficulty from a global DEM (Copernicus GLO-30 / AWS
terrarium tiles) — see `trailforge/SPEC.md §6e`. Current difficulty is a
weak length bucket. (3) decide whether to whitelist `Powerline-Gypsum` (5.1mi,
White River NF — dropped by the utility filter, may be a real MTB connector).
(4) **Flywheel + cadence** (tasks #19/#20): make `trail_reported` OSM-actionable
(carry a way id → 1-click OSM edit) + a triage script; and a scheduled
`trailforge-refresh.yml` that re-imports states on rotation and posts a diff
digest for review — planned, sandbox-buildable, not started. This is
deliberately v2 — the user wants the seed→curate→publish loop itself running
reliably first (see "ACTIVE" above) before building the user-report side of
the flywheel. (5) **`leisure=park` nationwide gap (task #21, found
2026-07-13):** `seed-areas.py`'s Overpass query has NEVER searched
`leisure=park` — only `boundary=protected_area`/`leisure=nature_reserve` —
so every plain municipal/city/county park is invisible to seeding in EVERY
state. Confirmed via live Overpass: Central Park (NYC) is tagged
`leisure=park` (`owner: NYC Dept of Parks and Recreation`), zero protected-
area/nature-reserve tags, completely unseeded. Bigger scope than tonight's
fixes — `leisure=park` is applied to essentially every park in OSM (Central
Park down to a fenced playground), so a naive query addition would surface
an enormous low-signal candidate volume that `is_quality()`'s existing name
keyword ("Park") would trivially accept. Needs a genuinely new quality
signal — most plausibly a minimum polygon-area/trail-density threshold — not
just a query tweak. Treat as its own dedicated session. (6) **Pre-broad-
launch**: cut a TestFlight build so the report loop is live; then let reports +
re-imports improve data (per the AllTrails/Komoot lesson — nobody 100%-eyeballs;
cadenced batch + curation gate + community feedback is the standard).

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

**Recent main HEAD (as of 2026-07-12):**
- `316b1be8` — trailforge: keep contained thru-hikes (drop only cross-park)
- `3eb03235` — trailforge: parallel whole-US publish (matrix fan-out + fan-in)
- `d8293ec1` — trailforge: prune 6 empty areas from the index
- `540ee322` — Report a problem with this trail (#355)
- `e87919b7` — Waitlist: beta-tester opt-in + "look around" CTA (#352–354)
- `1112b173` / `fe65a233` / `0be36e26` — named-road bucket · access + non-trail
  filters + guard net · review tooling (per-reason buckets + diff + ingest layer)
IN FLIGHT: the whole-US real publish (`trailforge-publish-us.yml`, dry_run
UNCHECKED) — when it lands look for a `publish ENTIRE US` commit. Earlier iOS
baseline: `f899bdac` dropped min iOS target 26 → 18 (Liquid Glass fallback).

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

### 5. Claiming a PR is "merged to main" without checking the API

On 2026-07-11 a session opened #352–355, watched #352 and #355 actually
merge, then wrote "All four PRs (#352–355) are merged to main" into this
file — #353 and #354 were still sitting open, untouched, `merged: false`.
That false claim survived until the user reported a real bug (Browse trail
search drawing the whole area instead of one trail — exactly what the
still-open #354 fixed) and a follow-up session had to `pull_request_read`
every PR number individually to find the two stragglers. **Never write "PR
#N is merged" from memory or from a prior session's notes — call
`pull_request_read` (method: get) and check `merged: true` yourself before
the claim goes in this file or in anything you tell the user.** This is
distinct from lesson #2 (waiting for CI before merging) — this is about
verifying a merge that supposedly *already happened*.

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
