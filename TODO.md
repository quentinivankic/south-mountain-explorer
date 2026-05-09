# TODO

Tracking work toward a polished MVP. App Store submission tasks (privacy
policy, store metadata, etc.) are intentionally not listed yet — we'll
capture those when we're ready to ship outside TestFlight.

## On TestFlight (build/rc, awaiting field test)

The current TestFlight build bundles PRs #37 → #44 + #41:

- #37 — Explore discovery + AreaView UX (silhouettes, Try Something
  New, length chips, All Areas Map, drag panel, tap-to-highlight,
  cyan complete color, phantom button gone)
- #38 — Wire Browse All Areas button, geographic-honesty note, History
  detail view, hide empty areas, stop-recording confirmation
- #39 — First-run onboarding, completion haptics, 100% celebration,
  trail-name resolution in Recording Summary
- #40 — Live coverage updates during recording + richer post-hike
  summary (cumulative area %, partial-coverage trails)
- #41 — Light/dark/match-phone theme toggle in Settings
- #42 — Recenter button + shareable hike card v1
- #43 — Difficulty mix bar on AreaCard + map-snapshot share card
- #44 — Concurrent recording prevention, far-from-area warning, global
  active-recording banner, map/list toggle stays visible during
  recording, onboarding "Get Started" hot-fix

## Bugs from device test (priority for next build)

- [ ] **B1** Trail completion count mismatch — Explore card shows
  "1 trail completed" but the trail list inside the area shows nothing
  checked off. Possibly tied to "Refresh Trail Data" in Settings —
  refresh may be clearing per-trail completions while leaving the area
  count intact (or vice versa).
- [ ] **B2** AreaView opens with the map zoomed into a random subregion
  instead of fitting the whole area. `centerOnArea()` either has stale
  bbox data or the camera animation lands on a fallback before the
  bbox-derived region applies.
- [ ] **B3** Trail-list panel drag is janky — works but feels glitchy
  during the swipe. Likely the `dragOffset` + `trailListHeight` math
  fighting the snap animation, or GeometryReader recomputing per-frame.
- [ ] **B4** Add filters to the trail list: completed/incomplete,
  difficulty, length, route type (out-and-back, loop, etc.). Route type
  isn't currently in the trail model; would need to either compute from
  the GPS shape or pull from OSM `route` / `network` tags.
- [ ] **B5** Length filter chips on Explore are ambiguous — testers
  assume they mean distance from the user, not total trail miles inside
  the area. Either rename ("Total miles" vs "Drive time") or restructure
  to actually use distance from user. (Pairs with the "drive-time
  bands" backlog idea.)
- [ ] **B6** Appearance picker in Settings has no effect — caused by
  ContentView force-pushes overwriting the `.preferredColorScheme`
  modifier. Restored on build/rc; verify in next build.
- [ ] **B7** Card art glow + harsh black background looks bad. Try
  a softer dark backdrop (charcoal, gradient, or the area's own colour)
  and tone down or rethink the glow effect.
- [ ] **B8** Stop-recording dialog needs a "Stop & Discard" option in
  addition to "Stop & Save". Discard should ask for confirmation again
  ("Discard hike? This can't be undone").

## Polish (post-MVP)

- [ ] iPad layout sanity-check
- [ ] Pick a single card-art style and drop the alternating treatment
  once you've decided between `.tight` and `.glow`
- [ ] Concurrency warnings cleanup (RecordingPanel timer,
  ActiveRecordingBanner timer, AuthService scene access — all warnings
  today, will become errors under stricter Swift 6 modes)
- [ ] Ad-Hoc + Diawi distribution pipeline (only if TestFlight cycle
  becomes a bottleneck)

## Backlog / ideas

- [ ] Drive-time bands instead of fixed-mile ranges ("Within 30 min",
  "Within 1 hr") — almost certainly the right answer to B5
- [ ] "Surprise Me" button — pick a random unvisited area
- [ ] Featured area of the week (auto-rotate by ISO week)
- [ ] Map-tile prefetch for offline hiking — record in spotty signal
  without losing the basemap

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
