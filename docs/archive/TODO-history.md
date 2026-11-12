# TODO history (archived)

Moved word for word from `TODO.md` on 2026-11-12 (tech-debt-speed D8). Nothing
here is current. Check the code and `TASKS.md` before acting on any of it. The
wording and order are unchanged; a plain line before each block names where it
came from.

Moved from TODO.md lines 157 to 453:

## Shipped 2026-07-20/26 (dedup, App Store submission, quality audit)

- **Duplicate / nested areas — build B+D SHIPPED (#475), confirmed on device.**
  205 areas hidden from Browse (186 identical twins + 19 near-coextensive nests
  at trail-set ratio ≥0.75), 9 canonical "traps" deliberately kept as two entries
  (Glacier NP ⊂ Waterton-Glacier would otherwise hide Glacier NP), provably
  lossless — 0 orphaned trails of 75,106. Mechanism is a reversible
  `public/areas/aliases.json` sidecar, never a delete, because `index.json` is a
  POSITIONAL array shared with old app builds. Rationale in
  `docs/adr/0002-areas-overlap-trails-are-identity.md`. The framing that unlocked
  it: the duplication is at the TRAIL level, so the fix is trail identity, not
  area deletion.
  - **Completion crosses twins by geometry fingerprint** (#471, extended to the
    home-card count and the map's cyan lines in #474) and **coverage + the
    walked-here halo resolve from a hidden twin onto its canonical** (#477).
  - STILL OPEN as task #37: sibling GROUPING. "Saguaro" still returns 4 and
    "Au Sable" 4 — real distinct polygons below the 0.75 cut, so the fix is to
    group them under one iconic parent in search, NOT to delete any.
- **v1.0 submitted to the App Store** (2026-07-26, iPhone-only via #478).
- **Parking rolled nationwide** — 6,204 of 9,060 areas, 39,082 lots.
  `trailforge-parking.yml` became a per-state matrix (#455) after the serial
  single-job shape proved unusable at ~28 min per state.
- **US coverage gap CLOSED.** Great Smoky ships as two state-clipped rows
  (`-nc` 128 trails, `-tn` 131, same OSM rel `2131838` — an artifact of the state
  clip, not a gap), and Death Valley (135 trails) and the other multi-state areas
  ship too. Only Canada is left.
- **Silhouette colour fixes** (#476) — moderate reads orange, overlaps no longer
  blend to gold.
- **Trail-quality audit tooling** (#481) — `scripts/audit-trail-quality.py`
  reproduces the #30 / #31 / #35 baselines straight from shipped geom, so every
  number in those tasks is re-checkable instead of remembered.

## HISTORICAL — in flight as of ~2026-07-15 (stale, kept for the lessons)

Snapshot of the active threads this session — see the PRs for detail.

- **Trail-data pipeline (Trekdex trail/area tiles).** Merged **#254** —
  scaffolds `data-pipeline/` per `TRAIL_DATA_PIPELINE_SPEC.md` and starts
  the §10 **New Zealand** pilot: fail-closed licensing gate + registry,
  pure-Python OSM stager, shapely conflation, thin Bucket-B flag emitter
  (no baked score), on-device scoring reference + weights, attribution
  generator, post-build inclusion guard, tippecanoe→`.pmtiles`, a
  Cloudflare Worker PMTiles range handler for R2, **58 unit tests**, and
  the dispatch-only **`build-region-tiles`** workflow (now on `main`).
  - NEXT (you): Actions → **Build Region Tiles** → `region=new-zealand`,
    `publish=false` — proves the geo steps + drops the `.pmtiles` as an
    artifact (no secrets). First run VERIFYs the DOC ArcGIS endpoints.
  - THEN: add `R2_*` secrets → re-run `publish=true` to land NZ tiles on
    the `trekdex-areas-dev` bucket. (I can't dispatch — `actions:write` 403.)
  - IN PROGRESS: dev-only iOS **Trail Confidence Lab** built (Settings →
    Developer, DEBUG-only) — `TrailScoring.swift` ports
    `scoring_reference.py` (conformance-tested), with live weight/base/band
    sliders re-scoring a sample trail set. Next: feed it real pmtiles
    feature props once the NZ build lands (swap the sample set), then
    validate on-device point-in-polygon area attribution against the
    DOC/LINZ polygons, then generalize to the rest of Wave 1.
- **Screenshot polish (PR #255, open).** Stats "Hikes per Month" now
  varies 1–4/month (no empty months); shot 3 reframed zoomed on the live
  recording with the blue user dot aligned to the recording position;
  shot 5 satellite tile-band fixed (dwell 5s→15s). Plus a small prod
  tweak: opening an area mid-hike zooms to your position
  (`TrailMapView.centerOnActiveRecording`).
  - NEXT: on `ios-pr-build` green, dispatch **`ios-screenshots` against
    branch `claude/screenshot-polish-stats-shot3-shot5`** (the simulated-
    location coord lives in that branch's workflow), verify the blue dot +
    no empty month, then merge #255. Those PNGs are the App Store 6.9" set.
- **App Store Connect account.** **Decided 2026-07-15: ship v1 under the
  existing INDIVIDUAL account** — no D-U-N-S / Org enrollment on the critical
  path. (The earlier "Individual→Organization conversion submitted" note was
  inaccurate — it was never started.) Org conversion is deferred to pursue
  later in parallel.
  - Tradeoff accepted: App Store **seller name = developer's personal legal
    name** for v1.
  - **Payment card on the Apple account — UPDATED 2026-07-19 (user).** Keep it
    valid so the Individual membership doesn't lapse.
  - **App HAS IAP — a three-tier TIP JAR, fully built in code** (inactive until
    App Store publish; the StoreKit products go live at store submission). So the
    **Paid Applications Agreement + tax/banking ARE required** before submitting
    (corrects the earlier "free, no IAP" note).
- **Stale branch cleanup.** ~182 old `claude/*` branches. Can't delete
  from the agent environment (the git proxy silently drops ref deletions
  and there's no delete-branch API tool). Prune locally — keep `main` +
  the open-PR branches, `git push origin --delete` the rest — or turn on
  Settings → General → **"Automatically delete head branches"** so future
  branches self-clean on merge.

## HISTORICAL — in flight 2026-07-19 (day 2: rel-id backfill, parking bug, elevation profiles)

_Stale: the republish landed, Death Valley ships (135 trails), #443 merged, #444
is superseded by #487, and #445's work landed via #447. Kept for the mechanisms._

- **Whole-US republish #2 — RUNNING** (`dry_run=false`, run 29690745615). Carries
  the 525 backfilled rel ids. Dry-run first: green, 13/13 regions, 65 min, no
  hang (#441's bound held under the heavier fetch load).
  - Dry-run CONFIRMED publishing: `great-smoky-mountains-national-park-tn` 131
    trails, `inyo-national-forest-ca` 319, `pisgah-national-forest-nc` 404,
    `george-washington-national-forest-va` 401,
    `chattahoochee-oconee-national-forest-ga` 295, `waterton-glacier` 158.
  - ⚠️ **Death Valley MISSED** — and it is NOT a data problem. rel `174732`
    fetches clean (138 members → 2 rings, 1.395 deg²). Rel-id rescue rates run
    ~80% (4/4, 11/12, 10/13, 6/9, 3/5, 6/8) with failures scattered at random =
    TRANSIENT Overpass pressure from 13 parallel regions. Self-healing by design
    (see `_fetch_boundary_by_rel`'s own comment). **NEXT: single-state re-publish
    for the stragglers**, where Overpass isn't contended.
- **PR #443 MERGED** (`87ef5833`) — `osm_relation_id` backfilled for **525/583**
  bare-seed areas via new `scripts/backfill-rel-ids.py` (batched Overpass name
  queries, matched by `name.casefold()` + nearest stored centre; idempotent, so
  two runs union to rescue a fetch-failed batch). The 58 left are tiny 1–2-trail
  preserves with no OSM boundary relation — accept. Verified: 0 counts clobbered,
  0 identity drift, all rel ids int, row count unchanged (29,852).
- **PR #444 OPEN — parking wilderness fix.** `assign_federal` tested "contained
  by a NON-blank area" BEFORE the edge-buffer check, so a wilderness trailhead
  (which sits just OUTSIDE the wilderness, on a road still INSIDE the
  surrounding NF) was dropped as "OSM covers it" and the wilderness stayed blank
  forever. **55 of AZ's 58 blank areas.** `_FED_EDGE_BUFFER_M` existed for
  exactly this and was unreachable. 21 tests pass, incl. a guardrail that #423's
  anti-bleed still drops a point with no blank neighbour in range.
  - Also adds **`trailforge-parking-us.yml`** — 16-region parallel fan-out,
    balanced by AREA COUNT (max 894, min 497; New England alone is 2,163). The
    existing `trailforge-parking.yml` loops states serially in ONE 90-min job,
    which cannot fit 8,809 areas / 50 states.
  - **BLOCKED**: needs a live `--state az --dry-run` before merge (no CI gate on
    scripts/). Held off while the publish contends for Overpass.
  - **Coronado NF was NOT this bug** — purged in #428, recreated by the whole-US
    publish AFTER the AZ parking pass, so parking never ran on it. Replaying the
    real gate against it yields **112 lots**. Only 3 of the 58 are this kind.
- **PR #445 OPEN — trail elevation profiles.** `serve/elevation.py` already
  computed the full DEM series and discarded it; now emits `profileFt` (feet,
  evenly spaced by distance, ~8/mile, floor 8, cap 64). **+7 MB on 430 MB
  (+1.7%)** measured over an 800-area sample. One DEM pass feeds gain + profile.
  App: `TrailProfile` (snap → fraction, interpolate, orient) + 
  `TrailElevationProfileView`, expanded into the selected row in `TrailListView`.
  iOS CI GREEN. **Direction decision + all rejected options are in CLAUDE.md.**
  - NEXT (task #11): swap to **nearest-end-always** orientation, revert the
    parking anchor.
  - NEXT (task #12): profiles only exist after a republish WITH this merged —
    the 2026-07-19 run predates it. Batch that republish; then a TF build, since
    the chart is app code.
- **No TF build is worth dispatching right now** — zero Swift changes on `main`
  since the shipped build `caf26794`; the only `ios/` churn is the bundled
  `areas-index.json` (offline fallback only; live index comes from R2).

## HISTORICAL — in flight 2026-07-18/19 (parking + coverage, big session)

_Stale: that whole-US republish landed and the US coverage gap is CLOSED._

- **Whole-US cross-state coverage re-publish — RUNNING.** `trailforge-publish-us.yml`
  (real, `dry_run=false`) re-publishing all US areas WITH the new multi-state
  boundary fix → ships the ~450 areas dropped as "no boundary in PBF" (Great
  Smoky NC side, giant National Forests, wildernesses) + refreshes existing geom
  + bakes elevation. Fans in to one commit + R2 sync. Reaches OLD apps via the
  R2-served index (no build needed). Watcher active.
  - NEXT after it lands: verify no regressions (golden areas, counts, parking
    preserved); then parking-nationwide OR the no-rel-id stragglers (below).

## Shipped 2026-07-18/19 (trailhead parking + the coverage gap) — see CLAUDE.md + auto-memory

- **Trailhead PARKING feature.** OSM `amenity=parking` enriched onto geom via
  `scripts/add-parking.py` (per-state Overpass, boundary point-in-polygon
  CONTAINMENT gate). AZ live on R2. Deep detail in auto-memory `parking-feature.md`.
  Sub-decisions: **federal fallback — BLM DROPPED** (generic area-POI markers,
  not trailheads; verified bad on Kanab + Grand Canyon-Parashant, #427),
  **NPS+USFS kept**. **Road-proximity gate** (#423). ⚠️ The assign-then-gate
  efficiency change (**#425**) was never merged and is now **CLOSED**
  (2026-07-28) — `main` still road-gates BEFORE assigning, and there is no
  `scripts/roll-parking.sh`. Do not cite it as shipped; see the closure note in
  "In flight" for why, and for the one piece worth salvaging.
  **iOS on-selection display** (#429 — parking hidden while
  browsing; tap a trail → ≤3 nearest + zoom frame) + **distinct green trailhead
  marker + attribution** (#421). **Publish PRESERVES parking on republish** (#426).
- **⚠️ Stale-geom cache bug FIXED (#424)** — root cause of recurring "ghost pin"
  reports (3 cache layers: HTTP `max-age`, `URLCache` not cleared by Refresh, 24h
  disk staleness). Now revalidate + URLCache clear + 5-min staleness.
- **System-1 geom PURGED (#428)** — 7,304 `cached_at` files + 601 silhouettes
  deleted; closes/supersedes task #38.
- **⭐ Coverage gap found + US fix shipped** — 810 areas (Great Smoky, Banff,
  big NFs) never shipped: Canada never published (360, DEFERRED), and US
  multi-state boundaries clipped at the state line (450). Fix
  (#430/#432/#435/#436): `publish_areas.py::_fetch_boundary_by_rel` fetches a
  multi-state boundary by `osm_rel` id via Overpass when PBF assembly fails.
  Shipped NC (#434) + MT (#437), then the whole-US run (above). Detail in
  `coverage-gap-missing-areas.md`.
- **CLAUDE.md condensed** 728→230 (#433) + corrected the false "index not
  R2-served" claim (`AreaIndexService` exists and works).

### Open follow-ups from this session
- [x] **No-rel-id stragglers** — DONE 2026-07-19 (#443). A plain re-seed does NOT
  work (`seed-areas.py --merge` is slug-keyed APPEND-ONLY and never updates an
  existing row; the osm_id goes to a CI-only cache). Real fix was a targeted
  backfill: `scripts/backfill-rel-ids.py`, 525/583 filled.
- [ ] **Parking nationwide** — expand `add-parking.py` AZ → all states (safe now:
  publish preserves it); validate USFS on first states.
- [ ] **Canada** (360) — DEFERRED per user 2026-07-19.

## Shipped 2026-07-15 (multi-area completion + CI workflows + cleanup) — see CLAUDE.md

- **Multi-area completion for trail + roam hikes** (#372, + fetch-storm cap
  #373). A trail/roam recording now credits every NEIGHBOR area whose trails its
  GPS path actually crossed — the app half of "a cross-park trail lives in one
  home area, credited from anywhere." Detect-at-stop; touch-gate + bbox-entry
  gate + nearest-16 load cap (dense-metro guard). `SavedRecording` now persists
  `mode` (`isWalk = mode == .walk`) via a never-throw decode so a multi-area
  HIKE isn't mislabeled a walk. **Needs an on-device cross-park hike to verify.**
- **Nationwide DEM elevation difficulty** — built `trailforge-elevation-us.yml`
  (#368, 13-region fan-out, dry-run default), dry-ran + verified gains (Boundary
  Peak 4,743 ft ✓), then real-ran: gain-based difficulty now ships for all 50 +
  DC (was AZ-only). Still a post-process (fold-into-publish remains a follow-up).
- **Red-flag audit, homelab-free** (#365 `trailforge-audit.yml`; task #27) —
  runs `audit-easement-ownership.py` on CI. 121 flagged → 4 shipping → **removed
  2** (Elk Forest MD hunting area, Newark Watershed NJ, #367); kept Mt Tam +
  Sebago (legit public watershed, `red_flag` false positives).
- **Widen `red_flag` water-operator whitelist** (#370, task #35) — MMWD +
  Portland Water District; first regression tests (`scripts/test_red_flag.py`).
- **Auto-dispatch R2 sync** (#369, task #29) — publish-us + elevation workflows
  now dispatch `sync-geom-to-r2` (GITHUB_TOKEN pushes can't trigger downstream).
- **Removed dead System-2 `build-region-tiles.yml`** (#366) — pmtiles pipeline
  fully superseded by trailforge; nothing consumes it (kept `data-pipeline/`).
- **Purged 1,672 orphaned System-1 geom/silhouette files** (#371, task #24 repo
  side) — stale `cached_at` files not in any index row; stops the R2 re-upload
  loop. R2-side sweep still pending (task #36).
- **Map centers on the selected trail** (#364) — open from a search result →
  frame that trail; tap empty map to deselect → back to whole-area (browsing
  only, not while recording/following).

## Shipped 2026-07-13/14 (curation + difficulty + search) — see CLAUDE.md

- **Otter Creek / null-decode root-cause fix** (#357 cache-bypass, #358
  null-tolerant `JSONValue`). Areas silently failed to appear because one
  `null` in the index array failed the whole-array decode for every user.
- **Whole-US re-seed + re-publish** with the curation suite (all 50+DC,
  `236e2ff3`); coverage ~5,400 → 9,539 areas with real geom.
- **New curation:** `is_nonhiking_route_name`, `fourwheeler`, degenerate-clip
  gate (`_MIN_AREA_MI`) — 97 broken "0.0 mi" areas swept.
- **DEM elevation difficulty** (`serve/elevation.py` + `add-elevation.py`) —
  AZ baked in, direction-invariant, calibrated 99.4% vs Humphreys. gain shown
  in-app (#360).
- **Global trail search** (#361) + **scroll-to-result** (#362) + **search
  thumbnails** (#363, `trail-shapes.json`).
- **Trail-mesh backdrop fix** (#359).

### Open follow-ups (updated 2026-07-15)
- [x] **Roll DEM difficulty to the other 49 states** — DONE via the new
  `trailforge-elevation-us.yml` (#368); real gain-based difficulty ships for all
  50 + DC.
- [x] **Fold DEM sampling into the publish pipeline** — DONE. `publish_areas.py`
  now takes `--elevation` (default ON in all 3 publish workflows) and samples
  gain + gain-aware difficulty inline via the shared `elevation.process_area`,
  so a republish keeps it (no more revert to length-only). Graceful fallback if
  DEM/Pillow is unavailable (warns once → length-based); skipped on `--dry-run`.
  `add-elevation.py` stays as a standalone re-sample tool.
- [x] **Audit stale red-flagged AREAS** (task #27) — DONE. `trailforge-audit.yml`
  (#365) ran; 121 flagged, 4 still shipping, **removed 2** (#367).
- [x] **`trailforge-publish-us.yml` R2 auto-dispatch** (task #29) — DONE (#369);
  publish-us + elevation now `gh workflow run sync-geom-to-r2`.
- [x] **Rail-line name curation** (task #30) — RESOLVED: `fourwheeler` shipped;
  trolley/traction/railway names are real rail-trail footpaths → won't filter.
- [x] **Finish #24 R2-side** (task **#36**) — DONE (#375). Generalized
  `cleanup-r2-orphans.py` off the Europe-only allowlist: deletes any R2
  geom/silhouette whose slug isn't in the current `index.json`, with safety
  belts (≥1000-id index floor, protected root files, >60% orphan-fraction
  abort). Repo purge was #371. **Next: dispatch the workflow (dry-run → apply)
  to actually purge R2.**
- [x] **Verify + close degenerate-clip sweep** (task #31) — DONE. Scanned all
  16,164 shipped geom files: **0** ship with ≥1 trail but <0.1 mi total. The
  whole-US republish (`236e2ff3`) ran with the `_MIN_AREA_MI=0.1` gate active
  and swept every ghost.
- [x] **Grade-aware difficulty floor** (task **#39**) — DONE (#377). The NPS
  rating `sqrt(2·gain·mi)` scales with distance, so it under-rated short brutal
  climbs (Acadia's Precipice, 966 ft in 0.67 mi, shipped **Easy**). Added a
  per-mile grade floor (≥1,500 ft/mi → Hard, ≥1,000 → Moderate; only ever
  raises). `scripts/recompute-difficulty.py` re-labels from the baked `gainFt`
  (no DEM re-sample): **1,377 trails relabelled (1.7%), all upward**. Found in a
  data-quality audit; concentrated in Adirondack High Peaks / Alpine Lakes /
  Acadia / Okanogan-Wenatchee.
- [x] **System-1 (cached_at) area hygiene** (task #38) — DONE 2026-07-18 (#428):
  purged all 7,304 `cached_at` geom + 601 silhouettes (guarded; 8,860 clean geom
  untouched; reversible via git). R2-side handled by task #36 sweep. Orig note:
  a QA audit found
  **7,304 shipped geom files still carry `cached_at`** (System-1 legacy), for
  areas trailforge never re-published (no boundary in the state extract —
  cross-state parks like Allegheny NF, Absaroka-Beartooth). They hold ~3,200
  trails the name filters would drop (bare forest-road numbers, ATV/snowmobile).
  **NOT a live bug: the iOS bundle, global search, silhouettes, and shapes all
  EXCLUDE `cached_at` areas** (bundle = exactly the 8,860 clean areas), so none
  of this junk is user-visible — it's dead repo/R2 storage. `sweep-geom-names.py`
  has an `--include-cached` capability drafted but NOT shipped (running it churns
  ~3,600 invisible files for no app-facing gain). Real fix = get these cross-state
  areas re-published by trailforge (a boundary/extract issue), or purge them as
  superseded. Decide before spending churn.
- [ ] **Reverse-profile / "descends first" signal** (task #32) — deferred; needs
  trailhead orientation.
- [ ] **Nested/duplicate areas** (task #37) — a trail appears in both a park and
  its nested wilderness. STARK EXAMPLE (2026-07-18): "Saguaro" returns 4
  overlapping areas (park + 2 districts that SUM to the park + the wilderness).
  Investigated a dedup; **deferred, keep both for now**
  (cosmetic since #372 made them completable). Real fix belongs in the pipeline.

Moved from TODO.md lines 627 to 629, section "Backlog, UX / polish" (done items):

- [x] **Trailheads & parking pins** on the map — SHIPPED 2026-07-18 (AZ). OSM
  containment-gated parking + federal (NPS/USFS) fallback; show only for the
  selected trail (≤3 nearest). See `parking-feature.md`. Remaining: roll beyond AZ.

Moved from TODO.md lines 643 to 649, section "Backlog, tech debt / ops" (done items):

- [x] **R2 NA orphan purge.** DONE 2026-07-15 (#375, task #36).
  `cleanup-r2-orphans.py` generalized off the Europe-only allowlist to
  "anything not in the current index," with safety belts (≥1000-id
  floor, protected root files, >60%-orphan abort). Dry-run flagged 1,683
  orphans (6.1%: dropped red-flag areas, slug renames, below-quality CA
  areas); apply run deleted all 1,683. The 375 curated CA areas the app
  ships were untouched.

Moved from TODO.md lines 656 to 662, section "Backlog, tech debt / ops" (done items):

- [x] **Global trail-name search index.** DONE 2026-07-14 (#361 +
  `build-trail-search-index.py`). Compact `[name,areaId,trailId,mi,
  difficulty]` index served from R2 as `trail-search.json` (~1.3 MB gz),
  loaded by `TrailSearchService` — trail search is now nationwide.
  Follow-on shipped: **search-result trail thumbnails** (#363) via a
  separate background-loaded `trail-shapes.json` (~3 MB gz, ~11-pt
  Douglas-Peucker shapes).

Moved from TODO.md lines 679 to 681, section "Waiting on you (not code)" (done items):

- [x] Enter the App Privacy nutrition label in App Store Connect — DONE
  2026-07-26 (Email, Other User Content, Product Interaction, Crash Data; all
  not-linked / not-tracking; Location NOT collected).

Moved from TODO.md lines 696 to 697, section "Waiting on you (not code)" (done items):

- [x] Dispatch `ios-screenshots` for the final capture set, then upload the
  PNGs to ASC — DONE for the 1.0 submission (5 shots at 1320×2868).

Moved from TODO.md lines 714 to 834:

## Shipped

Builds 1 through 19 — see `git log` for the full record. PRs are the
authoritative per-feature history.

Notable rollups for the current TestFlight cycle:

- **Trail-list panel drag glitchy → fixed.** Long-standing issue
  (originally option (a) in the older TODO entry: native sheet with
  `presentationDetents`). Closed across PRs #143, #176, #177, #178
  (perf passes that chipped at the cost but couldn't escape the
  SwiftUI body-rebuild ceiling), then #179 (full rewrite to native
  `.sheet + presentationDetents`), #180 (confirmation-dialog
  presentation conflict fix), #181 / #182 / #183 / #184 (de-glass +
  opaque background + header consolidation polish passes), #185
  (elevation chart metric ticks land on round numbers).
- **European data fully removed.** Repo (#186), R2 bucket (one-shot
  via the `cleanup-r2-orphans` workflow, 540 objects). App is NA-only
  end-to-end.
- **Pace / speed stats.** #191. Overall pace on hike detail + "Avg
  Pace" on the Stats summary card + a live pace column in the
  recording panel. `UnitFormatter.pace` honors the units toggle
  (/mi · /km); retroactive over existing history (distance + time
  already persisted).
- **Onboarding refresh.** #193. Replaced the single fullScreenCover
  with a swipeable 4-page walkthrough (Welcome · Discover · Record ·
  Complete) that showcases the Stats tab + live recording features.
- **Live elevation profile during recording.** #192. Elevation
  profile chart now renders live in the recording panel (reusing
  `ElevationProfileView` + `elevationStats`), so the user sees
  climbing in progress instead of only post-hike.
- **Dex / achievements per area.** #195 (page) + #196 (tap-a-badge
  detail). Pokédex-style grid as a second segment of the area sheet
  (Trails | Dex): milestones, difficulty firsts, distance tiers, and
  dedication badges, all derived retroactively by `AchievementEngine`.
- **Export fail-loud.** #197. `collectExport()` now throws instead of
  silently dropping an unreadable `hike-history.json`, so a backup
  can't quietly omit the irreplaceable GPS recordings.
- **App Store submission prep.** All four code blockers done: account
  deletion (#198), OSM attribution (#199), privacy manifest (#200),
  location When-In-Use (#202). Plus trekdex.app legal links + Terms
  (#205) and the `docs/app-store-submission.md` metadata/reviewer-notes
  package (#206). Remaining is external — see "Waiting on you".
- **Analytics + feedback pipelines (PostHog).** #208–#212. Swappable
  `AnalyticsService` facade (no-op default), instrumented events, an
  in-app Send Feedback form, the PostHog backend wired to the US
  project (key in Info.plist, anonymous, no autocapture), and MetricKit
  crash/hang capture. One SDK covers analytics + feedback; privacy
  manifest updated to match.
- **Units consistency + area header.** #213. Routed the last hardcoded-
  miles sites through `UnitFormatter` (area total, Settings → Your
  Activity, share card, far-warning). Collapsed the area sheet header
  to one line (trails · distance · completion, green at 100%) and
  dropped the redundant OSM caption there.
- **Live pace / ETA fix.** #214. Path timestamps are epoch
  milliseconds but `smoothedPaceMetersPerSec` read them as seconds, so
  the 60 s window was really 60 ms — live pace, ETA, and suggestion
  timing all silently returned nil. Fixed the ms→s math; extracted a
  pure, tested `paceMetersPerSec`. (Recording-panel stat truncation
  from the added Pace column fixed in #217.)
- **Out-of-region waitlist.** #219. Device Region ∉ US/CA (via
  `Locale`, no permission) → a waitlist card atop Explore ("Not in
  <Country> yet" + email → Join), remembered across launches. Soft
  prompt — US/CA parks still browsable. Signups ride PostHog as a
  `waitlist_joined` event (country + email); no new backend. Collection
  now; the launch-email *send* is a separate future step (export by
  country from PostHog).
- **CI infra hardening.** Fixed the macos-latest runner rolls:
  download the iOS platform before build un-sudo'd with retry (#197,
  #204), and resolve the test simulator UDID dynamically instead of
  pinning a device name (#201). Serialized `DataBackupManagerTests`
  to kill a filesystem race (#204). Later: bounce a wedged
  CoreSimulatorService between download retries (#231).
- **App Store screenshot automation.** #226–#239. Dispatch-only
  `ios-screenshots` workflow: 6.9" simulator, DEBUG-only
  `UITestSupport` seeds an art-directed South Mountain demo state
  (25% completion for the map shot, 43/48 + live Bajada recording for
  the recording shot, honest path-length hikes for a full-width
  jagged elevation profile), XCUITest drives the 5 planned screens
  via Stats-push navigation, PNGs upload as an artifact. War-story
  root cause of a week of failures: the location-permission sheet
  presented over the tab bar on permissionless CI simulators,
  swallowing taps and blocking every other modal (#236).
- **Device-feedback polish sweep.** Browse tab icon opens the search
  keyboard incl. re-taps (#233). All-areas map made actually usable:
  native Markers + flat elevation (#234) + viewport cull of the
  3,000-marker zoomed-in blowup (#237). AreaCard geometry + spacing
  rounds — glass overflow, heart collision, box width/height, name-gap
  collapse (#235, #237, #241, #244, #245). Elevation-chart ghost
  lines from duplicate stand-still GPS distances (#240). Stats Area
  Progress shows hike-only areas at 0/N (#242). Browse: trail-name
  search with pre-selected trail deep-link + silhouette-linework
  thumbnails (#243, un-boxed + one-line captions in #244). Trail rows
  render each trail's own linework as the icon (#247).
- **Walk-anywhere (multi-area recording).** #248. Start a walk with no
  area selection: Explore's figure-walk button opens a map of every
  trail from the ~12 nearest areas (20 mi, center-distance, frozen at
  start); at Stop & Save the walk credits coverage/completions to
  EVERY area the GPS path touched, with a per-area summary sheet.
  Walks persist as normal history records under a primary (nearest)
  area plus a `multiAreaCompletions` dict — deliberately NOT a new
  persisted mode enum, because an old build decoding an unknown enum
  raw value would blank the whole `try?`-decoded history array and
  truncate hike-history.json on its next save. All walk-aware
  consumers updated (launch rebuild, area halos/history, revisit
  anchors, Stats, Settings totals, Dex, trail walked-counts, HikeRow
  Walk badge). Six new unit tests. Follow-ups tracked in Backlog —
  features. Field-fixed after first walk: stale location on reopen +
  swallowed Stop & Save summary sheet (#250).
- **Trail-data pipeline scaffold + NZ §10 pilot.** #254. New
  `data-pipeline/` tree implementing `TRAIL_DATA_PIPELINE_SPEC.md` — the
  two-gates model (fail-closed licensing gate is the only thing that
  removes a trail; confidence score is a dev/on-device authoring aid,
  never baked into tiles). Registry + validator, OSM/DOC/LINZ
  downloaders, pure-Python stager + DuckDB SQL, shapely conflation +
  QA flags, thin Bucket-B `confidence.py`, on-device `scoring_reference.py`
  + weights, attribution generator, inclusion guard, tippecanoe→pmtiles,
  Cloudflare Worker PMTiles handler for R2, 58 unit tests, and the
  dispatch-only `build-region-tiles` workflow. Details + next steps in
  "In flight". (Rode in with a pre-existing R2 areas-index commit that
  was on the branch.)
