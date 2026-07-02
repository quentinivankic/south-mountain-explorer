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

> ⚠️ The privacy manifest / nutrition-label / policy items above are
> written against today's reality: **nothing is collected off-device**
> (no backend, no analytics). Two planned features would change that
> and force all three to be revised — the **out-of-region waitlist**
> (collects email → Contact Info) and **analytics + crash reporting**
> (Crash/Usage data). If either lands before or after store submission,
> update the manifest, the ASC nutrition label, and the privacy policy
> together. See the Backlog entries for both.

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
- [ ] **Out-of-region waitlist + notify-me.** Coverage is US + Canada
  only. For users outside NA, show a waitlist prompt instead of an
  empty Explore/Browse: detect region (start with `Locale.current.
  region` — cheap, no permission; optionally refine with the user's
  coarse location if already granted), and if it's not US/CA, offer
  "We're not in <country/continent> yet — get an email when we are."
  Capture their email + region.
  Needs infra the app doesn't have yet: TrekDex is currently 100%
  backend-less (SiwA is local, all data on-device), so storing emails
  + sending "your region is live" notifications requires a real
  service (a hosted list / DB + a send path). Scope that first.
  **Privacy coupling:** collecting an email address IS data
  collection of Contact Info tied to identity — updates the privacy
  manifest (`NSPrivacyCollectedDataTypes`), the App Store nutrition
  label, and the privacy policy (all currently say "nothing
  collected"). See the App Store gate section.

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

- [ ] **Analytics + crash reporting.** No analytics or crash capture
  today. Want both: usage analytics (screen/feature engagement,
  funnels) and crash/exception logs to catch field crashes before
  they become reviews.
  Options, cheapest-privacy-impact first:
    - Crashes: **MetricKit** (`MXMetricManager`) is Apple-first-party,
      on-device, no third-party SDK — lowest privacy footprint but
      delayed/aggregated reports, no live dashboard.
    - Crashes + analytics with a dashboard: a third-party SDK
      (TelemetryDeck is privacy-forward and IDFA-free; Firebase
      Crashlytics / Sentry are richer but collect more).
  **Privacy coupling (important — do NOT ship blind):** any
  off-device analytics/crash SDK changes the privacy story that the
  in-flight App Store work just set to "nothing collected":
    - `PrivacyInfo.xcprivacy` — add the collected data types
      (Crash Data, Usage/Product Interaction, maybe Performance) and,
      for some SDKs, `NSPrivacyTracking`/tracking domains + their own
      bundled privacy manifests.
    - App Store Connect App Privacy nutrition label — declare the
      new collection.
    - Privacy policy — describe what's collected, why, retention,
      and the processor.
  Pick the SDK with the privacy budget in mind; MetricKit-only keeps
  the manifest nearly as clean as it is now.
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
