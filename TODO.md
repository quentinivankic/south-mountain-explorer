# TODO

Long-running tracker of shipped work + open items. Live "what's in the
current build" planning lives in `~/.claude/plans/binary-hatching-
toucan.md`; this file is the historical record.

⚠️ **Read the dated section below, then treat every "In flight" section
further down as HISTORY.** Those were accurate on their date and are kept for
the lessons; their PR numbers and "NEXT" instructions are stale. Verify against
code / `gh pr list` before acting on anything below this line.

**Archived 2026-11-12.** The Shipped and HISTORICAL sections and the done `[x]`
backlog items moved word for word to `docs/archive/TODO-history.md`. Open `[ ]`
items from those sections are copied below under "Open follow-ups carried over".

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

## Open follow-ups carried over (their sections are in `docs/archive/TODO-history.md`)

From "Shipped 2026-07-18/19", open follow-ups from that session:
- [ ] **Parking nationwide** — expand `add-parking.py` AZ → all states (safe now:
  publish preserves it); validate USFS on first states.
- [ ] **Canada** (360) — DEFERRED per user 2026-07-19.

From "Shipped 2026-07-13/14", open follow-ups (updated 2026-07-15):
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
- [ ] **iOS 18 Liquid Glass visual QA.** Build was dropped to iOS 18
  with `.regularMaterial` as the glass fallback (PR #165) but never
  eyeballed on an actual iOS 18 device/simulator. Pure QA, no code.
- [ ] **NAME_KEYWORD_RE dead weight.** Still carries
  Danish/German/Icelandic/French/Italian keywords now that EU is
  gone. Tiny cleanup.
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
