# TODO

Long-running tracker of shipped work + open items. Live "what's in the
current build" planning lives in `~/.claude/plans/binary-hatching-
toucan.md`; this file is the historical record.

App Store submission tasks (privacy policy, store metadata, etc.) are
intentionally not listed yet — capture those when shipping outside
TestFlight.

## Still open

- [ ] **Trail-list filter menu has no visible section headers.**
  Wrapping each Picker in `Section("Status") { Picker(...) }` didn't
  render headers in iOS 26 — three "All" rows still stack with no
  label. Try a different structure: explicit
  `Text("Status").font(.caption).foregroundStyle(.secondary)` rows
  between the pickers, or split the menu into nested submenus
  (`Menu("Status") { Picker(...) }`).
- [ ] **Bottom-stack gap.** Tightened from 20pt → 6pt during build 3
  but visible space remains between RecordingPanel and the trail
  list panel. Probably the VStack's intrinsic spacing(12) +
  controlBar's vertical padding. Either drop the VStack spacing
  or fold the RecordingPanel inline with the trail-list panel.
- [ ] **Trail-list panel drag still glitchy.** Build 3 didn't
  rewrite this. Options when picked up: (a) accept SwiftUI's limits
  and rewrite as native sheet with
  `.presentationDetents([.medium, .large])`; (b) move the
  ScrollView contents into a separate view tree so layout
  invalidation stays scoped; (c) precompute the panel's frame in
  the parent and only animate `.offset(y:)` during drag.

## Backlog / ideas

- [ ] iPad layout sanity-check.
- [ ] Featured area of the week (auto-rotate by ISO week).
- [ ] Map-tile prefetch for offline hiking — record in spotty signal
  without losing the basemap.
- [ ] Ad-Hoc + Diawi distribution pipeline (only if the TestFlight
  cycle becomes a real bottleneck).

## Shipped — build 3 (2026-05-12)

Major scope (rolled up in PR #53):

- CDN-hosted per-area trail geometry via jsDelivr (
  `public/areas/geom/<id>.json`) — iOS fetches there instead of live
  Overpass. Counts match Browse exactly.
- Area index seeded from OSM with quality filters (511 areas across
  AZ + CA).
- Trail-id determinism end-to-end so completion records survive
  upstream re-fetches.
- Loop classification fix (closed-segment shortcut dropped, length /
  span ratio raised 2.2 → 3.0).
- Offline prefetch — cold-launch download of favorites + 10 most-
  recently-opened areas. Settings "Download for Offline" button.
- Step 4 nearby-radius prefetch (50 mi radius, 25 mi movement
  threshold, Wi-Fi-only by default). `NetworkService` wrapping
  `NWPathMonitor`. Manual "Download Nearby Areas" button with
  cellular confirmation dialog.
- Manage Downloads — per-area swipe-to-delete + Clear All.
- Endpoint-gated trail completion — celebration requires both
  polyline endpoints within 30 m of the recorded path, not just
  90% coverage.
- "Stop & Discard" option in the recording stop dialog.
- Recenter centers user in visible map (camera shifts south by half
  the bottom inset, scaled against the shorter screen axis).
- Cyan coverage halo clipped to on-trail GPS points only.
- Dimmed non-active trail opacity bumped 0.25 → 0.5 for legibility.
- Drive-time filter on Browse + route-type filter (loop / linear)
  on the trail list.
- Loading state: pill removed, silhouette reveal locked to 2.0 s,
  post-reveal sine wave so trails sway gently if data is slow.
- Trail-list filter chips, count badge, "Showing X of Y", empty
  state, map sync via `routeFilter` + `visibleTrailIds`.
- ActiveRecordingBanner tappable, trail name in banner.
- "Record This Trail" context-menu, purple recording stroke,
  long-press tip in trail-list header.
- Settings: "Refresh Trail Data" button, "Your Activity" stats,
  Send Feedback link.
- Pull-to-refresh + Surprise Me dice on Explore.
- Animated trail-by-trail loading reveal.
- Original device-test fixes B1 (completion mismatch), B2 (full
  area on open), B5 / B7 / B8 (caption, card-art bg, discard
  confirm).
- Concurrency cleanup — Swift 6 strict-concurrency throughout,
  `NotificationService` MainActor patterns, prefetch progress UI
  fixed to land writes on MainActor with `Task.yield()` between
  items.
- Build 3 cleanup PRs (#55–#57): `SpatialGrid` utility extracted,
  `StorageKeys` centralized, timing constants named.

## Shipped — earlier rollups

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
