# Release notes

Paste-ready "What's New" copy for App Store Connect. Apple's limit is 4,000
characters; each draft below is well inside it. Companion to
`docs/app-store-submission.md`, which holds the rest of the listing copy.

**Rule for this file:** every line describes something verified present in the
shipped source, not something a commit subject claimed. Reverted work does not
appear — the next-turn banner landed and was reverted, and `nextTurn` is absent
from `ios/SouthMountainExplorer` on `main`, so it is not listed.

---

## 1.1 — draft, not yet submitted

Covers 53 app-code commits merged since 1.0.1 was approved on 2026-08-09.

### Paste-ready copy

```
A big update to the area screen, to recording, and to your first run through
the app.

THE AREA SCREEN IS REBUILT
• Areas open full screen instead of as a half sheet.
• The bottom panel has three sizes and is only as tall as what it is showing,
  so nothing gets cut off.
• Swipe between Record, Trails and Dex.
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
• Where you have walked draws as the trail turning cyan, following the trail's
  own shape instead of a stripe laid over it.
• Your dot always shows which way you are facing, not only when the map is
  following your heading.

ELEVATION PROFILES
• Charts orient from whichever end of the trail is nearer you, so a climb
  reads as a climb.
• A Flip button on any chart if you would rather read it the other way.

STATS
• Insights moved into Stats, so it is all in one place.
• Recent Hikes is newest first, and one tap opens a hike.

FIRST LAUNCH
• The welcome walkthrough was being skipped on new installs. Fixed.
• It now ends by explaining what location is for, before asking for it.

ALSO
• Browse scrolls faster.
• A failed export says so instead of going quiet.
• Elevation badges no longer claim a climb they cannot back up.
```

### What each line rests on

| Note | Verified by |
|---|---|
| Areas open full screen | #538 |
| Three sheet sizes, sized to content | #552, #563, #574; photographed in runs 32206102097 / 32207386856 |
| Nothing gets cut off | #577–#582; the SwiftUI pager replaced `TabView(.page)` after two stale-frame bugs |
| Swipe Record / Trails / Dex | `AreaSheetTab` cases in `AreaView.swift` |
| Search field stays put | #580; asserted in `AreaSheetAuditTests` |
| Six sort orders | `TrailSort` in `TrailListView.swift`: default, nearest, shortest, longest, progress, A–Z |
| Hike has its own page | #573; camera controls moved there in #575 |
| Finish / Back estimates | `RecordingPanel.estimatesLine` — `Label("Finish …")`, `Label("Back …")` |
| Record on another trail is safe | #571 |
| Mid-hike completions in crossed areas | #535 |
| Cyan walked route, snapped to geometry | #528, #534 |
| Facing cone always drawn | `MapKitMapView.userHeading`; #548, #549 |
| Chart orientation + Flip | `TrailProfile.startIsNearer` (#447), `TrailElevationProfileView.onFlip` |
| Insights inside Stats | #550; `Views/Stats/` contains only `StatsView.swift` |
| Recent Hikes order and tap | #572, #539 |
| First launch fixed | #584; run 33512537301, `sawLocationPrompt=true`, test passed |
| Browse faster | #536 |
| Export failure surfaced | `exportError` in `SettingsView.swift` (#553) |
| Gain badge honesty | #553 |

### Version number

`ios/project.yml` currently reads `CFBundleShortVersionString: "1.0.2"`, which
is the TestFlight train (builds 267–302). Two options:

- **Submit as 1.0.2** using build 302, which is already verified on device.
  Nothing to rebuild.
- **Submit as 1.1** — more honest for a release this size, and the cost is one
  more TestFlight build carrying only a changed version string.

If 1.1 is chosen, bump `project.yml`, cut a build, and retitle this section.
