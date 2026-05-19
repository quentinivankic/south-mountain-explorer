# TODO

Long-running tracker of shipped work + open items. Live "what's in the
current build" planning lives in `~/.claude/plans/binary-hatching-
toucan.md`; this file is the historical record.

App Store submission tasks (privacy policy, store metadata, etc.) are
intentionally not listed yet — capture those when shipping outside
TestFlight.

## Still open

- [ ] **Trail-list panel drag still glitchy.** Build 3 didn't
  rewrite this. Pass 5 added `.geometryGroup()` + `.interactiveSpring`
  + `animation(nil, value: trailListHeight)` to scope layout
  invalidation, which helped but didn't fully eliminate stutter.
  Options when picked up: (a) accept SwiftUI's limits and rewrite
  as native sheet with `.presentationDetents([.medium, .large])`;
  (b) move the ScrollView contents into a separate view tree so
  layout invalidation stays scoped; (c) precompute the panel's
  frame in the parent and render it at full `tallHeight` always,
  only animating `.offset(y:)` during drag so layout never changes.

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
