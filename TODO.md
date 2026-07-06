# TODO

Long-running tracker of shipped work + open items. Live "what's in the
current build" planning lives in `~/.claude/plans/binary-hatching-
toucan.md`; this file is the historical record.

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
  artifact. Still TODO: one dispatch to recapture with the final
  #239 art direction (halo removal, jagged elevation, 43/48 recording
  shot on Bajada), optional caption-bar framing, and pasting it all
  into ASC.
- [ ] **DUNS / organization enrollment** — Gates submission itself.
  In progress (waiting on the DUNS number).

> ⚠️ **Analytics + feedback now collect off-device** (PostHog, US
> region — shipped #208–#212). The manifest already reflects this; make
> sure the **ASC nutrition label** and the **trekdex.app privacy
> policy** name PostHog + the US region and list the collected types.
> The out-of-region **waitlist** (would collect email → Contact Info)
> is still unbuilt; if it lands, revisit all three again.

### Strongly advised (rejection-likely or bad first impression)

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

- [ ] **Area quality cull.** Drop areas below a trail-count /
  mileage floor with a name-token whitelist for NPS-style units.
  Originally raised this session before pivoting to NA-only; still a
  real Browse-quality lever (some areas are 3-trail / 2-mi fragments
  that dilute the list).
- [ ] **R2 NA orphan purge.** Bucket has ~17,000 objects; index
  references 3,226. So ~14k unreferenced NA geom/silhouette files
  (areas the count/dedup filters dropped before they reached the
  index). Storage bloat — the `cleanup-r2-orphans` script can be
  rescoped from "European only" to "anything not in the bundled
  index" for a one-shot purge, with the same dry-run / apply safety.
- [ ] **iOS 18 Liquid Glass visual QA.** Build was dropped to iOS 18
  with `.regularMaterial` as the glass fallback (PR #165) but never
  eyeballed on an actual iOS 18 device/simulator. Pure QA, no code.
- [ ] **NAME_KEYWORD_RE dead weight.** Still carries
  Danish/German/Icelandic/French/Italian keywords now that EU is
  gone. Tiny cleanup.
- [ ] **Global trail-name search index.** Browse trail search (#243)
  covers locally-available areas only (trail names live in full area
  payloads, not the index). A pipeline-built (trail name → area id)
  index served from R2 would make trail search nationwide.
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
- [ ] Dispatch `ios-screenshots` (Actions tab) once more for the final
  post-#239 capture set, then (optionally) frame with the caption
  headlines from `docs/app-store-submission.md`.
- [ ] Crash/stability pass on device.
- [ ] Finish DUNS / org enrollment.

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
