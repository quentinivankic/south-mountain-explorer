# TODO

Tracking work toward a polished MVP. App Store submission tasks (privacy
policy, store metadata, etc.) are intentionally not listed yet — we'll
capture those when we're ready to ship outside TestFlight.

## On feat/build-3-fixes (PR #46, awaiting next TestFlight upload)

Bug fixes from the first device test of the merged main:

- B1 — Completion mismatch after Refresh Trail Data. Display now
  filters by current-trail IDs; AreaView re-derives completions from
  hike history on load via ProgressService.bulkMarkComplete (silent,
  idempotent). Refresh dialog rewritten with explicit messaging.
- B2 — AreaView opens on a fragment of the area. Initial map region
  now computed from union of trail-segment coordinates and seeded
  synchronously in init, falling back to area.bbox then to a camera.
- B3 — Trail-list panel drag jankiness. Switched to fixed-height +
  .offset(y:) layout with .animation(nil, value:) so SwiftUI treats
  the drag as a pure visual translation (no per-frame layout pass).
- B5 — Length-filter chips ambiguous. Added "Filter by total trail
  miles in the area" caption above the chips.
- B7 — Card-art black bg persists in light mode. AreaCard +
  ContinueCard hero now use Color(.secondarySystemBackground) with
  a .separator border; text flipped to .primary / .secondary so it
  adapts.
- B8 — Stop & Discard option with double-confirm dialog, in both
  RecordingPanel and the global ActiveRecordingBanner.

Plus:

- ActiveRecordingBanner is tappable → fullScreenCover hosts the
  recording's AreaView so users mid-hike can jump back to the map
  without manual navigation.
- Settings → Refresh Trail Data button re-enables 3s after a refresh
  instead of staying disabled until app relaunch.
- Timer / @MainActor concurrency warnings cleaned up in
  ActiveRecordingBanner and RecordingPanel.
- Settings → "Your Activity" vanity stats block: hikes / miles /
  trails / areas, deduped across history + ProgressService.
- Settings → "Send Feedback" link to the GitHub issues page.
- "Record This Trail" context-menu action on trail rows starts a
  recording in .trail mode for that specific trail. SavedRecording
  grew an optional trailId field with a custom Codable init so old
  hikes still decode. HistoryView surfaces the trail name as the
  row title for trail-mode hikes.
- TrailMapView paints a dashed cyan stroke over the trail being
  recorded so the user can see at a glance which polyline to follow.
- Trail-list summary header now has a "Tap to highlight · long-press
  to record" tip so the context-menu action is discoverable.
- Prefetch visible AreaCard areas on Explore appear / location /
  filter change. Combined with the existing on-disk area cache, area
  opens are now instant after the first warm-up.
- B4 — Trail-list filter menu: status (all / incomplete / complete),
  difficulty (all / easy / moderate / hard), length (all / <1mi /
  1–3mi / >3mi). Live count badge on the filter button + "Showing X
  of Y" line in the header.
- AreaCard.style param dropped — only `.glow` was ever used and the
  blur halo is already gated to dark mode inside SilhouetteArtwork,
  so the `.tight` branch never rendered differently. Cleaner API.

## Next-build candidates (not yet started)

- [ ] iPad layout sanity-check.
- [ ] Route type filter for trails (out-and-back, loop, etc.) — not
  in the trail model yet, would need to compute from GPS shape or
  pull from OSM `route` / `network` tags.

## Backlog / ideas

- [ ] Drive-time bands instead of fixed-mile ranges ("Within 30 min",
  "Within 1 hr") — likely the right answer to the original B5 if
  the caption isn't enough.
- [ ] "Surprise Me" button — pick a random unvisited area.
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
