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

## 1.2 (draft, TestFlight; not submitted to the App Store yet)
Baseline: App Store 1.1 is live. The old 1.1 draft that used to be here is
superseded; it described the area-screen rebuild and other work that has
already shipped. Source for this list:
`.agents/tasks/changes-since-appstore-1.1/report.md` section 5. Trim any
bullet not confirmed on a real hike (turn cues, Dynamic Island, notifications)
before pasting into App Store Connect. Do not say Apple maps work offline:
base-map tiles are not downloaded.
```
Hike with your phone locked, and see your hike on the Lock Screen.

NEW
- Lock Screen and Dynamic Island: a live card while you record shows your time, distance and status. Tap it to jump back into your hike.
- Turn cues: TrekDex warns you before forks and turns with a spoken cue, a haptic tap and a banner. It also tells you when you drift off the trail and when you are back on. Turn voice and haptics on or off in Settings > Turn Cues.
- Optional locked-screen alerts: switch on "Notify when locked" to get each cue as a notification in your pocket.
- Download by state or country: Settings > Offline Trails > Download by State or Country saves every trail in a state (or all of the US) so areas open without service. Resumable, verified, and easy to remove.
- Better Remaining and Back estimates: Remaining is the time left to finish the trail you picked. Back is the time to retrace to where you started. Both adapt to climbs, rests and loops.
- Denmark: the first trails outside North America.
- Surface Duo and wide screens: the area menu moves to a resizable side panel next to the map.
IMPROVED
- The area screen is simpler. The hidden swipe pages are gone: one sheet adapts to what you are doing, the map always stays visible, and your trail list is reachable mid-hike. Badges live behind a Collection button.
- Areas open centered and fade in from an animated outline.
- Explore no longer shows a stale "last location" notice.
- Larger text and VoiceOver work better across Explore, the area sheet and recording.
- With large text sizes, the trail list now scrolls smoothly, so a tall trail row can be scrolled fully into view.
- Removing a state's downloads now reports correctly how many saved or opened areas were kept.
- Settings: Offline Trails shows what is saved and verified.
SAFER
- A failed save no longer loses your hike, and a recording that cannot be recovered says so.
- GPS gaps no longer draw a straight line or add distance.
- GPS signal loss now shows as paused promptly.
- Deleting hikes and loading trail links are more careful.
- Backup import is removed; export is unchanged. TrekDex no longer uploads diagnostics automatically.
```
