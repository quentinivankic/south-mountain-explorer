# TODO

Long-running tracker of shipped work + open items. Live "what's in the
current build" planning lives in `~/.claude/plans/binary-hatching-
toucan.md`; this file is the historical record.

App Store submission tasks (privacy policy, store metadata, etc.) are
intentionally not listed yet — capture those when shipping outside
TestFlight.

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
- [ ] **Live elevation profile during recording.** Elevation is
  computed post-hike; show it live in the recording panel so the user
  sees climbing in progress.
- [ ] **Pace / speed stats.** Distance + time are tracked; pace is
  not. Cheap addition on hike detail + Stats.
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
- [ ] **Onboarding refresh.** Single fullScreenCover today; showcase
  the new Stats dashboard + native sheet now that they exist.
- [ ] **Share card expansion.** `ShareableHikeCard` exists; grow it
  into a proper "I completed X" share-out (especially good with the
  Dex once that lands).
- [ ] iPad layout sanity-check.
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
