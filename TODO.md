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
  contributors" in Settings → About (links to the ODbL page) + a
  caption in the area sheet where the trails render.
- [x] **Privacy manifest (`PrivacyInfo.xcprivacy`)** — Done in #200.
  Declares UserDefaults (CA92.1) only; `NSPrivacyTracking` false,
  `NSPrivacyCollectedDataTypes` empty (nothing leaves the device).
- [x] **Privacy policy URL** — Hosted at trekdex.app/privacy-policy;
  in-app link updated + Terms of Service added in #205.
- [ ] **App Privacy "nutrition label"** — Drafted in
  `docs/app-store-submission.md` ("Data Not Collected", matching the
  manifest). Still needs to be ENTERED in App Store Connect (you).
- [ ] **App Store metadata package** — Text (description, subtitle,
  keywords, category, URLs, age rating) drafted in
  `docs/app-store-submission.md`. Still TODO: **screenshots** (device),
  and pasting it all into ASC.
- [ ] **DUNS / organization enrollment** — Gates submission itself.
  In progress (waiting on the DUNS number).

> ⚠️ The privacy manifest / nutrition-label / policy items above are
> written against today's reality: **nothing is collected off-device**
> (no backend, no analytics). Two planned features would change that
> and force all three to be revised — the **out-of-region waitlist**
> (collects email → Contact Info) and **analytics + crash reporting**
> (Crash/Usage data). If either lands before or after store submission,
> update the manifest, the ASC nutrition label, and the privacy policy
> together. See the Backlog entries for both.

### Strongly advised (rejection-likely or bad first impression)

- [~] **"Always" location audit** — Code done in **PR #202 (open)**:
  confirmed the app never actually needed Always (no geofencing /
  significant-change relaunch; `requestAlwaysAuthorization` was
  defined-but-unused), dropped the Always usage string, When-In-Use +
  `UIBackgroundModes: [location]` covers background recording. HELD
  from merge until an on-device backgrounded hike confirms the GPS
  track stays continuous. Merge #202 after that.
- [x] **App Review reviewer notes** — Drafted in
  `docs/app-store-submission.md` (how to simulate a GPS hike via Xcode
  Simulate Location, roam vs trail, SiwA + account-deletion path).
  Paste into ASC at submission.
- [ ] **Stability / crash pass** — Broad device QA before submit.
  Drag jank is fixed, but App Review rejects crashy apps; do a sweep
  across a current iPhone + an older model if possible.

## Backlog — features

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

## Waiting on you (not code)

Everything below is external / on-device and can't be finished in the
repo:

- [ ] Enter the App Privacy nutrition label in App Store Connect (draft
  in `docs/app-store-submission.md`).
- [ ] Capture App Store screenshots on a device/simulator (plan in the
  same doc).
- [ ] Merge PR #202 after an on-device backgrounded-hike test.
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
- **App Store submission prep.** Account deletion (#198), OSM
  attribution (#199), privacy manifest (#200), trekdex.app legal
  links + Terms (#205), and the `docs/app-store-submission.md`
  metadata/reviewer-notes package (#206). Location When-In-Use
  downgrade is staged in #202 (see the gate section). See "Waiting on
  you" for the remaining external steps.
- **CI infra hardening.** Fixed the macos-latest runner rolls:
  download the iOS platform before build un-sudo'd with retry (#197,
  #204), and resolve the test simulator UDID dynamically instead of
  pinning a device name (#201). Serialized `DataBackupManagerTests`
  to kill a filesystem race (#204).
