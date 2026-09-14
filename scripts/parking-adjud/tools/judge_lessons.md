## 5. The ten load-bearing lessons

**Every one of these came from a real user correction or a measured failure.
They are the most valuable thing in this document. Do not quietly re-derive
around them.** 📋 for all ten (sourced from auto-memory `parking-vision-adjudication`).

### 1. SERVES = the app's own rule, run area-agnostically

Not containment, not "inside the park". The app's rule, which you can read in
`ios/SouthMountainExplorer/Models/Area.swift` ✅:

- `Area.nearestParking` — nearest 3 lots within **805 m** of the trail's segment
  **endpoints** (`Area.trailEndpoints`, both ends of every segment).
- `Area.nearestParkingWithFallback` — if nothing is within 805 m, the nearest
  **2 at any distance**, labelled `isNear: false`.

The adjudication tools re-implement this and add **one thing the app does not
have: a 5 km fallback cap** (`FB_MAX=5000.0`). Rationale, measured on Zion: a far
Dixie-NF trail with no parking near it was fallback-crediting a random Zion lot.
The sorted fallback distances split cleanly:

```
1461 1483 2261 3419 4124 4176 4314 4352 | 6841 7056 8747 8749 20704
```

5,000 m sits in that gap. It drops #112 (20.7 km), #99 (7.1 km), #92 (6.8 km) —
all real trailheads for trails we *don't* ship nearby — and keeps
Chamberlain's/Narrows (4,176 m, the deliberate river-route exception).

**The test is RELATIVE, not absolute.** Big Bend is within a mile of trails but
is never the *nearest* → drop.

**Endpoint anchor is correct and settled.** Measured on Zion: switching to a
nearest-*point* anchor adds only 5 lots and all 5 are dirt roads / roadside
points / open ground — zero real trailheads. Lever retired.

### 2. Overflow is real, and it dominates

A public lot the app surfaces for a trail is a **KEEP even if it primarily serves
a ball field / pool / picnic area**. A determined hiker parks there when the main
lot is full.

**Facility-adjacency is NOT a drop, and NOT even a flag.** This killed an entire
"flagged for a second look" experiment (#58 and #1548 are keeps). The only real
over-keep test that survived is **>1 mile walk**, and that measured 0 across all
6 original areas.

### 3. "Next to a building" is not a drop — classify the neighbour from OSM

A visitor centre is a building too. `context_classify.py` splits the
neighbourhood into:

- **SUPPORT** — `leisure=nature_reserve`, `tourism=information/picnic_site`,
  `amenity=ranger_station/toilets`, `boundary=protected_area`. Corroborates KEEP.
- **FACILITY** — `amenity=place_of_worship`, `tourism=hotel/resort`,
  `leisure=golf_course/pitch/horse_riding`, `building=apartments/commercial`,
  `landuse=residential`. Corroborates DROP.
- **PARK** / **NEUTRAL** — weak or nothing.

Three sub-rules, each earned:

- **Adjacency, not just containment.** Lot #946 sits 11 m from a `leisure=pitch`
  but *inside* the broad `leisure=park`; containment-only missed it. Use nearest
  facility **area by edge distance, ≤45 m**.
- **`leisure=park` and a big `nature_reserve` are WEAK.** A lot merely inside a
  park boundary is not a trailhead; the whole park is a reserve.
- Use it for the **DROP** side (private / commercial). **Never** to flag public
  overflow — that is lesson 2.

Audit across 6 areas found **0 real over-drops**.

### 4. Burden of proof: a surveyed lot stays unless imagery positively contradicts it

- `prior = surveyed` when the lot has `amenity=parking` **plus** any of
  `surface` / `parking` / `fee` / `capacity` / `name` / `operator`. Someone stood
  there.
- `prior = bare` = naked `amenity=parking`.
- **A surveyed prior flips only on a POSITIVE contradiction**: the mapped
  footprint is clearly a building, lawn, water, or nothing but through-lanes.
- **"I can't see a lot" NEVER flips a surveyed prior.** Escalate the zoom, try
  NAIP, or mark REVIEW.
- **Empty ≠ absent.** No cars on capture day is zero evidence.
- **Blank tag ≠ "no".** Absence of `access`/`surface` says nothing.

### 5. Zoom ladder, and feed the tags to the judge

The #1596 miss — a striped, car-filled `street_side` lot called "trees" — came
from a 480 m frame with the tags hidden. The fix:

| Rung | Half-width | Resolution | Purpose |
|---|---|---|---|
| Z1 | 600 m | — | Context: what could this plausibly serve? Name every non-trail owner you can see. |
| **Z2** | **220 m** | **≈0.21 m/px** | The lot: delineation, aisles, road relationship, neighbours. **Resolves almost everything.** |
| Z3 | 140 m | ≈0.14 m/px | Confirmation: stalls, stripes, individual cars, surface. |

- **Z3 is MANDATORY** before any DROP of a surveyed prior, and before any KEEP
  that Z2 did not already prove with cars or stripes.
- **NAIP is the primary imagery source**, not ESRI:
  `https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage`
  ESRI World Imagery **throttles under burst** — it fires fast failures and then
  HANGS every connection. `ladder_tiles.py` paces at 1 s (`PACE_S`) with a
  0/6/15 s retry ladder, and since 2026-09-13 fetches NAIP first with ESRI as
  the fallback (ESRI alone failed 61 of 61 Z3 frames that day).
- Draw the mapped polygon (red) and our shipped trails (yellow) on every frame,
  and put the OSM tags in front of the judge.
- **Misregistration:** read the ~15 m neighbourhood of the red outline, not the
  exact pixel. A lot 10 px off the outline is the lot.

### 6. Trailhead-not-served is a trail-coverage gap, not a parking miss

Named trailheads far from our nearest trail (Narrows, Right Fork, Zion #190,
whose Hurricane Rim spur is unmapped) mean our **trail** geom is short or
missing there. Record it in `coverage_gaps.json` as a punch-list item. It is not
a reason to drop the lot.

### 7. A named trailhead beyond 1 mile is KEEP + coverage-gap, NOT the >1-mi drop

The >1-mile overflow test from lesson 2 must **not** drop a lot that IS the
trail's trailhead. Signals that it is:

- the lot name matches the trail (#76 "Pumpelly Trail Parking" → Pumpelly Trail)
- a trailhead kiosk (`tourism=information`, #30 → Jaquith Rail Trail)
- a `highway=trailhead` node nearby

The far walk means our trail geom is a short fragment. **KEEP, and flag the gap.**
Unnamed far lots with no trail signal and a facility nearby (Carey Park #25,
community centre #29) still DROP.

**Root cause of the far walk, found and confirmed in code 2026-08-02.** It is NOT
missing OSM data. OSM's Grafton Loop Trail runs continuously to the trailhead —
nearest point **9 m**; of 97 OSM points in the trailhead corridor our geom keeps
6 and drops a contiguous 91 (~1.5 km). The cause is
`trailforge/serve/publish_areas.py::_trim_to_parks` (the "DC-Ray fix", PR #338):
every trail is clamped to the UNION of park boundaries and any **dangling end
that dead-ends outside all parks is trimmed**. It was built to strip residential
dead-ends. A trailhead access spur dead-ends at a lot on the approach road just
*outside* the park — indistinguishable from a residential dangle. Full analysis
lives in `TASKS.md` #54; the recommendation there is to leave it as a punch-list
rather than tweak `_trim_to_parks` inline.

### 8. Summer NAIP leaf-on HIDES small forested trailhead pull-offs

In NH / VT / ME many surveyed `amenity=parking` lots read as pure canopy even at
0.21 m/px (#5, #22 `parking=lane`, #31). **Do not DROP a surveyed lot for
canopy** — mark REVIEW with `resolve_hint: "leaf-off NAIP (winter) / ground"`.
This is a real imagery limitation of the eastern forest; it was absent in the
desert and urban runs. Consider sourcing a leaf-off NAIP layer for the northeast.

### 9. The serves-gate can be a trail-QA signal — but verify each case

The pre-compaction claim that `pinkham-notch-scenic-area-nh`'s Tuckerman Ravine
Trail had 158/334 vertices misplaced at Crawford was **FALSE** and was corrected
by checking the geom: that trail is one continuous ~4 km line, max
consecutive-vertex gap 128 m, `profileGaps: None`. Longitude −71.30 is the ravine
near Mt Washington's summit, not Crawford Notch (−71.41). The lots that looked
wrong were real Mt-Washington trailheads pulled into Crawford's per-area run by
the dossier's `BUF=0.06` region buffer, served at 3 m / 9 m / 193 m — not
fallback at all.

**The general name-stitch teleport IS real**, though:
`scripts/audit-namestitch-teleport.py` (national, off geom, ~35 s) finds **1,647**
trails split into ≥2 chunks ≥1 km apart. Smoking gun: "Yellow" in
`adirondack-park`, two chunks **111 km** apart. That feeds TASKS #36, not this task.

### 10. REVIEW must name the frame that would flip it; otherwise decide
Indian Peaks (2026-09-13), the first fan-out area: the judges returned 2 REVIEWs
in 149 lots and the user flipped both to DROP on sight. #9 was a highway pull-off
under 100% ESRI cloud with Social 16 at 705 m and nothing else in range; #82 a dirt
pad on a condo-street spur, Tunnel Hill 202 m away across the railroad, whose only
KEEP path was a "residents only" sign no aerial frame can show. Both
`resolve_hint`s asked for evidence the ladder cannot produce (a ground check, a
sign). REVIEW is for a call that a FETCHABLE frame would change: canopy over a
surveyed prior, a clear NAIP frame for a clouded ESRI one (fetch it, then
decide). When the only thing that could rescue a lot is unobservable from the
air and everything visible says DROP, the verdict is DROP, `leaning`, with the
doubt in the evidence string. The human's pen (`merge_drafts.py --set`) exists
for the rest, and `tools/calibration.py` counts how often it is used.

---

