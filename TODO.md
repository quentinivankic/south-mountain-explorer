# TODO

Tracking work toward a polished MVP. App Store submission tasks (privacy
policy, store metadata, etc.) are intentionally not listed yet — we'll
capture those when we're ready to ship outside TestFlight.

## On feat/build-3-fixes (awaiting next TestFlight upload)

Bug fixes from the first device test of the merged main:

- B1 — Completion mismatch after Refresh Trail Data. Display now
  filters by current-trail IDs; AreaView re-derives completions from
  hike history on load via ProgressService.bulkMarkComplete.
- B2 — AreaView opens on a fragment of the area. Initial map region
  computed from union of trail-segment coordinates.
- B3 — Trail-list panel drag jankiness. Pass 5: fixed-height layout +
  .geometryGroup() + .interactiveSpring snap.
- B4 — Trail-list filter menu: status / difficulty / length, single-
  select per dimension, sectioned with explicit headers, count badge
  on the filter button, "Showing X of Y" line, dedicated empty state.
- B5 — Length-filter chips clarified with caption.
- B7 — Card-art black bg in light mode → secondarySystemBackground
  with a .separator border.
- B8 — Stop & Discard with double-confirm dialog.

Trail recording / completion accuracy:

- Trail-id determinism. Sorted byName.keys before assigning the
  count-suffix in AreaDataService so the same trail always gets the
  same ID across fetches. Was scrambling completions when an area
  silently re-fetched.
- cachedAt = Date() stamped on fresh fetches so the staleness check
  actually works (was nil → ∞ → silent re-fetch on every open).
- Display-time filtering on completion counts (AreaCard, ContinueCard,
  TrailListView header, AreaView celebration trigger) so orphan
  completions don't inflate.
- rebuildCoverageFromHistory replays saved GPS paths against current
  trails on AreaView load → completions self-heal under id rotation.
- Empty-cache self-heal: 0-trail entries treated as cache misses on
  subsequent opens. Defensive guard in fetchAndCacheAreaWithError
  prevents overwriting a good cache with empty data on a flaky fetch.
- Inline retry (3 attempts, 600/1200ms backoff) when Overpass returns
  zero trails and there's no prior cache.

Recording / activity:

- ActiveRecordingBanner tappable → jumps back to recording's AreaView.
- Trail name in banner when in .trail mode.
- "Record This Trail" context-menu on trail rows; SavedRecording
  grew an optional trailId field. HistoryView shows trail name as
  row title for trail-mode hikes.
- TrailMapView paints purple over the trail being recorded.
- Trail-list summary tip: "Tap to highlight · long-press to record".
- Trail-completion push notifications via NotificationService (local
  notifications, no APNs). Permission requested at first hike start.
- Tap-to-celebrate overlay (checkmark seal, bounce, success haptic,
  tap or 3.5s to dismiss).
- AreaView layout: controlBar above RecordingPanel, gap tightened to
  6pt so the recording bar sits closer to the trail list panel.

UX polish:

- Pull-to-refresh on Explore.
- "Surprise Me" dice toolbar button — random unvisited area.
- Prefetch visible AreaCards on Explore appear / location / filter
  change. Combined with the on-disk Area cache, area opens are now
  instant after first warm-up.
- AreaCard.style param dropped — only .glow ever used.
- Settings → Refresh Trail Data button re-enables 3s after refresh.
- Settings → "Your Activity" vanity stats block.
- Settings → "Send Feedback" link.
- AreaView loading state paints the bundled silhouette behind a
  "Loading…" pill, with trails lighting up sequentially via
  TimelineView + Path.trimmedPath. Stagger auto-scales to trail
  count so 5-trail and 200-trail areas both finish in ~2.5s.
- Runtime-computed silhouettes: Area.computedSilhouette builds the
  silhouette from live trail data so AreaCard / ContinueCard art
  reflects current Overpass output (fixes Shadow Mountain rendering
  as all-easy when it actually has a mix).

## Reminders

- [ ] **Regenerate `public/areas/silhouettes.json`** by running
  `python3 scripts/build-trail-counts.py --force`. The bundled file
  is stale relative to current Overpass output (Shadow Mountain
  Preserve was the prompt — bundle had it as all-easy, real data
  has a mix). The runtime-computed silhouette workaround in
  Area.computedSilhouette covers the iOS app once an area is
  cached, but cold-load AreaCards still show the stale colors
  briefly. The script takes ~1 hour for the current 22-area index
  with Overpass rate limits, so probably worth its own background
  task or CI run.
- [ ] **Tweak the loading-state trail-reveal animation.** Direction
  TBD on device — pacing / opacity / stagger / line width.

## Next-build candidates (not yet started)

- [ ] iPad layout sanity-check.
- [ ] Route type filter for trails (out-and-back, loop, etc.) — not
  in the trail model yet, would need to compute from GPS shape or
  pull from OSM `route` / `network` tags.
- [ ] Drive-time bands instead of fixed-mile ranges ("Within 30 min",
  "Within 1 hr") — better answer to the original B5 if the chip
  caption isn't enough. Needs MapKit routing per area on a
  background queue with caching.

## Backlog / ideas

- [ ] Featured area of the week (auto-rotate by ISO week).
- [ ] Map-tile prefetch for offline hiking — record in spotty signal
  without losing the basemap.
- [ ] Ad-Hoc + Diawi distribution pipeline (only if the TestFlight
  cycle becomes a real bottleneck).

## Done (recent)

- [x] First-run onboarding screen — #39 (Get Started bug fixed in #44)
- [x] Haptic feedback on trail-complete — #39
- [x] "Area 100% complete" celebration — #39
- [x] Resolve trail names in Recording Summary — #39
- [x] Live coverage updates during recording — #40
- [x] Cumulative GPS coverage halo on area map — #40
- [x] Per-area difficulty mix bar — #43
- [x] Share a completed hike (map snapshot + stats card) — #42 + #43
- [x] Recenter-on-user button — #42
- [x] Concurrent recording prevention — #44
- [x] Far-from-area warning before recording — #44
- [x] Global active-recording banner across tabs — #44
- [x] Trail list stays interactable during recording — #44
- [x] Light/dark/match-phone theme toggle — #41
