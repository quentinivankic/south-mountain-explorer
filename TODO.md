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

## Shipped

Builds 1 through 19 — see `git log` for the full record. PRs are the
authoritative per-feature history.
