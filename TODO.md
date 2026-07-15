# TODO

Long-running tracker of shipped work + open items. Live "what's in the
current build" planning lives in `~/.claude/plans/binary-hatching-
toucan.md`; this file is the historical record.

## In flight (open PRs + awaiting action)

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
- **App Store Connect / org enrollment.** Individual→Organization
  membership conversion **submitted** (LLC + EIN + DUNS in hand; Apple
  reviews within ~1 business day and may call the D&B-listed phone
  number). Membership stays active meanwhile, so ASC listing prep can
  proceed now.
  - ⚠️ **Card expired** on the Apple account — update it (developer.apple.com
    → Update card) or membership/apps can lapse.
  - After approval: accept the **Paid Applications Agreement** + complete
    tax/banking (EIN, business bank account, W-9) under ASC → Business,
    if the app will be paid / have IAP.
- **Stale branch cleanup.** ~182 old `claude/*` branches. Can't delete
  from the agent environment (the git proxy silently drops ref deletions
  and there's no delete-branch API tool). Prune locally — keep `main` +
  the open-PR branches, `git push origin --delete` the rest — or turn on
  Settings → General → **"Automatically delete head branches"** so future
  branches self-clean on merge.

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
- [ ] **Fold DEM sampling into the publish pipeline** so gain survives a
  republish (it's a post-process now — a re-publish reverts to length-only).
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
- [ ] **System-1 (cached_at) area hygiene** (task #38) — a QA audit found
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
  its nested wilderness. Investigated a dedup; **deferred, keep both for now**
  (cosmetic since #372 made them completable). Real fix belongs in the pipeline.

## App Store release gate

Items required (or strongly advised) before submitting to the App Store
beyond TestFlight. Public TestFlight is currently live without any of
these; they ONLY matter when you want to ship to the store. None block
TF builds.

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
- [ ] **App Privacy "nutrition label"** — Draft in
  `docs/app-store-submission.md`. Now that PostHog analytics + feedback
  ship, this is **Data Collection: Yes** — Product Interaction, Other
  User Content, Email, Crash Data (not linked, not tracking). Still
  needs to be ENTERED in App Store Connect (you).
- [ ] **App Store metadata package** — Text (description, subtitle,
  keywords, category, URLs, age rating) drafted in
  `docs/app-store-submission.md`. **Screenshots are automated**: the
  dispatch-only `ios-screenshots` workflow (#226–#239) boots a 6.9"
  simulator, seeds an art-directed South Mountain demo state, drives
  the 5 planned shots via UI test, and uploads the PNGs as an
  artifact. Latest polish is **PR #255** (in flight — see "In flight"):
  stats variation, shot-3 zoom + blue dot, shot-5 tiles. Once merged +
  re-dispatched, paste the PNGs + text into ASC.
- [ ] **DUNS / organization enrollment** — Gates submission itself.
  **Individual→Organization conversion submitted** (LLC + EIN + DUNS);
  Apple review ~1 business day. See "In flight" for the post-approval
  steps (Paid Applications Agreement + tax/banking) and the ⚠️ expired-
  card warning.

> ⚠️ **Analytics + feedback now collect off-device** (PostHog, US
> region — shipped #208–#212). The manifest already reflects this; make
> sure the **ASC nutrition label** and the **trekdex.app privacy
> policy** name PostHog + the US region and list the collected types.
> The out-of-region **waitlist** (would collect email → Contact Info)
> is still unbuilt; if it lands, revisit all three again.

### Strongly advised (rejection-likely or bad first impression)

- [ ] **Re-gate the Trail Confidence Lab before App Store.** It now ships
  in Release (reachable in TestFlight) hidden behind a 7-tap gesture on
  Settings → About → Build, so the dev authoring tool can be tuned
  on-device. §8 says the shipped USER build carries no confidence UI —
  wrap `TrailConfidenceLabView` + its Settings link back in `#if DEBUG`
  (or drop the reveal) before submitting. Code comments flag both sites.
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
- [ ] **Trailheads & parking pins** on the map. Where to actually
  start a hike.
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

- [ ] **Fuller NA coverage.** Audit whether all 50 US states and 13
  Canadian provinces are seeded vs partially covered, fill gaps via
  `build-trail-index` dispatches.

## Waiting on you (not code)

Everything below is external / on-device and can't be finished in the
repo:

- [ ] Enter the App Privacy nutrition label in App Store Connect —
  now **Data Collection: Yes** (PostHog): Product Interaction, Other
  User Content, Email, Crash Data. Draft in `docs/app-store-submission.md`.
- [ ] Update the trekdex.app **privacy policy** to name PostHog as the
  analytics processor + the **US** data region + the collected types.
- [ ] Dispatch `ios-screenshots` against the **#255 branch** for the
  final capture set (verifies shot-3 blue dot + no empty stats month),
  then merge #255 and upload the PNGs to ASC.
- [ ] Crash/stability pass on device.
- [ ] Finish the **Individual→Organization** conversion (submitted;
  ~1 business day) + fix the ⚠️ expired card on the Apple account.
- [ ] Dispatch **Build Region Tiles** (`region=new-zealand`) — build-only
  first, then add `R2_*` secrets and re-run with `publish=true`.

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
