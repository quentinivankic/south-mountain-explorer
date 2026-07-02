# App Store Submission — TrekDex

Paste-ready drafts for the App Store Connect listing + review. Everything
here is editable copy, not code. Fill the ASC fields from this once org
enrollment (DUNS) clears. Character limits noted are Apple's.

> Status: analytics + feedback + crash capture (PostHog + MetricKit)
> **shipped**, so the App Privacy section is **Data Collection: Yes**.
> The out-of-region **waitlist** is still unbuilt — if it lands it adds
> Contact Info collection and this all gets revisited.

---

## Name & subtitle

- **App Name** (30 char max): `TrekDex`
- **Subtitle** (30 char max): `Hike & complete every trail`
  - Alternates: `Track hikes, collect trails` · `Your trail completion dex`

## Promotional text (170 char max — editable without a new build)

> Record GPS-tracked hikes, watch every trail in a park turn complete,
> and earn badges for your area. Live pace and elevation while you climb.

## Description (4000 char max)

```
TrekDex turns hiking a park into a collection you can finish.

Browse parks and preserves across the US and Canada, record your hikes
with GPS, and track your progress toward completing every trail in an
area — one trail at a time until the whole map is yours.

RECORD YOUR HIKES
• GPS-tracked recording that keeps running in the background while your
  phone's in your pocket.
• Live stats as you go: distance, duration, pace, and a live elevation
  profile so you can watch the climb happen.
• Trail mode locks onto a specific trail; roam mode just records wherever
  you wander.

COMPLETE THE MAP
• Every trail you finish turns cyan on the map. Watch a park fill in over
  time.
• Coverage tracking knows how much of each trail you've actually walked.
• A completion celebration when you finish a trail — and when you finish
  an entire area.

YOUR DEX
• A Pokédex-style achievements page for every area: first hike, first
  easy/moderate/hard trail, the every-trail crown, four seasons, distance
  milestones, and more — all earned from hikes you've already recorded.

SEE YOUR PROGRESS
• A Stats tab with lifetime totals, a hikes-per-month chart, per-area
  completion, and your full hike history.
• Every hike gets a detail page with a map of your route and an elevation
  profile.

MADE FOR REAL HIKES
• Works offline once an area is downloaded — trail data caches on device.
• Imperial or metric, your choice, everywhere.
• Export your data anytime; it's yours.

Trail data © OpenStreetMap contributors.
```

## Keywords (100 char max, comma-separated, no spaces after commas)

```
hiking,trail,hike tracker,gps,trails,national park,walk,outdoors,map,completion,elevation,pace
```
(93 chars. Don't repeat words already in the app name/subtitle — Apple
indexes those separately.)

## Category

- **Primary:** Health & Fitness (best fit for a GPS activity tracker;
  strong for search)
- **Secondary:** Navigation
  - Alternate primary if you'd rather lean discovery over fitness: Travel.

## Support & marketing URLs

- **Support URL** (required): `https://trekdex.app` (or a `/support`
  page if you add one — must be reachable and mention how to get help)
- **Marketing URL** (optional): `https://trekdex.app`
- **Privacy Policy URL** (required): `https://trekdex.app/privacy-policy`
  — MUST match the in-app link.

## What's New (version release notes, first public version)

```
First public release of TrekDex. Record GPS hikes, complete every trail
in a park, earn area badges in your Dex, and track it all in Stats.
```

---

## App Privacy ("nutrition label" — App Store Connect → App Privacy)

**Updated for PostHog analytics + in-app feedback** (both now shipping).
Hikes, GPS location, and the Sign in with Apple credential still never
leave the device. What IS collected now goes through PostHog + the
feedback form — declare **Data Collection: Yes**, then:

| Data type | Linked to identity | Used for tracking | Purpose |
|---|---|---|---|
| **Product Interaction** (usage events) | No | No | Analytics |
| **Other User Content** (feedback message) | No | No | App Functionality |
| **Email Address** (only if added to feedback) | No | No | App Functionality / Customer Support |
| **Crash Data** (MetricKit) | No | No | App Functionality |

- **Tracking:** No — no IDFA, no cross-app tracking, no data brokers.
  PostHog runs anonymous (we never call `identify`).
- Matches `PrivacyInfo.xcprivacy` (Product Interaction / Other User
  Content / Email / Crash Data declared; `NSPrivacyTracking` false).
  PostHog's SDK ships its own manifest for the identifiers/diagnostics
  it adds.

Still NOT collected (leave undeclared):
- **Location (precise):** used on-device only for recording/showing
  position — never transmitted.
- **Sign in with Apple:** the user id is stored in the device Keychain
  only; not sent anywhere.

Also update the **privacy policy** at trekdex.app: name PostHog as the
analytics processor, the **US** data region, what's collected (usage
events, feedback + optional email), and why. This is required now that
data leaves the device.

---

## Age rating questionnaire

All content questions → **None**. No objectionable content, no user-
generated content, no web access to arbitrary content, no gambling.
Expected result: **4+**.

---

## App Review — Reviewer Notes (App Store Connect → Version → Notes)

Paste this so reviewers, who test indoors with no real GPS, don't mark
recording as "non-functional":

```
TESTING GPS RECORDING WITHOUT WALKING OUTSIDE

TrekDex records hikes using GPS. Reviewers testing on a device indoors
(or in the simulator) won't move, so here's how to see recording work:

1. Simulate a route in Xcode: with the app running, Xcode > Debug >
   Simulate Location > (pick a City Run / Freeway Drive), or Simulator >
   Features > Location > City Run. The blue dot will move and the
   recording will accumulate distance, pace, and a path.
2. In the app: open any area (e.g. from the Explore or Browse tab), tap
   "Record Hike" to start a roam-mode recording, or tap a trail row's
   record action to start trail mode. Grant location permission
   ("While Using the App") when prompted.
3. Let it run ~30–60s with a simulated route so distance/pace/elevation
   populate. Tap Stop, then Stop & Save. The hike appears in the History
   list inside Stats, with a route map and elevation profile.

SIGN IN WITH APPLE + ACCOUNT DELETION
- Sign in is optional and entirely on-device (no server account).
- To delete the account: Settings > Account > Delete Account. This
  removes the local Sign in with Apple credential. Hikes/progress are
  local and can be wiped separately via Settings > Data > Reset All
  Progress.

Background location is used only during an explicit, user-started
recording session (When In Use authorization + background location) to
keep the GPS track continuous when the screen locks.

COVERAGE IS US + CANADA ONLY
Trail data currently covers the United States and Canada. If you review
from outside North America, the "nearby" lists may look empty — that's
expected, not a bug. To see full content, use Search and enter a US
park, e.g. "South Mountain, Phoenix" or "Camelback Mountain".

No account is required — all features work without signing in.
```

---

## Screenshots (you provide — device required)

Apple needs 6.9" (iPhone 17 Pro Max class) and 6.5"/6.7" sets at minimum
(App Store Connect will auto-scale down for smaller devices in most
cases; verify current requirements at upload time).

Suggested shots, in order (first 2–3 matter most — they show in search):
1. A park map with several completed (cyan) trails + the trail-list sheet.
2. Active recording: the panel showing live pace + the live elevation
   strip.
3. The Dex: a grid of earned badges for an area.
4. Stats tab: totals + the hikes-per-month chart.
5. Hike detail: route map + elevation profile.

Tip: capture on an iOS 26 simulator with a seeded set of hikes so the
Dex and Stats look populated, not empty.

---

## Export compliance

`ITSAppUsesNonExemptEncryption: false` is already set in `project.yml`'s
Info.plist — the app only uses standard HTTPS/TLS, which is exempt. So
ASC won't prompt for export-compliance docs at each upload. Nothing to do.

## Pre-submit checklist

Code side — all done:
- [x] In-app account deletion (#198)
- [x] OpenStreetMap attribution (#199)
- [x] PrivacyInfo.xcprivacy — incl. PostHog/feedback/crash types (#200, #211, #212)
- [x] Privacy Policy + Terms links → trekdex.app (#205)
- [x] Location When-In-Use only (#202) — confirmed on-device (screen-locked hike)
- [x] Export compliance key set (see above)

On you / Apple — remaining:
- [x] Privacy policy hosted + reachable (trekdex.app/privacy-policy)
- [ ] **Update privacy policy** to name PostHog + US region + collected types
- [ ] **App Privacy nutrition label** entered in ASC (table above)
- [ ] **Screenshots** captured (plan above) — often the long pole; can do pre-DUNS
- [ ] Metadata pasted into ASC (name/subtitle/desc/keywords/category/URLs)
- [ ] Reviewer notes pasted into ASC
- [ ] Crash/stability sweep on device
- [ ] **DUNS / org enrollment** — approved, ~24–48 h out (gates submission)
