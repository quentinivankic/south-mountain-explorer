# South Mountain Explorer (TrekDex) — Claude Primer

Source of truth for a fresh session. Read it first. It's a concise map, not
a changelog — **verify specific claims against the code before trusting them**,
and see the auto-memory files (`MEMORY.md` index) for deep detail on active
threads (App Store submission, trail/area quality, dedup, parking, coverage gap).
Updated 2026-07-28.

> ### Start here in a NEW session
>
> 1. **`TASKS.md` holds the 13 open tasks** with their full measurement history —
>    national counts, named counter-examples, and approaches already ruled out
>    with evidence. The in-session task list (`TaskList`/`TaskGet`) is
>    session-scoped and does NOT survive; **re-create the tasks with `TaskCreate`
>    from `TASKS.md`**, and write any new decision back into that file.
> 2. **`.claude/hooks/` is ENFORCEMENT, not documentation.** A `UserPromptSubmit`
>    hook re-arms gotcha #14 on every prompt and a `Stop` hook blocks a turn that
>    asserts a counterfactual with neither a verification citation nor a hedge.
>    They fail open and block at most once per turn. Tests:
>    `python3 .claude/hooks/test_verify_counterfactuals.py` (8/8).
> 3. ⚠️ **`main`'s git history only reaches back to 2026-07-08** (root commit
>    `1451d04cf`, 312 commits). Anything merged before that is absent from the
>    commit history while its CONTENT sits in that root tree, so a genuinely
>    merged old PR fails `git merge-base --is-ancestor` and looks unmerged.
>    For anything older, check `refs/pull/<N>/head` and `mergedAt`, not ancestry.

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
  lots as blue "P". Candidates are the area's OWN `parking` merged with the
  **global pool** (`Services/ParkingPoolService.swift`, `cdn.trekdex.app/parking.json`)
  — no area has to "own" a lot. All three display paths (pins, camera frame, row
  banner) go through `ParkingPoolService.merged(with:for:)`; wiring only one of
  them names a lot with no pin under it. See auto-memory `parking-feature.md`.
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

- **Coverage:** ~9,060 areas ship with clean trail geom (the iOS bundle set,
  verified 2026-07-26). The master index has ~29,852 rows; the rest are
  seeded-but-unpublished (incl. all ~375 Canada rows) or non-hikeable.
- **`add-parking.py`** is a post-process that enriches geom with OSM/federal
  parking (see `parking-feature.md`). Publish PRESERVES parking on republish
  (`publish_areas.py` + `merge-published-geom.py` carry it forward).
  `trailforge-parking.yml` is now **per-state matrix** (`max-parallel: 8`) →
  artifacts → one fan-in commit (#455); it was serial-in-one-job, and at ~28 min
  PER STATE a nationwide roll would have meant ~17 dispatches / ~23 h. Takes
  `states: all` (51 US codes; CA-* provinces excluded). **It had NEVER once
  succeeded before #454** — the workflow never installed `shapely`, so every run
  died on the import, lost the boundary containment gate, skipped writes and
  exited 2. AZ's shipped parking came from the homelab, which is why it went
  unnoticed. First use of the new shape should be a single-state dry run.
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
- `trailforge-publish-batch.yml` — `states: a,b,c`|`all`, **still SERIAL in one
  job**, ONE commit at end (keep batches ~5-8 states; a mid-loop timeout loses
  the run). 4 states measured at ~63 min. Not yet fanned out — see below.
- `trailforge-publish-us.yml` — whole-US: **51 parallel PER-STATE jobs**
  (`max-parallel: 16`) → artifacts → `merge-published-geom.py` fan-in → one
  commit + R2 sync. Was 13 region buckets until #453; wall clock tracked the
  slowest BUCKET, so northwest (6 big western states, serial in one job) ran
  2h42m while equal-sized plains ran 17 min. Now bounded by the slowest single
  state (california, ~46 min). **#453 has not been exercised yet — first use
  must be a dry run.**

**Fan-in is resilient (#452):** `merge` runs on `always()`, not `success()`, so
one failing/timed-out state no longer discards every state that succeeded. Safe
because `merge-published-geom.py` is strictly ADDITIVE — it copies only slugs
present in the artifacts and refreshes only those index rows, so a partial
publish is a partial UPDATE, never a truncation. It warns on a partial merge and
fails only on zero artifacts.

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

**Landowner data beats tag inference for PURPOSE questions (2026-07-27, #497).**
`public/areas/nonhiking-trails.json` is a reviewable sidecar of trails an agency
says are not for walking: `{areaId: {trailId: {reason, evidence, share,
terraShare}}}`, one entry per verdict, each carrying the agency asset that
justified it. Built by `scripts/build-nonhiking-list.py` from a cached copy of
`EDW_TrailNFSPublish_01`, applied to shipped geom by
`scripts/sweep-nonhiking-trails.py`, and honoured by `publish_areas.py` so a
republish can't resurrect the trails — the publisher reads the FILE, so it needs
no agency network call. Same reversible shape as `aliases.json`. Both the sweep
and the publisher REFUSE to empty an area from the sidecar: an external dataset
must never be able to remove a park from Browse. **First use dropped only 18
trails / 35.2 mi**, requiring three independent agreements (FS types it SNOW, FS
NAMES it snow, and no TERRA trail shares the tread at ≤0.25) — the ~2,780
suspected road-as-trail population is still UNSOLVED. Put future non-hiking
verdicts here (bridleways, #34) rather than inventing a new mechanism.

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

**DECIDED + SHIPPED: orient by whichever trail end is nearest the
user, always** (`TrailProfile.startIsNearer`) — no distance cutoff. It landed
via **#447**, NOT #445 — cite #447. (#445 was CLOSED 2026-07-26, never merged;
it had drifted hundreds of files behind `main`, so merging it would have reverted
the dedup/alias work and most of `public/areas/geom`. This is the same mis-scoped
merge described in "Things Claude gets wrong" #6.) The "you are
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
  same PR — because parking was AZ-only THEN (106 areas), so it fired for ~1% and
  coupled an elevation feature to the parking rollout. **That condition is GONE:
  parking now covers 6,204 of 9,060 areas (68%, verified 2026-07-26), so the
  trailhead anchor is viable for most trails with a nearest-end fallback for the
  rest. Now the strongest option — see "What would change the decision" below.**
- **Direction of travel** (flip so "ahead" is always right). Dropped with the
  above: it mirrors the chart when you turn round on an out-and-back, and needed
  `@State` + an `onChange` to damp the flapping. Latched nearest-end is stable
  for the whole hike and needs neither.
- **Low end on the left.** Right for most hikes, wrong for every canyon descent
  (Grand Canyon). A guess wearing a rule's clothes.
- **User drops a pin.** Works mechanically (a pin snaps exactly like a GPS fix),
  but asks the user a question they don't know they have — fine as an OVERRIDE,
  never as the default. **The override half of this SHIPPED and is on `main`
  (verified in code 2026-07-26)** — a Flip button on the chart
  (`TrailElevationProfileView.onFlip`) writes a per-trail boolean through
  `Utilities/ProfileDirectionStore.swift` (`StorageKeys.profileDirectionOverrides`),
  read back when the row expands (`TrailListView`, ~line 587). So "add a flip
  override" is DONE, not pending; only the default is still nearest-end.

**What would change the decision — and the FIRST trigger has now fired:** parking
reaching national coverage (68% as of 2026-07-26) makes the trailhead anchor real.
A parking-anchored orientation with a nearest-end fallback is now the strongest
option and should be reconsidered — it was only ever rejected for AZ-only coverage,
which no longer holds. **NOT yet implemented; recorded as an unblocked, open
decision.** The other trigger — complaints that browsed profiles read backwards
— is already covered: the per-trail flip override SHIPS (see the pin bullet
above), so that trigger no longer implies new work, and it is not a reason to
swap the default.

App side: `Utilities/TrailProfile.swift` (snap / elevationFt / oriented), chart
in `Views/Area/TrailElevationProfileView.swift`, expanded into the selected row
in `TrailListView`. Kept separate from `ElevationProfileView`, which charts a
RECORDED hike in metres against real cumulative distance — merging them would
mean one view with two unit systems and two meanings for x.

---

## Active threads (updated 2026-07-28) — see auto-memory for depth

- **Parking** (`parking-feature.md`): OSM containment-gated parking is NATIONAL
  (6,271 of ~9,060 areas). Federal fallback = NPS parking + USFS trailheads (BLM
  DROPPED — its "trailhead" layer is generic area-POI markers, not trailheads).
  Road-proximity gate on federal points. On-selection display + distinct trailhead
  marker shipped. Stale-geom cache bug FIXED (#424).
  **OWNERSHIP IS GONE (#500).** Containment stays a QUALITY filter and stops being
  ownership: `scripts/build-parking-pool.py` emits every qualifying lot once to
  `cdn.trekdex.app/parking.json` (29,196 lots, 0.33 MB gz, built fresh by
  `sync-geom-to-r2`, never committed), and the app merges it with each area's own
  lots. Measured: **6,390 trails (6.9%) gain a lot they didn't have, 1,027 of them
  in areas that ship no parking at all**, across 1,965 areas — Haleakalā
  Wilderness reaching the visitor-centre lot 25 m away that
  `haleakal-national-park-hi` held, Badwater, Henrys Fork, Heaven's Gate. Every
  sampled case was a real trailhead filed under the OVERLAPPING unit.
  **This makes task #38 moot**, not answered: nobody has to judge whether
  Chiricahua Wilderness or Coronado NF owns South Fork Trailhead.
  **Open follow-up is task #44 in `TASKS.md`, fully specced:** the pool is built
  FROM shipped geom, so it still inherits whatever assignment dropped. Emitting it
  from `add-parking.py` after the gates but BEFORE assignment recovers **2,348
  more lots** (USFS discards 87% of its trailheads to ownership, NPS 95%) —
  Peralta, String Lake, Two Medicine Lake, five Yellowstone trailheads. NPS must be
  filtered to trailhead-NAMED only; its `LOTTYPE`/`OPENTOPUBLIC` attributes are
  empty and a distance cut was measured and refuted. Read #44 before touching it.
  A re-measure with the real gates was attempted 2026-07-28 and did NOT finish —
  public Overpass 504'd through three road-gate retries. Nothing was written.
- **Coverage gap — US CLOSED, only Canada remains** (`coverage-gap-missing-areas.md`).
  Verified in data 2026-07-26: **Great Smoky Mtns SHIPS** (`...-nc` 128 trails +
  `...-tn` 131, both with geom on R2) and so do the other US multi-state areas —
  the boundary-by-`osm_rel`-id fix (#430/#432) landed and the states were
  re-published. **Do NOT claim marquee US areas are missing — verify the index/geom
  first.** GSMNP ships as TWO state-clipped rows (same rel `2131838`) — an artifact,
  not a gap. **Still missing: Canada** — 375 `-ca-*` rows sit in `index.json` with
  trail counts (Banff 421, Jasper 199) but have ZERO published geom, and there's no
  Canada publish path yet (`trailforge-publish-us.yml` is 51 US codes). Shipping
  Canada is a real pipeline lift, not a re-run.
- **System-1 purged** (#428): 7,304 pre-trailforge `cached_at` geom deleted
  (never shipped; caused "has trails but doesn't ship" confusion). Nothing
  regenerates them unless the old `build-trail-index.yml` runs — don't.
- **Nested/duplicate areas.** Identical twins + near-coextensive nests (ratio ≥0.75)
  SHIPPED via alias sidecar (B+D, #475 — 186 twins + 19 nests hidden; see auto-memory
  `nested-area-dedup`). REMAINING (task #37): sibling groupings B+D left — "Saguaro"
  still returns 4 (park + 2 districts at 0.44/0.58 + wilderness 0.66, all under the
  0.75 cut) and "Au Sable" 4 distinct units. Fix = search-GROUP siblings under one
  iconic parent, NOT delete (they're real distinct polygons).
- **Trail/area quality audit** (2026-07-26, tasks #21,#30-36; auto-memory
  `area-quality-grayling-audit`): investigated + quantified. TIGER roads-as-trails
  (3,561 flagged, auto-drop 256 + review), 0-length stubs (251, lossless), thru-route
  teleport/fragmentation (996 gaps ≥2mi; split into road-bounded sections),
  fragmentation quality score, route-source decision. Re-measure any time with
  `python3 scripts/audit-trail-quality.py` (reproduces the baselines).

---

## Build / release / CI

- **TestFlight:** public link live; uploads via `ios-testflight.yml`
  (workflow_dispatch). Keep the WORKFLOW dispatch-only — NEVER re-enable
  auto-on-push (#160). But **Claude MAY dispatch it** (user OK'd auto-dispatch
  2026-07-26): sensibly — after merging app-CODE changes on green CI, coalescing
  back-to-back merges; skip for data-only (ships via R2) or docs-only changes.
- **PR CI:** `ios-pr-build.yml` (simulator compile + unit tests) runs on
  `ios/**`, `public/areas/index.json`, and the workflow file. ~8-13 min.
  **Wait for green before merging** these. Trailforge/scripts paths have NO CI
  gate — dry-run + eyeball is the gate there.
- **NO workflow runs the Python tests** (verified 2026-07-28: nothing in
  `.github/workflows/` invokes pytest or any `test_*.py`). There are 9 such files
  under `scripts/` and `trailforge/`; each runs standalone and passes —
  `python3 scripts/test_add_parking.py` gives `30 passed`. Adding a job is cheap,
  but note it was MEASURED not to catch the #425 class of bug: a test that stubs
  the seam it integrates against passes regardless. See gotcha #14.
- **Branches:** `delete_branch_on_merge` is ON as of 2026-07-28, and the backlog
  was pruned 340 → 23. Deleted branches with a PR stay recoverable forever via
  `git fetch origin refs/pull/<N>/head`.
- **Merge authority:** the user authorized merging PRs autonomously once CI is
  green (squash + delete branch). Still eyeball a dry-run for no-CI-gate PRs.
  Claude MAY also dispatch TestFlight after app-code merges (user OK'd 2026-07-26)
  — see the TestFlight note above for when.
- **R2 sync:** `sync-geom-to-r2.yml` auto-runs on push to `public/areas/geom/**`
  or `silhouettes/**` → R2 bucket `trekdex-areas` (`cdn.trekdex.app`). Also
  rebuilds `trail-search.json` + `trail-shapes.json`. Does NOT cover `index.json`.

---

## Things Claude gets wrong (read before proposing)

1. **v1.0 SUBMITTED to the App Store 2026-07-26** (iPhone-only), awaiting review —
   so App Store compliance is NO LONGER moot (privacy manifest / metadata / IAP /
   account deletion all apply and were satisfied; see auto-memory
   `app-store-submission`). Public TestFlight is also live for testers.
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
6. **Branch from `origin/main`, never from whatever HEAD happens to be**, and
   check `git diff --stat origin/main...HEAD` BEFORE opening the PR. `git
   checkout -b fix/foo` inherits every unmerged commit on the current branch —
   `git add <one file>` only controls the NEW change, so already-committed work
   rides along invisibly. Burned 2026-07-19: a session that started on
   `feature/trail-elevation-profile` opened what looked like a one-file
   trailforge fix; #447 merged **9 files**, silently landing the whole elevation
   -profile feature (TrailProfile.swift, TrailElevationProfileView.swift,
   TrailListView.swift, elevation.py, +59 lines of this file) under a commit
   titled "trailforge: fetch a boundary…". Use
   `git checkout -b <name> origin/main`. The tell was there and was misread:
   `ios-pr-build` fired on a supposedly trailforge-only PR, and that got
   explained away as the trigger paths being wrong. **They are not wrong** —
   `ios-pr-build.yml` triggers on exactly `ios/**`, `public/areas/index.json`,
   and its own file. CI firing unexpectedly means the DIFF is wrong, not the
   workflow.
7. **A green run proves the job EXITED 0, not that it DID anything. Verify the
   OUTPUT.** Three separate bugs on 2026-07-19 all had this shape — a step
   reporting success while silently doing nothing:
   - `trailforge-parking.yml` never installed `shapely`, so every run in the
     workflow's ENTIRE history died on the import, lost the containment gate and
     skipped its writes. AZ's parking had come from the homelab, so nobody
     noticed CI had never once worked.
   - That same `ImportError` was retried through the full backoff ladder and
     then reported as *"rerun when Overpass recovers"* — a local missing module
     blamed on a remote service, which sent me hunting a nonexistent outage.
   - `trailforge-publish-batch.yml` computed `$ELEV` and never passed it to
     `publish_areas.py`. The log printed the flag being set. The republish then
     STRIPPED elevation from 18,641 trails across CA/MT/TN/VA, downgrading
     gain-based difficulty to length-based, with a green check throughout.

   So after any publish/enrich run, **count the thing it was supposed to
   produce** — `gainFt`, `profileFt`, `parking`, trail counts — in the actual
   geom, and compare against before. `jq`/a 5-line Python scan is enough. Never
   report a data job as done on the strength of a green check.
8. **Never tell the user to test a feature without verifying its DATA exists.**
   Burned 2026-07-19: the elevation-profile chart shipped in a TestFlight build
   and was written up with confident test steps, while `profileFt` was absent
   from all 91,922 trails — the publish that would have baked it ran BEFORE the
   baking code merged. The app half shipped and the data half never ran. This is
   the two-deploy-path hazard documented in `docs/adr/0001` and it was walked
   into hours later the same day. **Feature = code + data. Check both.**
9. **An unexpected signal is evidence, not noise — investigate before
   explaining it away.** Every mistake above was preceded by a correct signal
   that got rationalised: CI firing on a "trailforge-only" PR, a parking dry-run
   exiting 2 while printing a healthy report. When something contradicts the
   model, the model is usually wrong. Check before writing a "correction" — a
   false correction into this file is worse than the original error.
10. **OSM tags describe a way's FORM. Curation questions are usually about its
    PURPOSE. Those are different questions, and no predicate over the first
    answers the second.** Learned the long way on 2026-07-27 (task #21, PR #497).
    `highway=track` says "drivable land-access road"; `surface`, `tracktype`,
    `tiger:cfcc` all describe the tread. None of them says whether a person
    hikes it. Four separate approaches were built and measured before this
    landed: TIGER `cfcc` gates, `foot` presence, Forest-Service name codes, and
    an inverted "prove it's a hike" rule. All leaked, because the answer is not
    in the tags. **When you need purpose, ask the landowner's own inventory** —
    `EDW_TrailNFSPublish_01` carries `trail_type` (TERRA/SNOW/WATER) per trail,
    on the same ArcGIS host `add-parking.py` already uses.
11. **A blank attribute is NOT a negative.** Twice in one day, reading "no data"
    as "no" would have deleted real trails: OSM absence-of-evidence, then the
    Forest Service's own `hiker_pedestrian_managed`, whose blank on 2,346
    matches covers **Eagle National Recreation Trail** (23.3 mi) and General
    Crook Trail #140. (Those fields also hold seasonal DATE RANGES like
    `05/15-09/15`, so they say WHEN, not WHETHER.) Same shape as the fail-open
    parking bugs in #491: absent ≠ empty ≠ false.
12. **The user's eyes invalidate a proposed signal faster than any measurement
    you can run.** Asked to spot-check ~10 trails from each of four evidence
    buckets, the user found real trails AND truck roads in every single bucket —
    killing four candidate rules in one message, after a day of my measurements
    had failed to. **When proposing a curation signal, produce a stratified
    sample with map links and ask for eyeballs BEFORE building the rule.**
13. **Every threshold I proposed first was wrong, and reading the dry-run ROWS
    (not its summary) caught it each time.** Three in one day: a 2 km
    parking distance cap that deleted Springer Mountain Trailhead; name-only pin
    dedup that collapsed Saguaro's 19 distinct "Parking Lot" lots to one;
    `trail_type=SNOW` alone, which flagged Howlock Mountain and Thielsen Creek —
    real hikes that are groomed in winter. The counts looked fine in all three.
    **Print named examples and read them, every time.**
14. **A counterfactual is a claim, not a comment. Run it before you write it.**
    The user's standing complaint, 2026-07-28: *"I am so tired of you saying to
    do X with conviction, I ask you to test it, then magically X isn't the right
    way."* The defect is not being wrong — it is stating an untested inference in
    the same voice as a measured fact, which leaves the user to challenge every
    sentence to find out which ones hold. Any claim shaped like **"X would have
    caught Y"**, **"X is the bottleneck"**, or **"this is the right approach"**
    is testable, usually in under two minutes. Test it and cite the command; if
    it truly cannot be tested cheaply, write "I think" and name what would settle
    it. **Burned the same day:** asserted that a CI job running the repo's
    existing Python tests would have caught #425's broken `road_gate_assigned`.
    Grafting the function onto current `main` and running the suite gave
    **31 passed** — CI sails past it, because #425's own test stubs `road_gate`,
    the exact seam it integrates against. The corollary DID survive testing and
    is the useful rule: **stub the network boundary, not the function you are
    integrating against.** Swapping the stub to `fetch_roads_near` fails
    instantly, while the gate still logs `kept 1/2` on its way to returning `{}`.

---

## Where to look
- **Open tasks → `TASKS.md`** (13, with full measurement history; re-create them
  with `TaskCreate` at session start — the live list does not persist)
- **Assertion guard → `.claude/hooks/`** + `.claude/settings.json` (enforces #14)
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

## How to work here (agreed 2026-07-29)

The user's standing complaint after a stretch where six PRs landed invisibly:
*"you run forever and I have no idea what you're doing, whether it's the right
move or not, or if you're just stuck."* The work was fine; the silence was the
defect. So:

1. **Narrate every step** as it happens, not a summary at the end.
2. **Never estimate a duration.** *"You're historically bad at estimating time
   needed for something"* — and it is true: "10-20 min" for a road gate that never
   finished, "~4 h" for a national roll. **Measure ONE unit and report the
   observed rate** (one state took 34 s, so 51 is about half an hour), or say it
   is unknown and name the first checkpoint.
3. **Check in every 15 minutes**, even mid-task.
4. **Anything over ~20 minutes: state the cost and WAIT** for a go/skip.
5. **Do not chain scope.** Land the smallest useful increment and report. Doing a
   backfill, then a sweep, then a roll in one turn is how a session goes dark.
6. **Never poll a log in a loop** — it burns wall clock and shows the user
   nothing. Background the job and wait on its completion.
7. **A 20-minute geometry job is a BUG, not a big dataset.** The parking/boundary
   data is ~1.4M points and ~9k polygons — seconds of work. Every measurement
   that took 10-25 min did so because it was written as O(points × areas × ring-
   segments) pure-Python loops instead of a spatial index. Use
   `scripts/_geo_index.py` (shapely `STRtree`); compute the invariant once and
   derive thresholds from it. Full detail: auto-memory `always-spatial-index.md`.

Full detail in auto-memory `working-visibility-contract.md`.

## When in doubt
Already authorized (don't ask): merging PRs on green CI, and dispatching
TestFlight (see the TestFlight note above for when). Still ask before: touching
`Documents/hike-history.json` or `activity-log.json` on the device, reverting on
main, or force-pushing a shared branch. For outward-facing or hard-to-reverse
actions, confirm first unless already authorized.
