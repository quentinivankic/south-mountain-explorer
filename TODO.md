# TODO

Tracking work toward a polished MVP. App Store submission tasks (privacy
policy, store metadata, etc.) are intentionally not listed yet — we'll
capture those when we're ready to ship outside TestFlight.

## Verified shipped on feat/build-3-fixes

Confirmed working on device:

- B1 — Completion mismatch fixed. South Mountain shows 1/56 after the
  same-trail-twice hike instead of 2/56.
- B2 — AreaView opens on the full area extent.
- B5 / B7 / B8 — caption / card-art bg / discard double-confirm.
- Trail-id determinism + cachedAt + display-time filter +
  rebuildCoverageFromHistory + empty-cache self-heal + inline retry.
  Areas previously stuck on the empty state self-recover, areas you've
  hiked retain correct completion counts.
- Runtime-computed silhouettes — Shadow Mountain card now shows the
  real difficulty mix instead of all-green. AreaCard / ContinueCard
  art reflects current Overpass output once cache is warm.
- Trail-completion push notifications + tap-to-celebrate overlay.
- Pull-to-refresh on Explore.
- Surprise Me dice button.
- Settings → Refresh Trail Data button (re-enables 3s after refresh).
- Settings → "Your Activity" stats + Send Feedback link.
- Prefetch on Explore (instant area opens after warm-up).
- Animated trail-by-trail loading reveal (pacing tweak still pending).
- Trail-list filter menu (status / difficulty / length) — chips work,
  count badge works, "Showing X of Y" works, empty state works.
  *Caveat: section headers and map sync still open — see below.*
- AreaView layout swap — map controls above REC bar.
  *Caveat: gap still too large — see below.*
- ActiveRecordingBanner tappable, trail name in banner.
- "Record This Trail" context-menu, purple recording stroke,
  long-press tip in trail-list header.

## Still open after device test (carry into next build)

- [ ] **Trail-list filter menu has no visible section headers.**
  Wrapping each Picker in `Section("Status") { Picker(...) }`
  didn't render headers in iOS 26 — three "All" rows still stack
  with no label. Try a different structure: explicit `Text("Status")
  .font(.caption).foregroundStyle(.secondary)` rows between the
  pickers, or split the menu into nested submenus
  (`Menu("Status") { Picker(...) }`).
- [ ] **Filter trails on the map, not just in the list.** When
  Incomplete / difficulty / length filters are active, the map
  should hide non-matching trail polylines too. Currently
  TrailMapView renders all `area.trails`. Plumb the filtered set
  (or the active filters) from TrailListView → AreaView →
  TrailMapView.
- [ ] **Bottom-stack gap still too large.** Tightened from 20pt → 6pt
  but a visible space remains between the RecordingPanel and the
  trail list panel. Probably the VStack's intrinsic spacing(12) +
  controlBar's vertical padding. Either drop the VStack spacing,
  or move the RecordingPanel inline with the trail-list panel
  itself instead of in the bottom-stack.
- [ ] **Trail-list panel drag still glitchy.** Pass 5
  (geometryGroup + interactiveSpring) didn't fully fix it. Options:
  (a) accept SwiftUI's limits and rewrite as native sheet with
  `.presentationDetents([.medium, .large])`, (b) move the
  ScrollView contents into a separate view tree so layout
  invalidation stays scoped, (c) precompute the panel's frame in
  the parent and only animate `.offset(y:)` during drag.
- [ ] **Tweak the loading-state trail-reveal animation.** Pacing /
  opacity / stagger / line width — direction TBD.

## Reminders

- [ ] **Regenerate `public/areas/silhouettes.json`** by running
  `python3 scripts/build-trail-counts.py --force`. The bundled file
  is stale relative to current Overpass output. The runtime-computed
  silhouette workaround in Area.computedSilhouette covers the iOS
  app once an area is cached, but cold-load AreaCards still show
  the stale colors briefly. The script takes ~1 hour for the
  current 22-area index with Overpass rate limits, so probably
  worth its own background task or CI run.

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

## Done (older)

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
