# Parking adjudication — full handoff (task #53)

**Written 2026-09-13 for an agent with zero prior context.** Everything you need
to pick this up is here or pointed at from here. Claims marked ✅ were verified by
a command run while writing this doc; the command is named. Claims marked 📋 are
relayed from the durable README / auto-memory and were NOT re-verified — treat
them as well-sourced but stale-able.

---

## 1. What this project is, in one sentence

For every parking lot near a trail we ship, decide **"is this a real, public
place to park that actually serves one of our trails?"** — using OSM tags for
the data half and aerial imagery for the half that tags cannot answer — and
record the verdict in a reversible sidecar so bad pins stop reaching the app.

**It is a judgement pipeline, not a geometric filter.** That distinction is the
whole point and section 3 explains why it was fought for.

---

## 2. Status board

| Thing | State |
|---|---|
| Verdict model (3 axes) | Settled, exercised on 11 areas across 3 morphologies |
| Tooling | Built, **in the repo** at `scripts/parking-adjud/tools/` (14 python + 2 shell + the judge protocol). Paths are env-configurable; all 14 compile ✅ |
| Areas adjudicated | **11**, 0 reviews outstanding 📋 |
| Distinct OSM lots with a banked verdict | **299** ✅ (`python3` count over the 4 verdict stores) |
| Global pool the app actually serves | **30,840 lots** ✅ (ran `scripts/build-parking-pool.py`) |
| So: fraction adjudicated | ~1% |
| `public/areas/parking-verdicts.json` | **DOES NOT EXIST** ✅ (`ls`); nothing in the repo references it except `TASKS.md` ✅ (`grep -rn`) |
| Colorado batch | Inputs prepared, **never run** — 261 areas / 2,090 lots ✅ (`co_areas.json`) |
| Data | **In the repo** at `scripts/parking-adjud/data/` — 74 files, 8.2 MB ✅. Aerial tiles stay on the homelab (~91 MB, regenerable) |
| Scoring against the user's calls | **0 confident-wrong** on both Zion and Griffith ✅, re-run 2026-09-13 |
| Blocking | Nothing external. This is unblocked work. |
| It blocks | TASKS **#51**'s containment roll and **#52**'s polygon merge 📋 |

**The single most important open decision is in section 14 (the ID problem). Read
that before designing anything.**

---

## 3. Why this is the source of truth, and why a geometric gate is not

Decided 2026-08-12. The user chose the per-lot vision verdict over any geometric
gate. Anything that would REMOVE a parking pin now defers to this task.

The argument that settled it:

- `add-parking.py::pool_candidates`' own docstring states that callers apply the
  containment and road gates **first**. So a lot the containment gate rejects
  never reaches `public/areas/parking-pool.json`, and never reaches the global
  pool. **The pin vanishes from the entire app, not just from the area that
  "lost" it.**
- Containment is the wrong test for trailheads anyway. Trailheads are roadside
  pull-offs on the approach road, **outside the park polygon by nature**. Cady
  Hill Forest's three lots sit 179–556 m outside its boundary. 📋
- Measured 2026-08-12 over shipped geom: 6,848 of 9,074 areas carry a boundary
  id (75.5%); 2,226 do not; 1,437 of those ship **9,420 lots admitted by
  proximity alone**. Those are what a containment re-run puts at risk. 📋

Same fact is the root cause of TASKS **#54** (`_trim_to_parks` severing trailhead
access spurs) — see lesson 7 in section 5, and the coverage gaps in section 7.

---

## 4. The verdict model

Three independent axes. A lot is **KEEP** only if all three pass.

```
EXISTS   is there a real place to leave a car?
         (a lot / graded pad / street parking — NOT a pullout, open ground,
          or a turnaround)
PUBLIC   may an ordinary hiker park there?
         (not private / gated / customers-only. fee ≠ private.)
SERVES   does a trail we ship plausibly explain it?
```

**DROP** = one axis confidently fails: private by tag, not-real-parking by
vision, a NON-public facility lot (apartment / resort / church garage), or too
far to be overflow (>1 mile / 1609 m walk).

**KEEP** = any public lot the serves-gate surfaces within ~1 mile — park lot,
street parking, or a public-facility lot — facility-adjacent or not.

**REVIEW** = an axis is genuinely undecidable after the FULL zoom ladder. A
REVIEW verdict **must** name what would resolve it (`resolve_hint`).

Confidence tiers: `certain` (cars/stripes seen, or a hard tag rule) / `strong`
(clear read) / `leaning` (plausible but soft).

---

## 5. The nine load-bearing lessons

**Every one of these came from a real user correction or a measured failure.
They are the most valuable thing in this document. Do not quietly re-derive
around them.** 📋 for all nine (sourced from auto-memory `parking-vision-adjudication`).

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
  HANGS every connection. Pace at ≥1 request / 2.5 s.
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

---

## 6. The vision half, concretely

Sections 4 and 5 give the rules. This is what actually happens at the screen.

### What a judge is handed, per lot

1. **The dossier row** — read it BEFORE any image, and write the prior down
   first. `judge_protocol.md` step 0 is explicit that you may not look first and
   then decide what the tags "must have meant".
   - `prior`: `surveyed` or `bare`
   - `tags_union` and per-`members` tags (a cluster can mix access values —
     `mixed_access` flags it, and you judge the member nearest the trail)
   - `ring` / `area_m2` — the mapped footprint
   - serving trail, its **edge** distance, and whether it came via fallback
   - `walk_m` and `conn` from the foot network ("no route" is a real signal)
   - `trailhead_nodes` within 120 m, `footways_60m`, `building_overlap`
   - the OSM context category and its evidence string
2. **The tiles** — Z1 / Z2 / Z3, pre-rendered.
3. **Nothing else.** No map app, no Street View. The protocol is the protocol.

### What a tile looks like

`z2render.py` and `ladder_tiles.py` draw, on every frame:

- **red** — the lot's own mapped OSM polygon(s). Node-only lots get a 20 m red
  circle instead.
- **yellow with a dark casing** — every trail we ship in the region.
- **orange rings** — every OTHER parking lot in frame, so "is there a closer lot
  than this one" is answerable by eye.
- a black header strip naming the fid, the rung, the half-width, and the imagery
  source, e.g. `#1596 Z2 · 220 m · NAIP · red=lot yellow=trails`.

`ne_review2.py` adds a fourth layer for the northeast:

- **cyan, drawn UNDER the yellow** — hiking-relevant OSM ways we do NOT ship.
  Cyan reaching a lot while yellow stops short is a trimmed trailhead spur
  (lesson 7). The filter that keeps cyan meaningful is `_hike_ok`: drop
  `piste:*`, drop bare `highway=track` without `foot=yes`, drop
  `bicycle=designated` bike-park trails, drop sidewalk and crossing footways. ✅
  Without that filter a ski resort renders as a field of cyan that means nothing
  — measured at Gunstock: 189 ways, of which 74 piste and 124 MTB. 📋

### Imagery endpoints, verbatim

```
NAIP  (primary)
https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage
  ?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={PX},{PX}&format=png&f=image

ESRI World Imagery  (fallback only)
https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export
  ?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={PX},{PX}&format=png&f=image
```

- `PX = 1024`. Half-widths: Z1 600 m, Z2 220 m, Z3 140 m, converted to degrees
  with `dlat = half/111320`, `dlon = half/(111320·cos(lat))`.
- Retry ladder per source: immediate, +6 s, +15 s. NAIP first, then ESRI.
- **Pace at ~1 request/second minimum, 2.5 s under burst.** ESRI throttles by
  firing fast failures and then HANGING every subsequent connection, which looks
  like a network outage and is not one.
- A `User-Agent` header is set (`trekdex/1.0`); requests time out at 40 s.

### How a call is actually made

- Read the dossier, commit to the prior.
- Z1: name every plausible non-trail owner you can see. If you cannot name one,
  say so — that is evidence FOR the trail explanation.
- Z2: this resolves almost everything. Delineation, aisles, how it meets the
  road, what is next door.
- Z3: only for confirmation, and **mandatory** before dropping a surveyed prior
  or keeping something Z2 did not prove with cars or stripes.
- Evidence strength, strongest first: **parked cars > painted stripes >
  delineated graded surface > bare clearing.** Name which you saw and in which
  frame, or which prior carried it. The `evidence` strings in the banked verdicts
  are the style to copy — they are specific and falsifiable, e.g.
  `"Z3: painted stalls + 2 cars; NAIP: 5 cars"`.
- Then write the JSON object from section 13 and nothing else.

### The fan-out that is proven to work

67 of the 80 New England lots were judged by **four parallel per-area
sub-agents**, one area each. Each was handed, in its prompt: `judge_protocol.md`
verbatim, the nine lessons, and that area's `_dossier.json`, `_serves2.json`,
`_context.json`, `_walk.json` and rendered Z2 tiles. Each returned a
`<slug>_verdict_draft.json` — a LIST of verdict objects. `merge_ne.py` then
validated the schema, printed every DROP with its evidence for a human to
eyeball, and `--write` folded them into the OSM-keyed store. 📋

Verification after the fact found the agents correct: all 12 drops were
far-fallback serves (2.1–7.3 km walks, none a close lot wrongly dropped), the 3
surveyed-prior drops were rest areas and a ski lot dropped on serves-distance and
NOT on canopy (lesson 8 respected), and 4 tiles were re-read by hand. 📋

✅ You can see that whole review for yourself right now — `merge_ne.py` runs
against the committed data and reprints the nine drops with their evidence and
the path to each tile:

```bash
cd scripts/parking-adjud && PADJ_TMP=$PWD/data python3 tools/merge_ne.py
```

---

### See it, rather than read about it

The New England review page — all 80 lots, two NAIP frames each, the OSM
context, the serves gate and the verdict, in the sage two-frame design
`ne_review2.py` produces:

**https://claude.ai/code/artifact/c0649b34-6616-4ade-8901-2273d8f5faaf**

Self-contained: 80 embedded images, no external requests ✅. This is what
judging actually looks like. Read five lots there before judging anything.

The per-area Arizona artifacts (`padjart2.py` output — map plus per-axis cards)
are on the homelab at `/mnt/raid/trekdex/parking-adjud/artifacts/`: Phoenix
Mountains Preserve 8.6 MB, Pinnacle Peak 6.1 MB, Usery Mountain 6.0 MB. Publish
them the same way if you want them in front of someone.

---

## 7. The adjudication itself — what was actually decided, and by whom

The sections above are the method. This is the corpus: **296 verdicts with their
reasoning**, ✅ counted from the four committed stores, split 213 KEEP / 83 DROP,
by confidence 74 certain / 175 strong / 47 leaning, by prior 189 surveyed / 107
bare.

**101 of those 296 cite the user's own call in the evidence string.** ✅ This is
not a model that was left to run — it was steered, lot by lot, and the steering
is recorded in the data rather than lost to a chat log. `grep -l "user" ` over
`scripts/parking-adjud/data/*verdict*.json` finds them.

### The user's ground truth — the test set ✅

`data/groundtruth.json`, two areas:

| Area | Rows | Composition | Source |
|---|---|---|---|
| Zion | 39 | 19 KEEP + 20 DROP, **all `certain`** | `src: user` on every row |
| Griffith | 28 | 15 KEEP strong, 2 KEEP leaning, 4 DROP strong, 7 REVIEW leaning | model reads, held for comparison |

The Zion set is the one that matters: 39 lots the user personally called, with
no hedging. `score2.py` scores against it, and the passing bar is **zero
confident-wrong** — a KEEP on a certain/strong DROP row, or the reverse. Not 100%
agreement; `leaning` rows may land REVIEW freely, because forcing a call on a
genuinely ambiguous lot is how you get a confident mistake.

Zion verdicts carry `"user ground-truth KEEP"` / `"user ground-truth DROP"`
literally in the `exists` evidence, so the encoded calls are auditable one by one.

### The cases that became rules

Each of these is a real lot in the committed data. They are what the abstract
lessons in section 5 actually mean.

**The Phoenician resort cluster — `node/1924424411`, `node/1924424479`,
`node/1925612531`.** Three lots at a Scottsdale resort, all `prior=surveyed`, all
DROP on PUBLIC.

```
exists: yes — underground garage under The Phoenician resort
public: no  — resort
serves: no  — Cholla walk 2926 m
```

This is the clean PUBLIC drop: the lot is unambiguously real, and that is
irrelevant. Note both axes failing independently — a resort garage 2.9 km from
Cholla Trail fails PUBLIC *and* SERVES. Neither alone was leaned on.

**`way/809362789` — a private estate.** `prior=surveyed`, and the tags say
nothing about access:

```
exists: yes — Z2: lot inside a private estate/resort compound (mansion, pools, walls)
public: no  — private estate
serves: no  — estate explains it; Perl Charles walk 1540 m
```

The tag was blank. Vision supplied "mansion, pools, walls". This is the case
that shows why PUBLIC is not a pure tag rule — but also why the *evidence string*
has to name what was seen, or the call is unreviewable.

**`node/2103033767` and Zion `#109` — the only two EXISTS drops in the whole
corpus.** ✅ Out of 83 drops, exactly two were "this is not a place to park":

```
[AZ]   exists: no — Z2: bare dirt patch at a road fork, no delineated lot or cars
[ZION] exists: no — Z2: smooth bare tan patch beside the road next to a residence,
                    reads as a dry pond, no cars/stalls
       resolve_hint: NAIP / ground check whether the bare patch is a graded lot
                     or a stock pond
```

**That ratio is the burden of proof working.** EXISTS almost never fails, because
a surveyed prior only flips on a positive contradiction, and "I can't see it"
never counts. If a future run starts dropping lots on EXISTS at any volume,
something has gone wrong with lesson 4.

**Griffith `#946` — the case that produced the 45 m adjacency rule.** A pad
inside the broad `leisure=park` boundary but 11 m from a `leisure=pitch`:

```
serves: no — Baseball-field parking right beside it (leisure=pitch) — user call;
             the pad serves the field, not the trail
```

Containment-only classification called it a park lot. Edge-distance to the
nearest FACILITY area caught it. Hence `context_classify.py`'s ≤45 m rule and its
demotion of `leisure=park` to weak.

**`way/1367192203` and the Gilford school lots — where the FACILITY label is
overruled.** Three striped school lots, context FACILITY:

```
public: yes — public (government) school campus, fee=no, capacity:disabled=4,
              no access=private; not a private/commercial facility
serves: yes — Mt. Rowe Trail 302 m, fallback false; walk 662 m;
              Mt. Rowe trailhead sits on the school grounds (dual-use)
```

A FACILITY classification is an input, not a verdict. Government-owned and
publicly accessible beats the label. Compare directly against the Phoenician:
same "next to a big building" shape, opposite call, and the difference is
ownership plus whether a trail starts there.

**`way/36079469` at Bolton Valley, and `way/219690843` "Main Parking Lot" at
Gunstock.** Both ski resorts, both KEEP:

```
public: yes — resort base-area day-use lot; no access=private/customers tag;
              facility-adjacency (context FACILITY residential) is not by itself a drop
serves: yes — Brook Run 313 m non-fallback; walk 781 m (<1609)
```

Lesson 2 stated as an operating rule by an agent that had internalised it.

**Mount Major `#7` "Segway Training" — the ski-resort lot that DID drop.** Worth
holding beside the two above, because the difference is the whole skill: its
mapped footprint sits **on a Gunstock resort building**, and its only serve was a
1 km fallback through ski terrain. Facility adjacency did not drop it. Being a
building, with no real trail connection, did. 📋

**Mad River Glen — the debatable one, flagged to the user rather than buried.**

```
exists: yes — large gravel/dirt ski-area base parking lot clearly visible
public: yes — no access restriction tag
serves: no  — walk 2224 m (>1609) with only a fallback serve to Catamount Trail
              at 1679 m; no trailhead node, name does not match a serving trail,
              context FACILITY
```

A real, public, obvious ski lot dropped purely on distance. The agent called it
and **surfaced it as debatable** instead of letting it pass silently. That is the
behaviour to reproduce: a DROP that a reasonable person might reverse gets named
in the report.

**The I-89 rest areas and the Cog Railway materials yard.** Three surveyed-prior
drops in Camel's Hump and Crawford Notch, all dropped on SERVES distance and
explicitly **not** on canopy — the check that lesson 8 was respected:

```
Rest Area I-89 (North Bound): interstate rest-area parking with marked angled
  stalls and parked cars visible — public yes — serves no, 2509 m fallback only
Cog Railway: large graded yard at the base, but the footprint is a materials /
  laydown yard with rows of stacked piles, not clean trailhead parking
```

**Chamberlain's Ranch — the deliberate 4 km exception.** The user called it KEEP
at **4,172 m** to The Narrows Top Down. It is why the fallback cap landed at
5,000 m rather than lower: the cap had to keep this one and still drop the 6.8 km+
other-area trailheads. It also appears in `coverage_gaps.json` as "only a far
fallback", because the Narrows river route is unmapped.

### The coverage gaps — the by-product punch-list ✅

`data/coverage_gaps.json`, 12 entries, all Zion. Eleven read "serves no trail we
ship"; one is Chamberlain's. These are **trail** data problems found by parking
work:

```
#24  Right Fork Trailhead                    nearest: The Subway Bottom-Up Approach
#112 Orderville Corral Trailhead (Non-4x4)   nearest: Upper Orderville Canyon Trail
#148 Applecross North Trailhead Parking      nearest: East Rim Trail
#110 Birch Hollow Trailhead Parking          nearest: East Mesa Trail
#38  Gooseberry Mesa - Windmill Trailhead    nearest: (none)
#99  The Corral Trailhead - Applecross       nearest: (none)
#140 JEM Trailhead Parking                   nearest: (none)
#160 Gooseberry Trailhead Parking            nearest: (none)
#161 Gooseberry Mesa - White Trailhead       nearest: (none)
#193 Sheep Bridge Trailhead Parking          nearest: (none)
#206 Wire Mesa Trailhead Parking             nearest: (none)
#96  Chamberlain's Ranch Trailhead Parking   only a far fallback (4176 m)
```

The New England run found four more, marked inline with `"coverage_gap": true`
on the verdict — Monadnock Old Toll Road, Jaquith Rail Trail, Pumpelly Trail
Parking, and Grafton Loop Trailhead East ✅. Those four are the evidence behind
lesson 7 and behind TASKS #54: each is a named trailhead whose lot is right
there and whose *trail* stops a kilometre or two short.

```
Pumpelly Trail Parking — the named Pumpelly trailhead; our Pumpelly geom ends
  2.2 km short — coverage gap
Grafton Loop Trailhead East — served only via 1265 m fallback because our Grafton
  Loop Trail geom is clipped ~1.6 km short of its eastern trailhead
```

### What each morphology taught

Three deliberately different places, same protocol, no re-tuning:

| Run | Character | What it stressed |
|---|---|---|
| **Zion Wilderness** | desert, sparse, huge distances | the fallback cap; the difference between a trailhead and open ground |
| **Griffith Park, LA** | urban, 8× denser, 24 overlapping areas | the access tag as the heaviest filter (952 private/customers auto-dropped region-wide); facility adjacency; the genuinely ambiguous urban roadside, which is the only review-heavy zone |
| **New England, 5 areas** | sparse, roadside, forested | leaf-on canopy hiding real lots; trimmed trailhead spurs; the sub-agent fan-out |

Griffith was chosen specifically as a hostile contrast to Zion, and scoring both
against the user's calls gives **0 confident-wrong with no re-tuning** — ✅ still
true when re-run from the committed data on 2026-09-13. That is the evidence the
rules are not Zion-overfit, and it is the bar any change to them has to clear
again.

---

## 8. Killed ideas — do not re-propose without new evidence

- **Area-first framing** (judge a lot against one area, draw a boundary). WRONG —
  parking is area-agnostic since #500. Superseded by the parking-first,
  serves-gate model.
- **The "display-mirror gate"** (only judge lots the app currently surfaces) and
  the old `ref-build-area-parking-artifact.py` / `ref-display-mirror-gate.py`
  scripts. Pre-protocol harness.
- **"Facility building → DROP"**. Killed by lesson 2.
- **"Facility-adjacency → flag for review"**. Killed by lesson 2.
- **Nearest-*point* serves anchor** instead of endpoints. Killed by measurement
  (lesson 1).
- **Dropping a lot because no hiker-use attribute is recorded.** A blank
  attribute is not a negative — this is the same trap documented in `CLAUDE.md`
  gotcha #11.

---

## 9. The pipeline — running one area end to end

Needs `osmium`, `shapely`, `Pillow` and the OSM extracts — so this half is
homelab-only. See section 19 for what a non-homelab agent can still do.

```bash
cd scripts/parking-adjud
export PADJ_TMP=$PWD/work       # tools read AND write their JSON here
SLUG=<area-slug>                # matches public/areas/geom/<slug>.json

# 1. Per-area OSM context extract (footways + context features), ~3.5 min from
#    the national file. dossier.py and foot_route_area.py BOTH read
#    <slug>_ctx.osm.pbf from their TMP dir, so name it exactly that.
osmium extract --bbox=<lon0,lat0,lon1,lat1> \
  -o $PADJ_TMP/${SLUG}_ctx.osm.pbf /mnt/raid/trekdex/osm/us-latest.osm.pbf
#    bbox = the geom bbox expanded by BUF=0.06 degrees on every side.

# 2. Dossier: per-lot full tags + real OSM ids + polygons + the edge-distance
#    serves gate. Emits <slug>_dossier.json and <slug>_serves2.json.
python3 tools/dossier.py $SLUG

# 3. Foot-network walk distance to our nearest shipped trail (Dijkstra over the
#    walkable OSM network). Emits <slug>_walk.json and joins walk_m into the dossier.
python3 tools/foot_route_area.py $SLUG

# 4. OSM context classifier — pass the pbf path explicitly.
python3 tools/context_classify.py $SLUG $PADJ_TMP/${SLUG}_ctx.osm.pbf

# 5. Render the zoom tiles for the public-served set (NAIP primary).
python3 tools/z2render.py          # Z2 only, or ladder_tiles.py for Z1/Z2/Z3

# 6. JUDGE each served lot. Vision. By hand, or fan out to sub-agents each
#    handed tools/judge_protocol.md plus the nine lessons above plus this
#    area's dossier / serves / context / tiles. Store verdicts KEYED BY OSM ID.

# 7. Artifact for human review.
python3 tools/padjart2.py $SLUG "Display Name"
```

Cross-area helpers:

- `review_queue.py` — scans every area's verdicts and builds ONE cross-area
  REVIEW-only page (undecided lots, plus any KEEP beyond 1 mile).
- `score2.py <area> <verdicts.json> <dossier.json>` — scores verdicts against
  `data/groundtruth.json`. **The bar is 0 confident-wrong, not 100% match.**
  `leaning` rows may land REVIEW freely.

---

## 10. Paths — fixed, and what was wrong before

**This is no longer a blocker.** It is recorded because the failure shape recurs.

Every tool used to hardcode `TMP="/home/quentin/.claude/jobs/a4a4c50a/tmp"` — an
ephemeral Claude job directory that no longer exists ✅ (`ls -d`). Thirteen files
carried it, plus both shell drivers. `run_co.sh` tried to work around it with
`PADJ_TMP` / `PADJ_PARKING_PBF` / `PADJ_US` environment variables, but the
`/mnt/raid` copies of the tools read no environment variable at all ✅
(`grep -rn "PADJ_\|os.environ\|getenv"` returned nothing) — the env-aware
versions lived only inside that wiped tmp.

**Fixed 2026-09-13** when the tools were graduated into `scripts/parking-adjud/`.
Every path now resolves through an environment variable with a repo-relative
fallback:

| Variable | Default | What it is |
|---|---|---|
| `PADJ_TMP` | `scripts/parking-adjud/work` | Working dir; tools read and write JSON here |
| `PADJ_GEOM` | `public/areas/geom` | Shipped trail geom |
| `PADJ_PARKING_PBF` | a region pbf in `PADJ_TMP`, else `/mnt/raid/trekdex/osm/cache/parking-only.osm.pbf` | The `amenity=parking` extract |
| `PADJ_US` | `/mnt/raid/trekdex/osm/us-access.osm.pbf` | What per-area context is cut from |
| `PADJ_OSM` | `/mnt/raid/trekdex/parking-adjud/osm` | Per-area `_ctx.osm.pbf` extracts |

`score2.py` resolves `groundtruth.json` the same way, defaulting to the sibling
`data/` directory, so it scores with no environment set at all.

Verified ✅: all 14 python tools compile, both shell drivers pass `bash -n`, no
file anywhere under `tools/` still names the dead directory, `merge_ne.py` runs
against the committed `data/` and reproduces the New England review, and
`score2.py` scores both ground-truth areas at 0 confident-wrong.

```bash
cd scripts/parking-adjud && PADJ_TMP=$PWD/data python3 tools/merge_ne.py
```

Two tools still fail on their own inputs, and that is expected, not a
regression — `serves_relative.py` and `padjudicate.py` are **superseded by
`dossier.py`** and read pre-protocol files (`zion_ctx.json`) that were never
carried forward. They are kept because `serves_relative.py` is the clearest
single statement of the serves rule.

### Dependencies ✅

`python3 -c "import osmium, shapely, PIL"` — osmium, shapely 2.0.3, Pillow 10.2.0,
all installed on the homelab.

### Stale warnings you can ignore

📌 The raid README, `TASKS.md` #53 and the auto-memory all warn that the root disk
is at 94% with 5.7 GB free, and that a disk-full once truncated a verdict file.
**No longer true** ✅ (`df -h /`): 232 GB, 46% used, **121 GB free**. Keeping pbfs
on `/mnt/raid` is still right — they are huge and belong with the other extracts
— but the disk panic is over.

📌 The auto-memory lists "filter cyan to hiking-relevant ways" as a TODO. **Already
done** ✅ — `ne_review2.py::_hike_ok`, wired into the render path.

## 11. Tool reference

All in `scripts/parking-adjud/tools/` (also still at `/mnt/raid/trekdex/parking-adjud/tools/`, now the stale copy).

| File | Role |
|---|---|
| `dossier.py` | **The data layer.** Reads parking geometry + full tags + real OSM ids from the parking-only pbf (area handler for closed ways/relations, node handler for point lots — the JSON cache strips ids/geometry/tags). Clusters lots at 40 m keeping per-member tags. Runs the serves gate with **polygon-EDGE** distances. Joins the foot-walk and OSM context (trailhead nodes ≤120 m, footways within 60 m, building overlap). Emits `<slug>_dossier.json` + `<slug>_serves2.json`. |
| `serves_relative.py` | The standalone serves gate (app rule + 5 km cap), fid-keyed. Superseded inside `dossier.py` but readable as the clearest statement of the rule. |
| `foot_route_area.py` | Multi-source Dijkstra over the walkable OSM network. Sources = lots, targets = our shipped trail vertices. Produces `walk_m` and a `conn` field ("no route" when unreachable). |
| `context_classify.py` | SUPPORT / FACILITY / PARK / NEUTRAL per lot, from OSM tags by edge-adjacency. See lesson 3. |
| `ladder_tiles.py` | Renders the full Z1 / Z2 / Z3 ladder. |
| `z2render.py` | Renders Z2 only (the rung that resolves almost everything). NAIP primary, ESRI fallback, 1 s pacing. |
| `judge_protocol.md` | **The per-lot checklist and verdict schema. This is also the verbatim fan-out prompt for sub-agents.** |
| `padjart2.py` | Per-area review artifact: map plus per-axis cards, REVIEW cards embed the Z3 tile. |
| `review_queue.py` | Cross-area REVIEW-only artifact. Wide 600 m context frame + 220 m detail frame per lot. |
| `ne_review2.py` | The New England reviewer. Same sage design, but shows ALL served lots with a YOUR-CALL badge, and adds the **cyan OSM-trail overlay** (a hiking trail OSM has that we don't ship — makes a trimmed trailhead spur visible). |
| `make_ne_review.py` | Earlier NE review builder, superseded by `ne_review2.py`. |
| `merge_ne.py` | Merges per-area NE verdict drafts into `ne_verdicts_osm.json`, with a schema check. `--write` actually writes. |
| `score2.py` | Verdicts vs `groundtruth.json`, confidence-tiered. Exit 1 if any confident-wrong. |
| `phx_apply.py` | Maps an OSM-id-keyed verdict store back onto each area's run-local fids so `padjart2.py` can build per-area artifacts. **A lot's verdict is the same in every area it appears in.** |
| `padjudicate.py` | Early area-agnostic serves-only builder. Superseded by `dossier.py`. |
| `run_ne.sh` | The NE per-area driver — the model for any future batch runner. |

---

## 12. Data reference

All in `scripts/parking-adjud/data/` — 74 files, 8.2 MB ✅. The rendered tiles are NOT in the repo; they stay at `/mnt/raid/trekdex/parking-adjud/data/<slug>_ladder/`.

| File | Shape |
|---|---|
| `<slug>_dossier.json` | `{slug, name, bbox, facilities: [...]}`. Each facility: `fid`, `osm: ["way/123", ...]`, `lat`, `lon`, `members[{osm, tags}]`, `tags_union`, `mixed_access`, `ring` ([lat,lon] pairs), `rings`, `area_m2`, `prior` (`surveyed`\|`bare`), `descriptive`, `trailhead_nodes`, `footways_60m`, `building_overlap`, `walk` |
| `<slug>_serves2.json` | `{fid: {served, trail, dist_m, fallback, n}}` — the edge-distance serves gate |
| `<slug>_context.json` | `{fid: {category, evidence, fac_area_m, fac_label}}` |
| `<slug>_walk.json` | `{fid: {walk_m, conn, trail}}` |
| `<slug>_pub.txt` | comma-separated fids of the public+served set (the render/judge worklist) |
| `<slug>_verdicts2.json` | **fid-keyed** verdicts (Zion, Griffith, and the AZ areas after `phx_apply.py`) |
| `phx_verdicts_osm.json` | **OSM-id-keyed** verdicts, the 4 Arizona areas. 98 entries ✅ |
| `ne_verdicts_osm.json` | **OSM-id-keyed** verdicts, the 5 New England areas. 84 entries ✅ |
| `<slug>_verdict_draft.json` | per-area sub-agent output, a LIST of verdict objects, before `merge_ne.py` folds it in |
| `groundtruth.json` | `{area: {fid: {want, confidence, lat, lon, src}}}` — the user's own calls. Two areas: `zion`, `griffith` ✅ |
| `coverage_gaps.json` | list of `{fid, name, lat, lon, reason, nearest_trail}` — 12 entries ✅ |
| `QUALITY_REPORT.md` | The 2026-07-31 autonomous quality pass. Worth reading in full — it contains the fallback-cap derivation and the Griffith generalization test. |

### Rendered tiles that survive ✅

Only the New England ladders and raw context caches are still on disk:

```
camels-hump-state-park-vt_ladder      20 tiles    _ctx600  15
crawford-notch-state-park-nh_ladder   25 tiles    _ctx600  25
grafton-notch-state-park-me_ladder     6 tiles    _ctx600   6
monadnock-reservation-nh_ladder       13 tiles    _ctx600  13
mount-major-state-forest-nh_ladder    16 tiles
```

The Zion / Griffith / Arizona tiles lived in the wiped job tmp and are **gone**.
Their verdicts are banked, and the tiles can be re-rendered from the dossiers.

### OSM extracts on hand ✅

`/mnt/raid/trekdex/parking-adjud/osm/` (182 MB): per-area `_ctx.osm.pbf` for
Camel's Hump, Crawford Notch, Grafton Notch, Griffith, Monadnock, Mount Major,
Zion; plus `ne_region.osm.pbf`, `ne_parking.osm.pbf`, `phx_metro.osm.pbf`,
`phx_parking.osm.pbf`.

### Artifacts on disk ✅

`/mnt/raid/trekdex/parking-adjud/artifacts/` (35 MB): `ne_parking_review.html`,
`review_queue.html`, and the three Arizona `*_parking_v2.html` pages.

---

## 13. The verdict schema

One JSON object per lot, no prose outside it. From `tools/judge_protocol.md`:

```json
{"fid": 1596, "osm": ["way/896741459"], "verdict": "KEEP",
 "prior": "surveyed",
 "exists": {"call": "yes", "evidence": "Z3: painted stalls + 2 cars; NAIP: 5 cars"},
 "public": {"call": "yes", "evidence": "access=yes; no gate visible Z3"},
 "serves": {"call": "yes", "evidence": "Bird Sanctuary Loop edge 50 m; walk 46 m; nothing else adjacent"},
 "frames_used": ["z1","z3","z3_naip"],
 "tags_cited": {"parking": "street_side", "surface": "asphalt", "fee": "no"},
 "confidence": "certain",
 "resolve_hint": null}
```

`resolve_hint` is **required** (non-null) when `verdict == "REVIEW"`.

The New England run added two optional fields, both useful — keep them:

```json
 "area": "monadnock-reservation-nh",
 "coverage_gap": true,
 "src": "opus-ne"
```

---

## 14. ⚠️ THE ID PROBLEM — read before designing the sidecar

The plan of record is a reversible `public/areas/parking-verdicts.json` **keyed by
OSM id**, shaped like `nonhiking-trails.json`, honoured by the pool builder, and
never able to empty an area.

**There is a hole in that plan, and I verified it this session.**

✅ Scanned all 9,074 shipped geom files. **40,339 parking records across 6,319
areas. The only fields present are `lat`, `lon`, `trailhead`, `fee`, `name`,
`source`. There is no OSM id anywhere in shipped geom.**

```
field frequency: {'lat': 40339, 'lon': 40339, 'trailhead': 9455,
                  'fee': 7965, 'name': 7682, 'source': 534}
```

So an OSM-id-keyed sidecar **cannot be joined to shipped lots by id**. The pool
builder reads `lat`/`lon` out of geom and has nothing to match an id against.

You have three options:

**(a) Emit the OSM id at fetch time — recommended.** ✅
`scripts/add-parking.py::parse_parking` (line 759) already holds `el["id"]` and
`el["type"]` from the Overpass element and discards them. Line 776 builds:

```python
entry: dict = {"lat": lat, "lon": lon, "_self_th": tags.get("highway") == "trailhead"}
```

Adding `entry["osm"] = f"{el['type']}/{el['id']}"` is a one-line change. The cost
is that every area must be re-rolled through the parking workflow before the ids
exist in geom, and ~40k extra short strings ship.

**(b) Join by position.** Match a verdict to a lot within some metres. The pool
builder already dedupes at `DEDUP_M = 40.0` m ✅, and `Area.mergingPool` dedupes
at ~1 m grid precision ✅, so precedent exists for positional identity. Risk: OSM
lots move when a mapper redraws a polygon, and the dossier **clusters at 40 m**,
so one verdict may cover several nearby physical lots.

**(c) Key the sidecar by position yourself** — store `lat`/`lon` rounded, and
carry the OSM ids as evidence rather than as the key. This is what
`coverage_gaps.json` already does.

**Whatever you choose, decide it explicitly and write the decision into
`TASKS.md` #53.** The "keyed by OSM id" plan was written before anyone checked
what shipped geom contains.

---

## 15. The consumer side — what the sidecar must respect

### A concrete proposal, with real entries

Nobody has written this file yet, so here is a starting shape with three actual
verdicts from the committed stores dropped into it. Argue with it — but argue
against something specific rather than starting from a blank page. Every value
below is real: the ids and evidence come from the stores, the coordinates from
the dossiers ✅.

```jsonc
// public/areas/parking-verdicts.json
{
  "version": 1,
  // Keyed by OSM id IF the id question (section 14) resolves that way.
  // Every entry carries the evidence that justified it, so a wrong call is one
  // line to remove — the nonhiking-trails.json discipline.
  "lots": {
    "node/1924424411": {
      "verdict": "DROP",
      "reason": "not-public",
      "lat": 33.5071, "lon": -111.9520,          // so a positional join works too
      "name": null,
      "evidence": "underground garage under The Phoenician resort; access: resort",
      "serves": "Cholla walk 2926 m",
      "confidence": "strong",
      "judged": "2026-08-01",
      "src": "vision+user"
    },
    "way/1507563904": {
      "verdict": "KEEP",
      "reason": "named-trailhead",
      "lat": 42.9009, "lon": -72.0765,
      "name": "Pumpelly Trail Parking",
      "evidence": "Z2: roadside pull-off on Dublin Lake Rd (tiny, informal)",
      "serves": "the named Pumpelly trailhead; our geom ends 2.2 km short",
      "confidence": "strong",
      "coverage_gap": true,                        // feeds TASKS #54's punch-list
      "judged": "2026-08-02",
      "src": "opus-ne"
    },
    "way/167025471": {
      "verdict": "KEEP",
      "reason": "dual-use",
      "lat": 44.2694, "lon": -71.3029,
      "name": null,
      "evidence": "two large graded summit parking areas, rows of parked cars (Mount Washington summit)",
      "serves": "Tuckerman Ravine Trail at 3 m, walk 98 m",
      "confidence": "certain",
      "judged": "2026-08-02",
      "src": "opus-ne"
    }
  }
}
```

Only DROP entries change anything. KEEP entries are still worth storing — they
are the record of what was checked, they stop a lot being re-judged, and they
make the file auditable rather than a list of deletions with no context.

Suggested `reason` vocabulary, taken from the drops that actually exist:
`not-public` (resort / private estate / commercial), `not-a-lot` (vision, the
rare EXISTS failure), `too-far` (>1609 m walk with no trailhead signal),
`facility-only` (serves the building, not the trail).

**Wire it into `build-parking-pool.py`'s `consider()`**, which is the one
function every lot passes through — geom lots and sidecar lots alike. A DROP
there removes the lot from the pool once, for every area, which is the same
single choke point the containment gate has. Add the empty-area guard beside it.


### The global pool

`scripts/build-parking-pool.py` builds `cdn.trekdex.app/parking.json`. Run fresh
this session ✅:

```
parking pool: 30840 lots from 6319 area(s), 12732 duplicate copies merged
  from shipped geom 29179  + pre-ownership sidecar 1661 NEW (of 3233 read)
  named 7619  federal 2837  trailhead-flagged 6134  fee known 5511
  wrote 1.39 MB raw
```

Key facts:

- It reads **shipped geom first, deliberately** — a lot that already ships keeps
  its exact position and identity, and a sidecar lot within 40 m merges INTO it
  rather than displacing it.
- It takes repeatable `--extra <path>` sidecars. `public/areas/parking-pool.json`
  is the existing one (the **pre-ownership** federal set from task #44): `{version,
  states: {CODE: [lots]}}`, 51 states, 3,233 lots ✅.
- **A missing sidecar is not an error.** That is the established pattern for a
  new one.
- It is invoked in `.github/workflows/sync-geom-to-r2.yml` around line 215 ✅:
  ```
  python3 scripts/build-parking-pool.py --out /tmp/parking.json \
    --extra public/areas/parking-pool.json
  ```
- Output is **not committed** — it is built fresh and pushed to R2 with a 300 s
  cache TTL.

⚠️ Note the naming collision waiting to happen: `public/areas/parking-pool.json`
is the *input* sidecar; `parking.json` on R2 is the *output*. A verdicts sidecar
is a third file. Name it clearly.

### The app

- `ios/SouthMountainExplorer/Services/ParkingPoolService.swift` fetches
  `https://cdn.trekdex.app/parking.json` with ETag revalidation, buckets lots into
  ~275 m cells, and exposes them for merging.
- `Area.mergingPool(own, pooled)` unions an area's own lots with the pool,
  deduped at ~1 m grid precision.
- `Area.nearestParking` / `nearestParkingWithFallback` then rank them (section 5,
  lesson 1).
- All three display paths — map pins, camera frame, trail-row banner — go through
  the merged list. Wiring only one of them names a lot with no pin under it.

### The refuse-to-empty guard — copy this exactly

Both the publisher and the sweep for `nonhiking-trails.json` refuse to let a
sidecar empty an area. Copy the pattern.

`trailforge/serve/publish_areas.py` line 642 ✅:

```python
if not row["trails"]:
    # Never let the sidecar empty an area; that would remove it
    # from Browse on the strength of an external dataset.
    print(f"  {slug}: sidecar would empty the area — ignoring it")
```

`scripts/sweep-nonhiking-trails.py` line 62 ✅:

```python
# An area must never be emptied by a curation sidecar — that would make
# it vanish from Browse entirely on the strength of an external dataset.
if gone and not keep:
    print(f"  !! REFUSING to empty {slug} — {len(gone)} flagged, 0 would "
          f"remain. Review the sidecar for this area.", file=sys.stderr)
    continue
```

For parking the equivalent rule is: **a verdicts sidecar must never remove the
last lot serving an area.** Leaving a real trailhead unmarked is a smaller harm
than telling a hiker a park has nowhere to park.

### The shape to copy

`public/areas/nonhiking-trails.json` ✅ — 13 areas, 18 verdicts:

```json
{
  "ashley-national-forest-ut": {
    "little-man-mine-fr-575": {
      "distanceMi": 1.15,
      "evidence": "BASSETT SPRINGS X-C SKI LOOP",
      "name": "Little Man Mine - FR 575",
      "reason": "usfs-snow-trail",
      "share": 0.889,
      "terraShare": 0.0
    }
  }
}
```

Every verdict carries the evidence that justified it, so a wrong call is one line
to remove. Do the same.

---

## 16. What is banked

✅ counted directly from the stores this session:

| Store | Entries | Distinct OSM ids | Verdicts |
|---|---|---|---|
| `phx_verdicts_osm.json` (4 Arizona areas) | 98 | 100 | 60 KEEP / 38 DROP |
| `ne_verdicts_osm.json` (5 New England areas) | 84 | 84 | 70 KEEP / 14 DROP |
| `zion-wilderness-ut_verdicts2.json` | 57 | 58 | 40 KEEP / 17 DROP |
| `griffith-park-ca_verdicts2.json` | 57 | 57 | 43 KEEP / 14 DROP |
| **Distinct OSM ids across all four** | | **299** | |

Per-area results as recorded in the README 📋:

| Area | Keep | Drop |
|---|---|---|
| Zion Wilderness, UT | 40 | 17 |
| Griffith Park, CA | 43 | 14 |
| Phoenix Mountains Preserve, AZ | 28 | 25 |
| Pinnacle Peak Park, AZ | 11 | 6 |
| Usery Mountain, AZ | 21 | 7 |
| Camelback / Echo Canyon, AZ | 16 | 6 |
| New England, 5 areas combined | 67 | 13 |

Camelback/Echo Canyon's buffered bbox overlaps the Preserve, so its lots are
mostly the same physical lots — judged, but not published as a separate artifact.

**Generalization result:** Griffith + Zion score **0 confident-wrong** against the
user's own calls, same protocol, no re-tuning, across a desert wilderness and a
dense city. ✅ **Re-run 2026-09-13 from the committed data and still 0:**

```
zion:     rows=39 matched-agree=31 review=0 unmatched=[]   CONFIDENT-WRONG: 0
griffith: rows=28 matched-agree=20 review=0 unmatched=[]   CONFIDENT-WRONG: 0
```

**The New England batch is the proof the fan-out works.** 67 of the 80 lots were
judged by **4 parallel per-area sub-agents**, each handed `judge_protocol.md`,
the lessons, and that area's dossier / serves / context / Z2 tiles. Verified
afterwards: all 12 drops are far-fallback serves (2.1–7.3 km walks, none a close
lot wrongly dropped), and 4 tiles were spot-read by hand — the agents were
correct. User-approved 2026-08-02: *"all looks good"*.

---

## 17. The Colorado batch — prepared, never run

`/mnt/raid/trekdex/parking-adjud/co/` (1.2 GB) ✅.

- `co_areas.json` — **261 areas, 2,090 lots, 6,116 trails** ✅. Each row:
  `{slug, name, bbox, lots, trails, rel}`.
- `osm/ctx/` — **261 per-area `_ctx.osm.pbf` extracts already built** (769 MB) ✅.
  The slow part is done.
- `osm/colorado-latest.osm.pbf` (360 MB), `osm/colorado_parking.osm.pbf` (4.2 MB).
- `run_co.sh` — the per-area driver.
- `data/`, `tiles/`, `artifacts/` — **all empty** ✅. Nothing was judged.

Biggest areas by lot count: White River NF 227, Roosevelt NF 146, Pike NF 82,
Rocky Mountain NP 79, San Juan NF 77, Cherry Creek SP 73.

⚠️ `run_co.sh` depends on the env-var-aware tools that no longer exist (section
8). Fix that before using it.

---

## 18. Checking you have not broken it

Two things to run before and after any change to the rules or the stores.

**1. The stores still parse and the totals still hold.** ✅ (this is the command
that produced the numbers in section 7)

```bash
cd scripts/parking-adjud/data && python3 -c "
import json,collections
S=['phx_verdicts_osm.json','ne_verdicts_osm.json',
   'zion-wilderness-ut_verdicts2.json','griffith-park-ca_verdicts2.json']
V=[v for f in S for v in json.load(open(f)).values() if isinstance(v,dict) and v.get('verdict')]
print(len(V), dict(collections.Counter(x['verdict'] for x in V)))"
# expect: 296 {'DROP': 83, 'KEEP': 213}
```

**2. Nothing is confidently wrong against the user's own calls.** This is the
real bar, and it is the one that proved the rules are not Zion-overfit.

```bash
cd scripts/parking-adjud
PADJ_TMP=$PWD/data python3 tools/score2.py zion \
  data/zion-wilderness-ut_verdicts2.json data/zion-wilderness-ut_dossier.json
PADJ_TMP=$PWD/data python3 tools/score2.py griffith \
  data/griffith-park-ca_verdicts2.json data/griffith-park-ca_dossier.json
```

Actual output, ✅ run 2026-09-13:

```
zion:     rows=39 matched-agree=31 review=0 unmatched=[]
CONFIDENT-WRONG: 0  <-- must be 0
griffith: rows=28 matched-agree=20 review=0 unmatched=[]
CONFIDENT-WRONG: 0  <-- must be 0
```

`matched-agree` sitting below the row count is expected — the remainder are
`leaning` ground-truth rows the protocol answered differently without
contradicting a confident call. **The exit code is 1 if any confident-wrong
appears**, so this works as a gate.

A third, cheap sanity check: `PADJ_TMP=$PWD/data python3 tools/merge_ne.py`
re-validates the four New England drafts and reprints every DROP with its
evidence. It should report **no schema issues** and 55 keep / 9 drop / 3 review ✅.

---

## 19. What could still be wrong

Written deliberately, because a handoff that only lists what is known produces an
overconfident successor.

- **The protocol has been tested on three morphologies, not four.** Desert
  wilderness, dense city, sparse eastern forest. **Colorado is alpine and
  national-forest and has never been run.** Expect the first CO area to teach
  something — run one and read every row before fanning out. Treat an unchanged
  rule set surviving CO as a result worth recording, not as the expected outcome.
- **The >1 mile over-keep test rests on a small sample.** It measured 0 across
  the 6 original areas 📋. Six areas is not a national claim, and lesson 7 already
  carved out the named-trailhead exception after New England showed it was
  needed. Watch it in the next batch.
- **40 m clustering may merge lots that a hiker experiences as separate.**
  `dossier.py` unions members within 40 m and gives them one fid and one verdict.
  At a big trailhead complex — the Saguaro "19 distinct Parking Lot" case that
  killed name-only dedup — that is exactly the wrong merge. Nobody has measured
  how often it happens.
- **Urban roadside is the only genuinely review-heavy zone**, and it was called
  an honest ambiguity rather than a bug 📋. Griffith produced 6 reviews out of 57
  public-served lots — dirt park roads, roadside-with-a-few-cars, a library lot,
  street corners. A national run will hit far more of these than the tested areas
  suggest.
- **The leaf-off NAIP layer for the northeast was recommended and never
  sourced.** Lesson 8's workaround is to mark REVIEW, which does not scale: a
  batch that produces reviews nobody resolves is a batch that produced nothing.
- **Confidence is self-reported.** 47 of 296 verdicts are `leaning` ✅. Nobody has
  checked whether `leaning` calls are actually less accurate than `strong` ones.
  If they are not, the tier is decoration; if they are, `leaning` DROPs deserve a
  second look before they ship.
- **The sub-agent fan-out was verified once, by hand, on one batch.** All 12 NE
  drops were re-checked and 4 tiles re-read 📋. That is a spot check, not a
  measured error rate. Keep spot-checking every batch; do not let the fan-out
  become unsupervised because it worked once.

---

## 20. First actions for the next agent

1. ~~Repoint `TMP` in the tools~~ — **done 2026-09-13**, section 10. The tools and
   data are in the repo and run from it.
2. **Settle the ID question** (section 14). It determines the sidecar's shape,
   and everything downstream depends on it. Write the decision into `TASKS.md` #53.
3. **Build the sidecar and its consumer**, copying `nonhiking-trails.json`'s shape
   and the refuse-to-empty guard. Land the 299 banked verdicts through it as the
   first real payload, and confirm the pool changes by the expected count before
   and after.
4. **Graduate `dossier.py`, `context_classify.py`, `foot_route_area.py` and
   `judge_protocol.md` into `scripts/`**, the way
   `scripts/build-nonhiking-list.py` and `scripts/sweep-nonhiking-trails.py` were
   graduated. They are durable logic living in a scratch directory.
5. **Then scale.** Colorado is prepared and is the obvious next batch — 261 areas
   with their extracts already built. Run a couple of areas first and eyeball the
   artifact before fanning out.

---

## 21. Where everything lives

### In the repo — travels with git, works on any machine

| Path | What |
|---|---|
| `docs/parking-adjudication-handoff.md` | this document |
| `scripts/parking-adjud/README.md` | how to run the tools |
| `scripts/parking-adjud/tools/` | all 14 python tools, 2 shell drivers, `judge_protocol.md` |
| `scripts/parking-adjud/data/` | every dossier, serves gate, context, walk, verdict store, `groundtruth.json`, `coverage_gaps.json`, `QUALITY_REPORT.md`, `co_areas.json` — 74 files, 8.2 MB |
| `scripts/parking-adjud/work/` | scratch, git-ignored, created on demand |

**The tools no longer hardcode anything.** Paths come from `PADJ_TMP`,
`PADJ_GEOM`, `PADJ_PARKING_PBF`, `PADJ_US` and `PADJ_OSM`, each falling back to a
repo-relative default. Verified: all 14 compile, and `merge_ne.py` runs against
the committed data and reproduces the New England review. ✅

### On the homelab only — too big for git, regenerable

| Path | What | Size |
|---|---|---|
| `/mnt/raid/trekdex/parking-adjud/data/<slug>_ladder/` | rendered Z1/Z2/Z3 aerial tiles | ~91 MB |
| `/mnt/raid/trekdex/parking-adjud/data/<slug>_ctx600/` | raw NAIP context-frame caches | included above |
| `/mnt/raid/trekdex/parking-adjud/osm/` | per-area `_ctx.osm.pbf`, regional extracts | 182 MB |
| `/mnt/raid/trekdex/parking-adjud/artifacts/` | the rendered review HTML pages | 35 MB |
| `/mnt/raid/trekdex/parking-adjud/co/osm/` | Colorado: 261 per-area extracts + state pbfs | 1.2 GB |
| `/mnt/raid/trekdex/osm/us-access.osm.pbf` | what per-area context is cut from | 3.7 GB |
| `/mnt/raid/trekdex/osm/us-latest.osm.pbf` | full US | 12 GB |
| `/mnt/raid/trekdex/osm/cache/parking-only.osm.pbf` | national `amenity=parking` | 120 MB |

Tiles regenerate from a dossier with `z2render.py`; extracts regenerate with
`osmium extract`. Nothing here is irreplaceable, but re-cutting the US extract is
hours, so do not delete it casually.

### What a NON-homelab agent can and cannot do

**Can**, from the repo alone: read every verdict and its evidence, re-score
against `groundtruth.json`, validate schemas, reason about the rules, design and
build the sidecar and its consumer, and change any of the logic.

**Cannot**, without the homelab: build a dossier, cut an OSM extract, compute
foot-network walks, or render a tile — all of those need the multi-GB extracts
and, for tiles, network access to NAIP.

So: **the sidecar work (section 18 steps 2 and 3) is portable. Adjudicating new
areas is not.**

### Elsewhere in the repo

| What | Where |
|---|---|
| Task, with measurement history | `TASKS.md` #53 (and #51, #52, #54 which defer to it) |
| Auto-memory | `parking-vision-adjudication.md`, `parking-feature.md`, `always-spatial-index.md`, `prefer-homelab-over-network.md`, `verify-before-asserting.md` |
| Global pool builder | `scripts/build-parking-pool.py` |
| Parking enrichment | `scripts/add-parking.py` |
| Sidecar precedent | `public/areas/nonhiking-trails.json` + `scripts/sweep-nonhiking-trails.py` |
| App parking model | `ios/SouthMountainExplorer/Models/Area.swift`, `Services/ParkingPoolService.swift` |
| R2 sync | `.github/workflows/sync-geom-to-r2.yml` |

### Working rules that apply here specifically

- **Never hand-write point-in-many-polygons or nearest-neighbour as Python
  loops.** Use `scripts/_geo_index.py` (shapely `STRtree`). A 20-minute geometry
  job on this data is a bug, not big data. The tools here already follow this.
- **Prefer the homelab extracts over Overpass.** Measured: Overpass was 87% of a
  state's parking runtime.
- **Print named examples and read them.** Every threshold proposed first in this
  project was wrong, and reading the dry-run ROWS caught it each time — a 2 km
  parking cap that deleted Springer Mountain Trailhead, name-only dedup that
  collapsed Saguaro's 19 distinct lots to one.
- **Get the user's eyes on a stratified sample before building a rule.** Their
  spot-check killed four candidate curation rules in one message after a day of
  measurement had failed to.
