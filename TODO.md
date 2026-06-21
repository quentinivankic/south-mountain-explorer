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

- [ ] **In-app account deletion** — Apple Guideline 5.1.1(v): any app
  offering account creation must provide an in-app delete path. Sign
  in with Apple counts as account creation. If SiwA is purely local
  (no server-side account record), document that and the delete-all-
  local-state flow IS the delete; if anything is server-side, build
  the deletion call. Extremely common rejection reason.
- [ ] **OpenStreetMap attribution** — Trail geometry + silhouettes are
  OSM-derived (ODbL license), which legally requires a visible
  "© OpenStreetMap contributors" credit. Missing it is both an ODbL
  violation AND an App Review 5.2 IP risk. Add an attribution line
  somewhere in Settings → About, ideally also a small credit on the
  map view.
- [ ] **Privacy manifest (`PrivacyInfo.xcprivacy`)** — Required since
  May 2024 for apps using required-reason APIs (UserDefaults, file
  timestamps, boot time, disk space) and apps that collect precise
  location. Draft existed in the closed PR #141.
- [ ] **Privacy policy URL** — App Store Connect requires a reachable
  hosted privacy policy for any data collection. The Notion page
  linked from Settings → About probably qualifies; verify it actually
  reads as a real privacy policy (collection categories, retention,
  contact, etc.).
- [ ] **App Privacy "nutrition label"** — Declare in App Store
  Connect: precise location (yes), linked to identity (no), used for
  tracking (no), purpose (app functionality only).
- [ ] **App Store metadata package** — Screenshots (multiple device
  sizes), app description, keywords, category, support URL, marketing
  URL (optional), age rating questionnaire.

### Strongly advised (rejection-likely or bad first impression)

- [ ] **"Always" location audit** — Currently requesting
  `NSLocationAlwaysAndWhenInUse`. "Always" is Apple's highest-
  scrutiny permission. For an explicit user-started hike recording,
  `When In Use` + `UIBackgroundModes: [location]` works (Apple grants
  background tracking for the duration of a user-started session).
  Confirm whether the app genuinely needs Always for any flow (e.g.
  auto-resume of an interrupted recording across reboots); if not,
  downgrade. Removes both a rejection vector and a scarier user
  prompt at first launch.
- [ ] **App Review reviewer notes** — App Store Connect → Build →
  Notes. Reviewers test indoors with no real GPS; without
  instructions they'll mark recording as "non-functional" and reject.
  Include: how to simulate a hike (e.g. Xcode location simulation
  routes), what to expect in roam vs trail mode, how to test the SiwA
  flow + account deletion.
- [ ] **Stability / crash pass** — Broad device QA before submit.
  Drag jank is fixed, but App Review rejects crashy apps; do a sweep
  across a current iPhone + an older model if possible.

## Backlog — features

- [ ] **Dex / achievements per area.** Pokédex-style page showing
  milestones + badges for a given area: first hike here, first
  easy/medium/hard trail completed, every-trail crown, four-seasons,
  total miles in this area, etc. Entirely derivable from existing
  data (`RecordingService.loadHistory()`, `ProgressService.completions`
  date-stamped per-trail, `CoverageService`, Trail.difficulty) so it
  populates retroactively. Likely a second segmented tab inside the
  area sheet (Trails | Dex).
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
  ship it.

## Backlog — UX / polish

- [ ] **Completion celebration upgrade.** Trail-complete and
  area-100% currently show a basic overlay. Add haptics + a richer
  animation; this is the moment of payoff.
- [ ] **Trailheads & parking pins** on the map. Where to actually
  start a hike.
- [ ] **Share card expansion.** `ShareableHikeCard` exists; grow it
  into a proper "I completed X" share-out (especially good with the
  Dex once that lands).
- [ ] Featured area of the week (auto-rotate by ISO week).

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
- [ ] Ad-Hoc + Diawi distribution pipeline (only if the TestFlight
  cycle becomes a real bottleneck).

## Backlog — content

- [ ] **Fuller NA coverage.** Audit whether all 50 US states and 13
  Canadian provinces are seeded vs partially covered, fill gaps via
  `build-trail-index` dispatches.

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
