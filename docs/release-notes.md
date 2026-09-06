# Release notes

Paste-ready "What's New" copy for App Store Connect. Apple's limit is 4,000
characters; the draft below is well inside it. Companion to
`docs/app-store-submission.md`, which holds the rest of the listing copy.

**Rule for this file:** a line appears only if the commit behind it is NOT an
ancestor of the build the previous App Store version shipped from. Checked with
`git merge-base --is-ancestor <commit> <baseline>`, not by reading dates or
commit subjects. Reverted work does not appear either — the next-turn banner
landed in #555 and was reverted in #557, and `nextTurn` appears nowhere in
`ios/SouthMountainExplorer` on `main`.

---

## Which baseline, and the one thing to confirm

Version 1.0.1 was approved around 2026-08-09. Four TestFlight builds carried
`CFBundleShortVersionString: "1.0.1"` — the version was bumped to 1.0.2 in
`815fe1207` right afterwards:

| Build | Commit | Contains |
|---|---|---|
| 261 | `24ffd7cac` | the 1.0.1 bump itself |
| 262 | `56e75553d` | + live coverage drawn as cyan (#528) |
| 263 | `e37809f5c` | + animated trail hero on the welcome page (#529, #530) |
| 264 | `013e01772` | + real park silhouette gallery on Discover (#531) |

⚠️ **Which of those four was actually submitted is only visible in App Store
Connect, and this draft assumes the last one, build 264 (`013e01772`).** That is
the conservative choice: it under-claims rather than promising users something
they already have. **If the submitted build was 261 or 262, three more items are
genuinely new and should be added back** — the cyan walked-route colour, the
animated South Mountain hero on the welcome page, and the park silhouette
gallery on Discover.

Against that baseline there are **49 app-code commits**.

---

## 1.1 — draft, not yet submitted

### Paste-ready copy

```
A big update to the area screen, to recording, and to your first run through
the app.

THE AREA SCREEN IS REBUILT
• Areas open full screen instead of as a half sheet.
• The bottom panel is only as tall as what it is showing, so a selected trail
  or a hike in progress fits without being cut off.
• Swipe between Record, Trails and Dex instead of tapping a segmented control.
• The search field stays where it is — it no longer slides away or slips
  behind the header.
• Sort trails by nearest to you, shortest, longest, most progress, or A–Z.

YOUR HIKE GETS ITS OWN PAGE
• Recording has its own page now, with the map controls beside it.
• Two live estimates while you walk: when you will finish the trail, and when
  you will be back where you started.
• Tapping Record on a different trail no longer discards the hike you are on.
• Trails you finish mid-hike now count in neighbouring areas you cross into.

ON THE MAP
• Your walked route follows the trail's own shape instead of drifting off it.
• Your dot always shows which way you are facing, not only when the map is
  following your heading.

ELEVATION PROFILES
• Charts now open from the trailhead car park rather than from wherever you
  happen to be standing, so a climb reads as a climb when you plan at home.

STATS
• Insights moved into Stats, so it is all in one place.
• Recent Hikes lists the newest hike first.

FIRST LAUNCH
• The welcome walkthrough was being skipped on new installs. Fixed.
• It now ends by explaining what location is for, before asking for it.

ALSO
• Browse scrolls faster.
• A failed export says so instead of going quiet.
• Elevation badges no longer claim a climb they cannot back up.
```

### What each line rests on

| Note | Commit | Also verified |
|---|---|---|
| Areas open full screen | `af82bae2a` #538 | |
| Panel sized to its content | `2cac4d426` #552 | the three stops themselves are NOT new — `013e01772` already had `.height(150)` / `.fraction(0.5)` / `.large` |
| Swipe Record / Trails / Dex | `6177cc535` #542 removed the segmented control; `51a1b3b70` #573 added the Record page | `AreaSheetTab` predates the history floor, so only the swipe and the Record page are new |
| Search field stays put | `1cc53e921` #580 | asserted in `AreaSheetAuditTests` |
| Six sort orders | `83ba385bc` #543 | `TrailSort`: default, nearest, shortest, longest, progress, A–Z |
| Camera controls on the hike page | `6bb44dcd1` #575 | |
| Finish / Back estimates | `3e1d59b74` #566 | `RecordingPanel.estimatesLine` |
| Record on another trail is safe | `dfff52ebc` #571 | |
| Completions in crossed areas | `295778525` #535 | |
| Walked route follows the trail | `99e775f48` #534 | the CYAN colour is `56e75553d` #528 and is already in 1.0.1 — not claimed |
| Facing cone always drawn | `fbed080a0` #548, `76b0d6642` #549 | `MapKitMapView.userHeading` |
| Chart opens from the trailhead | `92b81e1ca` #562 | parking now outranks the user's position; the nearest-end rule (#447) and the Flip button (`d1ae919c4` #462, 2026-07-19) both shipped earlier and are NOT claimed |
| Insights inside Stats | `9c2538bfb` #550 | `Views/Stats/` holds only `StatsView.swift` |
| Recent Hikes newest first | `58d76680c` #572 | |
| First launch fixed | `8e8f1ac65` #584 | run 33512537301, `sawLocationPrompt=true`, test passed |
| Browse faster | `2783180bc` #536 | |
| Export failure surfaced | `59e24e347` #553 | `exportError` in `SettingsView.swift` |
| Gain badge honesty | `59e24e347` #553 | |

### Cut after checking, and why

- **Live coverage drawn in cyan** (`56e75553d` #528) — ancestor of `013e01772`.
  Shipped in 1.0.1.
- **Flip button on the elevation chart** (`d1ae919c4` #462, 2026-07-19) — older
  than v1.0 itself.
- **"One tap opens a hike"** (`d38546e2c` #539) — the long-press regression it
  fixed was introduced and repaired inside this cycle. `013e01772` has no
  `simultaneousGesture` in `StatsView.swift`, so nobody on 1.0.1 ever saw it.
- **Discover gallery coloured by difficulty** (`6ba2a300e` #532) — genuinely new,
  but too granular for release copy. Add if the notes need padding.

### Version number

`ios/project.yml` reads `CFBundleShortVersionString: "1.0.2"`, the TestFlight
train for builds 266–302.

- **Submit as 1.0.2** using build 302, which is verified on device. Nothing to
  rebuild.
- **Submit as 1.1** — more honest for 49 commits of feature work, at the cost of
  one more TestFlight build carrying only a changed version string.
