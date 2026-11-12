# Closed tasks (archived from TASKS.md)

Moved word for word from `TASKS.md` on 2026-11-12 (tech-debt-speed D8). Each
task keeps its anchor and heading in `TASKS.md` with a pointer here.

<a name="44"></a>
## #44 — CLOSED 2026-07-29. Pool emitted before ownership, rolled nationally.

Shipped in three PRs and one roll:

- **#508** — `add-parking.py --pool-sidecar` emits the road-gated federal points
  between `road_gate` and `assign_federal`; `build-parking-pool.py --extra` folds
  them in; `merge-parking-pool-sidecars.py` fans in the per-state artifacts.
- **#509** — the roll runs off the homelab's extracts. Overpass was 87% of a
  state's runtime (VT) and 71% (CO), and the road gate alone was 34.7 s / 175.6 s
  of that. `build-local-osm-cache.py` builds parking + road-gate caches; ONE
  network call remains (ArcGIS, which has no local copy). VT went 5 min -> 34 s.
- **#510** — boundaries stay on Overpass. The local rings are coarser (Four Peaks
  4,815 points vs 6,595 over the same bbox) and a cut corner dropped Cane Spring
  Trailhead, 7.6 m from a trail, clearing 8 Arizona wilderness areas. Boundaries
  were also the CHEAPEST call (2.4-6.1 s), so moving them bought nothing.
- **#511** — the national roll, five parallel shards on the homelab, 51 states,
  no 504s and no skipped states.

⭐ **RESULT: the R2 pool went 29,196 -> 30,840 lots (+1,661 pre-ownership).**
Oregon 708, Washington 464, Idaho 349, Colorado 292, Montana 235, California 228
— national forest wrapped around wilderness, exactly the case ownership could
never resolve. geom: added 50, updated 607, cleared 13. Every named casualty
this task was opened for is now in the pool and verified on R2: **Peralta
Trailhead, String Lake Trailhead Parking, Two Medicine Lake Trailhead Parking.**

Predicted 1,462-1,636; delivered 1,661 (the national ArcGIS bbox includes AK/HI,
which the CONUS measurement did not).

**STILL OPEN, and bigger than this was:** only 2,909 of 9,060 shipped areas carry
an `osm_relation_id` at all. The other 6,151 have no boundary polygon, so
containment can never be evaluated for them and no gate or buffer can ever fill
them. Not on this list yet.

---

<a name="57"></a>
## #57 — CLOSED 2026-09-06. First launch showed neither onboarding nor the location ask.

**Reported 2026-09-01** from a clean install on a spare iPhone 13 mini. Not that
phone: reproduced on a fresh CI simulator, so it hit every new install from the
public TestFlight link.

**Why nothing appeared.** `ContentView` stacked THREE `.fullScreenCover`
modifiers on one `TabView` (walk, jump-to-area, onboarding) and
`HomeView.onAppear` auto-presented a fourth thing, `LocationPromptView`,
whenever permission was missing. SwiftUI arbitrates among presentations
attached to one view, and on first launch none of them won. Run
`33506899768`:

```
AUDIT[first-launch] onboardingVisible=false continueExists=false
                    getStartedExists=false tabBarCount=1 firstTab=Explore
```

— app sitting on Explore, no cover and no sheet in the dumped tree.

⭐ **The hazard was already written down and half-fixed.** `LocationPromptView`'s
own comment said it "floats over the tab bar" and "blocks every other
sheet/cover in the app from presenting" — and the response had been to suppress
it for SEEDED UI tests only, which left the shipping path exactly as broken.

**Fixed in #584, three parts:**

1. Onboarding is a `ZStack` branch on `!onboarded`, not a presentation, so
   nothing can out-vote it. `OnboardingView` takes an `onFinish` closure rather
   than `@Environment(\.dismiss)` (which only works when presented) and fills
   its container now that no cover supplies the frame.
2. The location ask is onboarding's fifth page — the user chose that placement
   over asking on first use. Either answer to the system alert finishes
   onboarding through the authorization callback; "Not now" skips it.
3. `HomeView` no longer auto-presents a location sheet, and `LocationPromptView`
   is deleted. Explore's empty state keeps its visible "Enable Location" button.

**Why it survived to a shipping build, and the hole that is now closed.** Every
UI test launched with `--uitest-seed`, and that seeding sets
`StorageKeys.onboarded = true` — the suite was blind to first launch BY
CONSTRUCTION. `ios/SouthMountainExplorerUITests/OnboardingAuditTests.swift`
launches with no arguments, walks all five pages, taps through the springboard
alert and asserts the overlay clears afterwards.

| Run | Result |
|---|---|
| `33506899768` | repro: `onboardingVisible=false`, app on Explore |
| `33509976324` | overlay fix alone: onboarding SHOWS, then HomeView's sheet slides over it and eats the Continue button ("page 1 had neither CTA") |
| `33512537301` | all three parts: `sawLocationPrompt=true`, `pagesWalked=5`, `passed (150.6s)` |

**SHIPPED as TestFlight build 302** (run `33517633592`, "Build number set to
302", "Upload succeeded", 2026-09-01 ~14:20 UTC).

✅ **VERIFIED ON DEVICE 2026-09-06** — the user installed build 302 clean on the
spare iPhone 13 mini and reported it good.

**Device checklist (kept for the next clean-install regression):** delete the app, install fresh, and confirm
five pages, the system alert at the end, and that "Not now" also lands you on
Explore. Cosmetic and deliberately not chased: "Not now" sits ~10 pt above the
home indicator — legible in the capture, tighter than ideal, and a change costs
another ~30 min capture run.
