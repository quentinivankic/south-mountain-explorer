# TODO

Long-running tracker of shipped work + open items. Live "what's in the
current build" planning lives in `~/.claude/plans/binary-hatching-
toucan.md`; this file is the historical record.

⚠️ **Read the dated section below, then treat every "In flight" section
further down as HISTORY.** Those were accurate on their date and are kept for
the lessons; their PR numbers and "NEXT" instructions are stale. Verify against
code / `gh pr list` before acting on anything below this line.

## In flight (2026-07-28) — the current picture

- 📋 **OPEN TASKS NOW LIVE IN `TASKS.md`** (13 of them, full measurement history).
  The in-session task list does not survive a new session — re-create them with
  `TaskCreate` from that file. This TODO stays the historical record; `TASKS.md`
  is the forward-looking one.
- **Global parking pool SHIPPED (#500).** Containment stays a QUALITY filter and
  stops being OWNERSHIP: `scripts/build-parking-pool.py` emits every qualifying
  lot once to `cdn.trekdex.app/parking.json` (29,196 lots, 0.33 MB gz, built fresh
  by `sync-geom-to-r2`, never committed) and the app merges it with each area's own
  lots via `ParkingPoolService.merged(with:for:)`. All three display paths — pins,
  camera frame, row banner — go through it; wiring only one names a lot with no pin
  under it. Measured: 6,390 trails (6.9%) gain a lot, 1,027 of them in areas with
  no parking at all, across 1,965 areas. Re-measured over ALL trails: **10,801
  trails went from "nothing within 5 miles" to having something**; 13,416 still
  have nothing, which the pool cannot fix because those lots are in neither OSM nor
  the federal layers. Follow-up is task #44.
- **An assertion guard is now enforced in `.claude/hooks/` (#505).** Gotcha #14
  says run a counterfactual before asserting it; a `UserPromptSubmit` hook re-arms
  that on every prompt and a `Stop` hook blocks a turn whose final message asserts
  one with neither a verification citation nor a hedge. Calibrated against all 318
  assistant messages of the session that produced it: 5 would block (1.6%). Fails
  open, blocks at most once per turn. `python3 .claude/hooks/test_verify_counterfactuals.py`
  → 8/8.
- **v1.0 is SUBMITTED to the App Store** (2026-07-26) and awaiting review.
  iPhone-only (`TARGETED_DEVICE_FAMILY=1`, #478). Privacy answers, store copy and
  the screenshot recipe are in auto-memory `app-store-submission.md`. The
  submitted binary is `919c5c1f5`; **TestFlight has since moved on to
  `83e8fbbd3`** (the completion-gate fix, #496) — a separate build that does not
  disturb the one in review.
- **Trail completion no longer needs you to touch both ends** (#496,
  `83e8fbbd3`). The gate was `fraction >= 0.95 && endpointsVisited`, where the
  "ends" were the first node of the first segment and the last node of the last —
  meaningless for the **11,191 of 92,297** trails stored as several disconnected
  pieces in arbitrary OSM order. Pima West Loop (a closed loop plus a 19 m orphan
  127 m away) and Guadalupe Perimeter (five pieces, ends 1.4 km apart) were
  permanently uncompletable. Now `fraction >= 0.95 && longestSkippedRunM <= 50`.
  Measured against 11,191 disconnected trails + a 6,000 control and four walkers:
  deleting the gate outright falsely completes **46% of ordinary trails** when a
  hiker stops 100 m short, so it was replaced rather than removed. Rejected with
  numbers in the commit: bare delete, topological free ends, and splitting
  disconnected trails into one trail per component.
- **Road-as-trail (#21) is PARTLY solved and the honest yield is small.** 18 ski
  and snowmobile routes dropped (35.2 mi, 13 areas, #497). The **~2,780
  road-classified trails remain unsolved**, and four approaches are ruled out with
  evidence — see the task and auto-memory `curation-signal-lessons.md` before
  proposing a fifth. The lesson generalises: OSM tags describe a way's FORM,
  curation questions are about its PURPOSE, and the fix was to ask the landowner's
  own inventory (`EDW_TrailNFSPublish_01`). Verdicts now live in the reviewable
  sidecar `public/areas/nonhiking-trails.json`; put bridleways (#34) there too.
- **Trail / area QUALITY is the active engineering thread** — #21 (partial), #31,
  #33-#38 open; #30, #32 and #42 shipped. Re-measure any baseline with
  `python3 scripts/audit-trail-quality.py`; depth in auto-memory
  `area-quality-grayling-audit.md` and `curation-signal-lessons.md`.
  Shipped trail count is now **92,279** (92,548 before #492 dropped 251
  zero-length trails and #497 dropped 18 snow routes).
- **Parking: national, with one real-but-modest bug fixed in code (#487,
  `519b58ad`).** 6,204 of 9,060 areas ship parking (39,082 lots, verified
  2026-07-26). `assign_federal` dropped a federal trailhead as "OSM covers it"
  *before* the 250 m edge search, which made that edge rule unreachable for a
  wilderness nested inside a national forest — the exact case it was written for.
  - **Do not oversell it.** The affected POPULATION is large (1,376 of the 2,856
    blank areas sit inside an area that has parking, 326 named Wilderness), but
    an A/B of `main` against the fix over the same fetched boundary + federal
    data gave **Arizona +1, New Mexico +4, 0 regressions** — Organ Pipe Cactus,
    Bandelier, Dome, San Pedro Parks and Sandia Mountain Wildernesses. Order of
    **~100 areas nationally**, not 326, and states with little federal land
    contribute nothing.
  - **The binding constraint is federal source coverage, not the rule.** Of
    Arizona's 37 blank areas that have a boundary, **26 have no federal point
    within 5 km**. Only 846 of the 2,856 blank areas have an `osm_relation_id`
    at all, so 2,010 can never be edge-filled. USFS's ArcGIS layer returned 93
    features for the whole of Arizona. This matches the 2026-07-18 finding in
    auto-memory `parking-feature.md` ("rescues only 3 of 47 blank areas").
  - **Follow-up worth its own review:** five real wildernesses sit in the
    250 m – 1 km band (Chiricahua 358 m, Granite Mountain 405 m, Miller Peak
    697 m, Bear Wallow 780 m, Castle Creek 834 m). Raising
    `_FED_EDGE_BUFFER_M` toward ~1 km would roughly triple the Arizona fill, but
    the buffer is the only thing preventing misattribution to a neighbouring
    area and the road gate does not help with that — needs before/after review on
    named examples, not a tuning tweak.
  - **The geom does not change until `trailforge-parking.yml` is re-run — that
    roll has NOT happened.** ~4 h at `max-parallel: 8`.
- **Canada is the only coverage gap left.** 375 `-ca-*` rows sit in the master
  index with trail counts (Banff 421, Jasper 199) and ZERO published geom, and
  there is no Canada publish path (`trailforge-publish-us.yml` is 51 US codes).
  The iOS bundle gates on geom presence, so none of them reach the app — this is
  a missing region, not a broken one.
- **`trailforge/extract/` NAS staging scripts merged** (#468, `da065332`) —
  `fetch-us-extract.sh` pulls the ~11 GB Geofabrik US extract onto the NAS and
  `prefilter-access.sh` derives a roads + parking subset. This is what turns
  Overpass-per-question work into local osmium passes; task #22 (`--update` via
  pyosmium diffs) builds on it.
- **PR queue, cleaned 2026-07-26.** Merged #468 and #487. Closed:
  - **#445** trail elevation profiles — its work landed via **#447**, and the
    branch was hundreds of files behind `main`, so merging it would have reverted
    the dedup/alias work, `docs/adr/0002`, and most of `public/areas/geom`.
  - **#444** parking wilderness fix — superseded by #487, which re-applied the
    same 13-line hunk on current `main` without reverting later fixes.
- **#425 CLOSED unmerged 2026-07-28** (parking gate-after-assign +
  `roll-parking.sh`). Three independent reasons, any one sufficient. (a) It was
  **broken against `main` and failed silently**: `road_gate` now returns
  `(kept, ok)` — the fail-closed flag from #40 — but `road_gate_assigned` did
  `{id(lot) for lot in road_gate(...)}`, iterating the TUPLE, matching no lot,
  returning `{}` and dropping every federal lot. Its test passed anyway because
  it stubbed `road_gate` with a list-returning lambda. Gotcha #7 exactly.
  (b) Its **premise was already false when written**: the PR argued that gating
  before assignment costs "hundreds of Overpass `around` queries", but
  `road_gate` has batched 60 clauses per query since #423 — the branch's OWN
  merge-base (`_ROAD_CHUNK = 60` at `212ce8434`). 500 points is 9 queries, not
  500. (c) `roll-parking.sh` is **superseded** by `trailforge-parking.yml`'s
  per-state matrix (#455), and the roll it prepped already completed.
  **What survives:** `main` still gates BEFORE assigning
  (`add-parking.py:1198` then `:1205`), so a ~9 → 1 query saving is real and
  unclaimed — a fresh small PR off `origin/main` that respects the tuple, never
  a rebase of `claude/parking-roll-prep` (83 commits behind, conflicts).
- **Still open (verified 2026-07-26):**
  - **#224** app-icon punch-in — a product call that needs a fresh build; parked
    while v1.0 is in review.
  - **#147** Live Activity and **#144** distance-to-next-turn — long-standing
    drafts, both still valid ideas.
- **Branch pile PRUNED 2026-07-28: 340 → 23.** `delete_branch_on_merge` is now
  ON, so merged PR branches clean themselves up from here. The one-time sweep
  deleted 317: **315 were the head of a MERGED PR**, 2 had a tip that is a plain
  ancestor of `main`. Kept: 3 open-PR heads (#224, #147, #144), 15 heads of
  CLOSED-unmerged PRs (work never landed — incl. `claude/parking-roll-prep`
  from the just-closed #425), 4 branches with no PR at all and not in `main`
  (`claude/build-7-decimation-history-migration`, `claude/icon-history-preview`,
  `claude/pbf-pipeline-j1`, `claude/privacy-policy-doc` — unknown work, left
  alone deliberately), and `main`.
  **Nothing was lost, and the reason is worth knowing:** GitHub keeps
  `refs/pull/<N>/head` forever, independent of the branch. Any deleted branch
  with a PR is recoverable with
  `git fetch origin refs/pull/<N>/head && git push origin FETCH_HEAD:refs/heads/<name>`.
  Verified present for a sample spanning #154 → #465.
- ⚠️ **`main`'s git history only goes back to 2026-07-08** — the root commit is
  `1451d04cf` (17,373 files), and `git rev-list --count origin/main` is 312.
  Everything merged before that date (roughly PRs #1–#280: the export/import
  work, onboarding reset, trail search, the original assembler) is **NOT in
  `main`'s commit history at all**, though its CONTENT is in that root tree.
  This bites when auditing: a genuinely-merged old PR's merge commit will fail
  `git merge-base --is-ancestor <sha> origin/main`, which looks exactly like
  "never merged". It is not. Check `refs/pull/<N>/head` and the PR's
  `mergedAt`, not ancestry, for anything older than 2026-07-08.

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

## App Store release gate

✅ **This gate is CLEARED — v1.0 was submitted 2026-07-26 and is awaiting
review.** The list below is kept as the record of what was required and how each
item was satisfied; the two remaining unchecked boxes are noted inline. The old
framing ("these only matter if you ever want to ship to the store") no longer
applies — App Review compliance is live now, so anything that touches privacy,
metadata, IAP, or account deletion has to stay true.

### Hard blockers (App Review will reject)

- [x] **In-app account deletion** — Guideline 5.1.1(v). Done in #198.
  SiwA is purely local (no server account), so Settings → Account →
  Delete Account removes the local Keychain credential; hikes/progress
  stay (Reset All Progress wipes those).
- [x] **OpenStreetMap attribution** — Done in #199. "© OpenStreetMap
  contributors" in Settings → About (links to the ODbL page). (The
  extra area-header caption was removed in #213 — the About credit
  alone satisfies the ODbL.)
- [x] **Privacy manifest (`PrivacyInfo.xcprivacy`)** — Done in #200,
  expanded in #211/#212 when PostHog + feedback + MetricKit shipped.
  Now declares Product Interaction, Other User Content, Email, and
  Crash Data (all not-linked, not-tracking); `NSPrivacyTracking` still
  false.
- [x] **Privacy policy URL** — Hosted at trekdex.app/privacy-policy;
  in-app link updated + Terms of Service added in #205.
- [x] **App Privacy "nutrition label"** — ENTERED in App Store Connect
  2026-07-26. **Data Collection: Yes** — Email Address, Other User Content
  (feedback), Product Interaction (PostHog), Crash Data. All *not linked, not
  tracking*; purpose is App Functionality except Product Interaction =
  Analytics. **Location is NOT collected** (GPS stays on device, map-fetch
  coords are transient) — keep PostHog GeoIP OFF or that stops being true.
  Answers must stay consistent with `PrivacyInfo.xcprivacy`.
- [x] **App Store metadata package** — DONE and submitted. Description,
  subtitle, keywords, promo text, category (Health & Fitness + Navigation),
  age 4+, content rights (OpenStreetMap under ODbL), privacy policy + ToS
  URLs, support/marketing URL, automatic release after approval. Text drafted
  in `docs/app-store-submission.md`. **Screenshots are automated**: the
  dispatch-only `ios-screenshots` workflow (#226–#239, polish in #255) boots a
  6.9" simulator, seeds an art-directed South Mountain demo state, drives the 5
  shots via UI test, uploads the PNGs as an artifact — 5 at 1320×2868, which ASC
  scales to every other size. Going iPhone-only (#478) is what removed the iPad
  13" screenshot requirement.
- [x] ~~DUNS / organization enrollment~~ — **N/A (decided 2026-07-15): ship
  v1 under the existing INDIVIDUAL account.** No D-U-N-S / Org enrollment
  needed to submit; the only tradeoff is the seller name being the
  developer's personal legal name. Org conversion DEFERRED (not started —
  the earlier "conversion submitted" note was inaccurate). This removes the
  1–2 week D-U-N-S wait from the critical path. When pursued later, confirm
  with Apple Support whether it's an in-place membership conversion or a new
  Org account + App Transfer (App Transfer has conditions). ⚠️ Still worth
  making sure the **payment card on the Apple account is valid** so the
  membership doesn't lapse.

> ⚠️ **Analytics + feedback now collect off-device** (PostHog, US
> region — shipped #208–#212). The manifest already reflects this; make
> sure the **ASC nutrition label** and the **trekdex.app privacy
> policy** name PostHog + the US region and list the collected types.
> The out-of-region **waitlist** (would collect email → Contact Info)
> is still unbuilt; if it lands, revisit all three again.

### Strongly advised (rejection-likely or bad first impression)

- [x] **Re-gate the Trail Confidence Lab before App Store.** DONE. Wrapped
  `TrailConfidenceLabView.swift` (whole file) + its Settings entry point
  (the `showTrailLab`/`labTapCount` state, the NavigationLink, and the 7-tap
  Build-row reveal) in `#if DEBUG`, so the dev authoring tool compiles out of
  the Release/App Store binary entirely (§8). The `TrailScoring` service +
  tests are untouched. Verified nothing in Release references the gated
  symbols.
- [x] **"Always" location audit** — Done in #202. The app never
  actually needed Always (no geofencing / significant-change relaunch;
  `requestAlwaysAuthorization` was defined-but-unused). Dropped the
  Always usage string + dead methods; When-In-Use +
  `UIBackgroundModes: [location]` covers background recording,
  confirmed on-device with a screen-locked hike (continuous track).
- [x] **App Review reviewer notes** — Drafted in
  `docs/app-store-submission.md` (how to simulate a GPS hike via Xcode
  Simulate Location, roam vs trail, SiwA + account-deletion path).
  Paste into ASC at submission.
- [ ] **Stability / crash pass** — The in-repo half is done: static
  crash-risk audit (#222, one real force-unwrap fixed) + Swift 6
  concurrency audit (#223, clean) — see `docs/stability-audit.md`.
  MetricKit crash capture (#212) is the field net. Remaining: broad
  on-device QA sweep (largely happening via daily TF use; an older-
  model iPhone pass would still be nice).

## Backlog — features

- [ ] **Walk-mode follow-ups** (feature shipped #248, bug fixes #250):
  - _Field-test status (first SF walk, no trail completions yet):_
    open speed, area load, pan/zoom, conflict warning, stop, force-quit
    restore, live panel stats, backgrounding, Stats data, and the
    Stop & Save summary sheet all ✅. Stale-location + swallowed-summary
    bugs fixed in #250. STILL UNTESTED: a walk that actually finishes a
    trail — unlocks summary-with-completions, per-park credit (mint +
    cyan halo), Walk badge counts, hike detail, Dex credit, "Walked
    once". Radius/cap (20 mi / nearest-12) confirmed fine on device.
  - **Persist throttling for all-day walks.** The recorder re-encodes
    the ENTIRE ActiveRecording to UserDefaults after every GPS point
    (crash safety). Fine for 1-5 h hikes; a 12 h city walk grows the
    path to 15-20k points and the per-point encode cost with it.
    Throttle to every ~30 s / N points. Related: the crash-restore
    window is 12 h from START — consider extending for walks.
    (Battery/warmth held up on the first field walk; not yet urgent.)
  - **Mid-walk trail feedback.** v1 computes all credit at Stop & Save,
    so there's zero live signal that you're accumulating coverage. A
    cheap proximity-only "on trail: <name>" line in the walk panel
    would close the gap without running full coverage math live.
  - **Walk completion celebration.** The per-area summary sheet
    (confirmed good on device) is understated next to the in-area
    confetti moment.
  - **Dex semantics decision.** A walk currently counts as "a hike in"
    EVERY credited area, full distance included — a 10 mi walk that
    clips 3 parks adds 10 mi toward each park's distance badges.
    Generous by design; revisit after a crediting walk.
  - **Marketing.** "Start a walk anywhere" is a differentiator — worth
    a 6th App Store screenshot / description bullet later.
- [ ] **Distance-to-next-turn banner (#144).** Third line in the
  recording banner during trail-mode: "→ 420 ft to next turn". Already
  scoped in a stale PR; pure logic + one UI line. No provisioning
  hurdles, ships through normal TF.
- [ ] **Photos on a hike.** Attach geotagged photos to a recording,
  show them as pins on the hike-detail map. High emotional value for
  a hiking app.
- [ ] **Offline map tiles.** Download an area's map region for
  no-signal hiking. Geom + silhouettes already cache locally; map
  tiles (MKMapView/MapKit overlay caching) is the missing piece.
- [ ] **Weather for an area.** Current conditions + forecast in the
  area sheet. WeatherKit is free for Apple devs; minimal new infra.
- [ ] **Home-screen widget.** Simpler than Live Activity (no
  per-update provisioning drama). Surfaces things like "trails
  completed this month" or "nearest area." Reuses the Live-Activity
  widget extension target if/when that lands.
- [ ] **Live Activity / Dynamic Island.** Stale draft branch #147 has
  the model + service + widget UI roughed in. Blocked on Apple
  Developer portal setup: register App ID for
  `com.southmountainexplorer.app.widgets`, create distribution
  provisioning profile, add as `APPLE_WIDGETS_PROVISIONING_PROFILE_BASE64`
  GitHub secret. Then the widget target can sign and the workflow can
  ship it. Walk mode (#248) is the killer use case — an all-day walk
  living in the Dynamic Island.
- [ ] **Smarter mid-hike recommendations.** Full analysis in
  `docs/recommendations-notes.md`. TL;DR:
  (1) **Verify it even fires now.** The `SuggestionBanner` bails when
  pace is nil, and pace returned nil on every hike until the ms→s fix
  (#214) — so the banner was silently dead on builds ≤197. Re-test on a
  #214+ build before assuming it's broken.
  (2) **Frequency vs relevance.** The 300 m-detour + 1.5 mi-remaining
  caps make it fire in a narrow "trivial add" window. Loosening the
  caps is the wrong lever (the engine is heading-blind, so more firing =
  more *irrelevant* pops). The right fix is anchoring to the fork you're
  approaching.
  (3) **Make it richer.** Today it's proximity + detour-time +
  completion only. Wanted: "the fork coming up gets you more completion,
  and it's an easy, flat path" — difficulty/terrain-aware
  (`Trail.difficulty`, elevation) + heading/junction-aware + ranked by
  completion-gained-per-minute. See the notes doc for the redesign.
- [ ] **"Suggest a hike" (coverage-optimized route).** A *proactive
  planned route* built to maximize area coverage/completion — not
  individual-trail nudges — e.g. "This 4.2 mi loop hits 3 uncompleted
  trails and takes you from 5/48 → 8/48." Needs a trail-graph + route
  search over uncompleted segments (coverage-weighted routing) and a way
  to present/start it. Bigger than the banner, and shares the
  junction/graph groundwork the fork-anchored mid-hike redesign needs —
  worth building that graph layer once for both. See
  `docs/recommendations-notes.md`.

## Backlog — UX / polish

- [ ] **Completion celebration upgrade.** Trail-complete and
  area-100% currently show a basic overlay. Add haptics + a richer
  animation; this is the moment of payoff.
- [x] **Trailheads & parking pins** on the map — SHIPPED 2026-07-18 (AZ). OSM
  containment-gated parking + federal (NPS/USFS) fallback; show only for the
  selected trail (≤3 nearest). See `parking-feature.md`. Remaining: roll beyond AZ.
- [ ] **Share card expansion.** `ShareableHikeCard` exists; grow it
  into a proper "I completed X" share-out (especially good with the
  Dex once that lands).

## Backlog — tech debt / ops

- [~] **Area quality cull.** PARTIAL — a `_MIN_AREA_MI=0.1`
  degenerate-clip gate shipped 2026-07-14 (`publish_areas.py`; skips
  areas whose trails clip to a near-zero sliver — 97 live ones swept,
  `31c660c5`). A broader trail-count/mileage floor with an NPS-style
  whitelist is still open (some 1-2 trail fragments dilute Browse), but
  data showed the floor must be low: dropping <0.15mi trails would empty
  6 areas + gut 30%+ of trails in 31 more, so tread carefully.
- [x] **R2 NA orphan purge.** DONE 2026-07-15 (#375, task #36).
  `cleanup-r2-orphans.py` generalized off the Europe-only allowlist to
  "anything not in the current index," with safety belts (≥1000-id
  floor, protected root files, >60%-orphan abort). Dry-run flagged 1,683
  orphans (6.1%: dropped red-flag areas, slug renames, below-quality CA
  areas); apply run deleted all 1,683. The 375 curated CA areas the app
  ships were untouched.
- [ ] **iOS 18 Liquid Glass visual QA.** Build was dropped to iOS 18
  with `.regularMaterial` as the glass fallback (PR #165) but never
  eyeballed on an actual iOS 18 device/simulator. Pure QA, no code.
- [ ] **NAME_KEYWORD_RE dead weight.** Still carries
  Danish/German/Icelandic/French/Italian keywords now that EU is
  gone. Tiny cleanup.
- [x] **Global trail-name search index.** DONE 2026-07-14 (#361 +
  `build-trail-search-index.py`). Compact `[name,areaId,trailId,mi,
  difficulty]` index served from R2 as `trail-search.json` (~1.3 MB gz),
  loaded by `TrailSearchService` — trail search is now nationwide.
  Follow-on shipped: **search-result trail thumbnails** (#363) via a
  separate background-loaded `trail-shapes.json` (~3 MB gz, ~11-pt
  Douglas-Peucker shapes).
- [ ] Ad-Hoc + Diawi distribution pipeline (only if the TestFlight
  cycle becomes a real bottleneck).

## Backlog — content

- [~] **Fuller NA coverage.** IN PROGRESS 2026-07-18/19: the big US gap was the
  multi-state boundary bug (810 areas incl. Great Smoky/NFs) — fixed + whole-US
  re-publish running (see "In flight" + `coverage-gap-missing-areas.md`).
  Remaining: no-rel-id stragglers (re-seed), then Canada (deferred). Do NOT use
  the old `build-trail-index` dispatch — that's the purged System-1 pipeline.

## Waiting on you (not code)

Everything below is external / on-device and can't be finished in the
repo:

- [x] Enter the App Privacy nutrition label in App Store Connect — DONE
  2026-07-26 (Email, Other User Content, Product Interaction, Crash Data; all
  not-linked / not-tracking; Location NOT collected).
- [x] **Privacy policy + ToS — DONE (user, 2026-07-19): fully up to date and
  live on the website.** Canonical source in-repo: `docs/privacy-policy.md` +
  `docs/terms-of-service.md` (#382) — analytics (PostHog + MetricKit + feedback/
  waitlist) language corrected, not-built iCloud/CloudKit claim removed. Entity:
  **Trekdex LLC** (real entity; Individual Apple account = store seller name is
  the personal legal name).
- [ ] **Paid Applications Agreement + tax/banking in App Store Connect** —
  REQUIRED because the app ships a **three-tier tip jar IAP**. NOT VERIFIED
  either way from here: 1.0 was submitted with the tip jar attached, which
  normally means the agreement is in place, but nothing in the repo can confirm
  it and no in-session note records signing it. **Check ASC → Business before
  release** — if it is unsigned the products stay unavailable and the tip jar is
  dead UI in a shipped app. (Was once marked N/A under a "free, no IAP"
  assumption — corrected 2026-07-19.)
- [x] Dispatch `ios-screenshots` for the final capture set, then upload the
  PNGs to ASC — DONE for the 1.0 submission (5 shots at 1320×2868).
- [ ] Crash/stability pass on device. Still the one genuinely open QA item —
  an older-model iPhone pass would be the useful next step.
- [x] **Payment card on the Apple account — UPDATED 2026-07-19 (user).** Keep it
  valid so the Individual membership doesn't lapse. (Org conversion deferred —
  shipping v1 under Individual, decided 2026-07-15.)
- [x] Dispatch **`ios-testflight`** for the submission-candidate build, then
  submit that build in ASC — DONE. The Confidence Lab was already `#if DEBUG`;
  build `919c5c1f5` (iPhone-only) is what 1.0 was submitted with. Claude may
  now auto-dispatch TestFlight after app-CODE merges on green CI (user OK'd
  2026-07-26) — skip it for data-only and docs-only changes.
- [~] ~~Dispatch **Build Region Tiles** (`region=new-zealand`)~~ — **N/A: the
  workflow no longer exists.** `build-region-tiles.yml` was deleted in #366 as
  dead System-2 pmtiles infrastructure superseded by trailforge; the
  `data-pipeline/` tree is still in the repo but nothing dispatches it. Don't
  resurrect this without deciding the pmtiles path is worth reviving.

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
