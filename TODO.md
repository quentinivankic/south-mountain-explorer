# TODO

Tracking work toward a TestFlight-shareable MVP and beyond. App Store
submission tasks (privacy policy, store metadata, etc.) are intentionally
not listed yet — we'll capture those when we're ready to ship outside
TestFlight.

## In flight (current draft PRs)

- **#37** `feat: Explore discovery + AreaView UX` — silhouette card art
  fixes from earlier plus Explore-tab Try Something New, length chips,
  All Areas Map, AreaView UX (drag panel, tap-to-highlight, distinct
  complete color, phantom button gone). Awaiting TestFlight verification.

## MVP-feel fixes (this branch: `feat/mvp-feel-fixes`)

- [ ] **#6** Wire or remove the dead "Browse All Areas" button in the
  Explore empty state.
- [ ] **#7** Geographic honesty — when the nearest area is far away,
  surface a coverage note ("Phoenix, AZ + Fredericia, Denmark for now").
- [ ] **#8** Tapping a past hike in History should open a detail view
  with the GPS path on a map, stats, and any newly completed trails.
- [ ] **#9** Hide the 4 areas with zero trail data (Powers Butte, Robbins
  Butte, Scarlett Canyon, Base & Meridian) so they don't show as broken
  cards. Re-enable when their trail data lands.
- [ ] **#10** Confirmation dialog on the Recording panel's stop button so
  an accidental tap doesn't kill an in-progress hike.

## Polish (post-MVP, no rush)

- [ ] First-run onboarding screen ("Find every trail. Record hikes.
  Watch the map fill in.")
- [ ] Haptic feedback on trail-complete
- [ ] "Area 100% complete" celebration moment (confetti, badge, etc.)
- [ ] Share a completed hike (PNG of map + area link)
- [ ] iPad layout sanity-check
- [ ] Pick a single card-art style and drop the alternating treatment
  once you've decided between `.tight` and `.glow`

## Backlog / ideas

- [ ] Drive-time bands instead of the fixed-mile ranges ("Within 30 min",
  "Within 1 hr") — more useful than haversine miles.
- [ ] "Surprise Me" button — pick a random unvisited area.
- [ ] Featured area of the week (auto-rotate by ISO week).
- [ ] Per-area difficulty mix shown on the card (e.g., 60% easy, 30%
  moderate, 10% hard).
- [ ] Map-tile prefetch for offline hiking — would let people record in
  spotty signal without losing the basemap.
- [ ] Resolve trail names in the Recording Summary (currently shows raw
  trail IDs in the "New Completions" list).
