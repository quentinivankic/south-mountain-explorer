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
| Verdict model (3 axes) | Settled; 1,203 unique verdict clusters across 14 source areas / 4 morphologies |
| Tooling | Built in `scripts/parking-adjud/tools/`; paths are env-configurable; shadow replay + journaled resolver are local-only |
| Areas adjudicated | **14**, 0 REVIEW outcomes outstanding |
| Distinct OSM lot clusters with a banked verdict | **1,203** (1,273 source rows, 70 duplicate folds) |
| Global pool the app actually serves | **30,949 lots**, live and byte-verified 2026-09-26 |
| So: fraction adjudicated | ~3.9% of the current global pool (not a random sample) |
| `public/areas/parking-verdicts.json` | Generated deterministically from five explicit stores; consumed by sweep, pool builder and `add-parking.py` |
| Colorado batch | Indian Peaks (149) + Pike (758) complete; **258 source areas remain** |
| Data | Durable source stores and legacy evidence are in `scripts/parking-adjud/data/`; large aerial/OSM inputs stay on the homelab |
| Trust measurement | 90 explicit human labels; only 38 binary model calls have a per-lot denominator, all one DROP area/source; no class promoted |
| Autonomous routing | Shadow replay: 190 refresh / 746 blind challenge / 173 arbiter / 90 preserved explicit authority / 0 direct human exceptions. Staunton field pilot: 4 `PRESERVE_MACHINE_RESOLUTION` / 0 human exceptions. |
| Blocking | **Operational/manual freeze — not code-enforced:** do not publish `bear-creek-lake-park-co`. Its available resolver run and 4 historical human decisions predate the current prepare/review-receipt authority schemas and cannot be newly published. Regenerate/re-evaluate all 50 rows, rebind accepted decisions through current frozen review evidence, resolve every exception, pass hostile review, and obtain an explicit thaw before store/sidecar/geom/pool/R2/live publication. Staunton's four DROPs are already published and live-verified. Direct model promotion remains disabled. |
| It blocks | TASKS **#51**'s containment roll and **#52**'s polygon merge 📋 |

**The ID problem is settled in section 14. The current design boundary is the
shadow trust contract in section 18A: automate refresh/challenge/arbitration,
and involve the user only after evidence remains contradictory.**

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

## 5. The eleven load-bearing lessons

**Every one of these came from a real user correction or a measured failure.
They are the most valuable thing in this document. Do not quietly re-derive
around them.** 📋 for all eleven (sourced from auto-memory `parking-vision-adjudication`).

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
- **NAIP is the host's primary imagery source**, not ESRI. Its endpoint and
  fetch credentials/configuration stay host-side; agent assignments contain
  only frozen image paths, and orchestration must disable browser/network tools.
  The Python preparers do not themselves provide a network sandbox. ESRI World Imagery **throttles under burst** — it fires fast failures and then
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
sign). REVIEW is for a call that a HOST-FETCHABLE frame would change: canopy
over a surveyed prior, or a clear NAIP frame for a clouded ESRI one. Agents do
not fetch it; request the exact host-frozen frame in `resolve_hint`, then decide
on the next bound run. When the only thing that could rescue a lot is unobservable from the
air and everything visible says DROP, the verdict is DROP, `leaning`, with the
doubt in the evidence string. New human authority uses receipt-bound
`merge_drafts.py --decide` for a flip or `--confirm` for same-verdict acceptance;
the unbound `--set` CLI is retired and exists only as legacy replay code.
`tools/calibration.py` counts the resulting explicit decisions.

### 11. Public ownership and proximity are not parking permission

Bear Creek's first autonomous run failed a high-bar audit because the same
model family repeatedly turned weak context into permission:

- government ownership or a whole park/venue marked `PUBLIC_ACCESS=Open`;
- no gate visible in aerial imagery;
- road-right-of-way classification;
- a finite walk below one mile;
- a trail line touching or passing a mapped footprint.

None proves that **this lot** is legal public trail parking. Apply the axes in
order:

1. **EXISTS:** a roadside feature needs a vehicle-access throat and a bay outside
   the travel lane, cars/stalls, or a clearly delineated graded surface. A
   continuous curb, sidewalk, verge, through-lane, or building footprint is no.
2. **PUBLIC:** require lot-level access tags, an official access/trailhead record
   at the lot, or an authoritative statement granting parking. Public ownership,
   an open enclosing property, and no visible gate are only context.
3. **SERVES:** only after PUBLIC=yes, require direct trail access or a named/
   official trailhead relationship. Distance alone does not convert school,
   church, commercial, restaurant, transit, event, VIP, pit, resident, or
   employee parking into trail overflow.

Same-family external evidence can improve the human exception packet, but under
resolver v2 it is never independent authority and cannot authorize a machine
resolution—even when every call matches and the source is novel. Every source
still needs a durable official locator, retrieval/update timestamps, exact
frozen bytes/hash, structured support matching the selected call, and an
axis-local citation. If no arbiter whose model ID and family differ from every
parent reaches exact verdict-and-axis agreement, produce REVIEW/human exception
rather than manufacturing confidence.

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
3. **No other evidence assignment.** Dispatch only the generated prompt and its
   named run-local files, with browser/network tools disabled. Packet tags can
   contain URL strings; those are data, not fetch permission. The human-only
   review sheet is separate, deterministic, and self-contained. It embeds
   frozen run-local PNGs plus the complete frozen packet payload and normalized
   publication facts; OSM IDs and coordinates are labels, not live evidence or
   fetch permission.

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

### Measured, end to end, on a real area ✅

`grafton-notch-state-park-me` (6 served lots) re-run from the repo copy on
2026-09-13, against its existing `_ctx.osm.pbf`:

| Step | With the NATIONAL parking pbf | With a REGIONAL parking pbf |
|---|---|---|
| `dossier.py` | **6 m 50 s** | **14.7 s** |
| `foot_route_area.py` | 9.1 s | 9.1 s |
| `context_classify.py` | ~2 s | ~2 s |

⭐ **Set `PADJ_PARKING_PBF` to a regional extract. It is 28× faster and it is the
whole difference between a batch being feasible and not.** `dossier.py` scans the
entire parking pbf for every area, so pointing it at the 120 MB national file
costs seven minutes per area; a state or region extract costs fifteen seconds.
This is why the Colorado prep built `colorado_parking.osm.pbf` (4.2 MB) — at the
national rate its 261 areas would be about 30 hours of dossier building alone.

```bash
osmium tags-filter -o region_parking.osm.pbf <region>.osm.pbf \
  n/amenity=parking w/amenity=parking r/amenity=parking
export PADJ_PARKING_PBF=$PWD/region_parking.osm.pbf
```

**The rerun reproduced the committed result exactly** ✅ — 57 facilities, and the
same 6 served OSM ids as the August run:

```
node/5814874288  node/5897812888  node/5912066241
way/243581117    way/244102591    way/730327047
```

One caveat worth knowing: the same area run against the *national* pbf found 60
facilities rather than 57, because that extract is newer than the August regional
one and the bbox has picked up three more mapped lots. The **served** set was
identical either way. So drift in the lot universe is normal; drift in the served
set would be a signal.

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

All in `scripts/parking-adjud/data/`; see section 21 for the rooted artifact
inventory. The rendered tiles are NOT in the repo; they stay at
`/mnt/raid/trekdex/parking-adjud/data/<slug>_ladder/`.

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
refused from emptying an area by default. Only exact, committed, manually
reviewed empty-population signatures may proceed; section 15 defines that
narrow policy.

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

> **DECIDED 2026-09-13 — (c) now, plus the one line of (a).** The sidecar is
> keyed by primary OSM id for readability, but a shipped lot is matched by
> **footprint**: at the bounding-box centre of one of the verdict's rings,
> inside the polygon, within 10 m of its edge, else within 20 m of its
> position — the most confident rule wins, then the nearest verdict. Not a fixed
> radius — measured against the 40,339 shipped lots, the out-center vs
> centroid gap runs to 41 m on big lots, and a circle that wide would have
> swallowed unjudged neighbours in Griffith. The footprint rule also accepts a
> lot at a ring's bounding-box centre exactly — that is the point `out center`
> ships, and it is how a crescent or L whose centre falls off its own pavement
> (Stough Park's lot, 42 m from the centroid) is still recognised.
> `add-parking.py::parse_parking` now emits `osm`, so a lot rolled after this
> date whose id the sidecar has judged matches by id exactly; an id the sidecar
> has NOT judged still falls back to footprint and position on purpose (ids
> churn when a node is redrawn as an area; the verdict is about the place). The
> matcher is `scripts/_parking_verdicts.py`; the generator is
> `scripts/build-parking-verdicts.py`; the decision and the first payload's
> numbers are in `TASKS.md` #53.

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
single choke point the containment gate has. Last-lot removal is decided by the
sweep's reviewed-signature policy below; the pool builder keeps its separate
conservative guard when handed a still-nonempty geom.


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

### Refuse by default; exact reviewed-empty exceptions

The parking sweep refuses last-lot removal by default.
`_REVIEWED_EMPTY_SIGNATURES` in `scripts/sweep-parking-verdicts.py` is the
source for the code-approved versioned policy registry, not an operator force
switch. An area may empty only when its complete matched DROP population
exactly equals the recorded `(verdict key, reason)` tuple sequence, including
multiplicity. A changed key, reason, or matched count restores refusal;
evidence- or confidence-only edits are not part of this signature. The CLI
exposes no force option.

Every nonempty footprint must be an explicitly closed, finite, nonzero-area
polygon with at least four points and three distinct vertices, a diagonal no
larger than 2,000 m, and its verdict position inside a ring or no farther than
100 m from the nearest edge. `rings=[]` remains valid. Control, format, and
line-separator characters are rejected throughout sidecar and geom strings;
all report-bound untrusted values are also rendered through one single-line
escaping helper.

`--dry-run` is recursively nonmutating and begins `DRY-RUN — would remove`.
It prints `REVIEWED-EMPTY ... exact signature` for an approved match and
`REFUSING ...` otherwise. A refused apply exits 2, mutates nothing, and begins
`NOT APPLIED — 0 lots removed; planned N`; details are under `planned
removals:` and never claim an applied removal. Check both stdout and stderr.
The current reviewed signatures are:

- Mesa Valley / `way/58294967:not-public`
- Promntory Point / `way/1206954210:not-public`
- Sondermann / `way/58294967:not-public`

Before canonical mutation the sweep durably verifies exact BEFORE backups,
AFTER stages, an owner-only exact sidecar snapshot, and its PREPARED journal.
Recovery ignores mutable current sidecar bytes/inode and current policy: it
independently derives paths and transaction ID, accepts only hardcoded approved
historical policy version+bytes, reconstructs the original plan from retained
authority, and rolls forward only an exact AFTER prefix/BEFORE suffix. Unsafe
target or inventory drift refuses deterministically without further mutation.

All canonical geom writers share the persistent gate: `add-parking.py`,
`merge-published-geom.py`, the federal/degenerate/name/nonhiking sweep tools,
`backfill-area-boundary-ids.py`, `recompute-difficulty.py`, standalone
`trailforge/serve/add-elevation.py`, non-cache-only `build-trail-counts.py`
(including its low-level `_seed_constants.write_geom_file` helper), and
canonical `trailforge/serve/publish_areas.py` or direct canonical
`to_app_json.py` output all hold the same geom EX plus live-journal lease and
refuse while PREPARED exists. Artifact and derived output directories remain
outside this gate. The canonical sidecar builder joins the
sidecar/live-journal lease too. `add-parking.py` recaptures exact sidecar bytes
and every geom source under one globally sorted commit lease before its first
write, so a stale roll cannot restore a DROP.

The live journal remains until all targets and the receipt are durable and the
bound stdout/stderr report has been printed and flushed. Only then is it moved
to Archive and both parents fsynced. A crash before or during reporting leaves
the journal live; rerunning the exact sweep command replays the bound report,
finishes archival, and does not recompute authority from current inputs.

Run the sweep before the pool build. `build-parking-pool.py` retains its own
conservative unconditional guard when handed a still-nonempty geom; it does not
implement the reviewed-signature policy. Once the committed sweep has written
`parking: []`, those areas contribute no lot to the pool.

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

## 17. The Colorado batch — running; 1 of 261 areas done

**2026-09-13: `indian-peaks-wilderness-co` went end to end**, the first area
judged with the fan-out tooling now in `scripts/parking-adjud/tools/`:
- Homelab: `bash tools/run_co.sh <slug>` (~70 s; 279 facilities → 157 served →
  **149 public-served**, 77 surveyed, 23 fallback-served), then
  `ladder_tiles.py <slug> all-served` renders Z1/Z2/Z3 for every served lot
  (447 frames, ~8 s each, three workers in parallel via `padj_missing.py`).
  ESRI alone failed 61 of 61 Z3 frames; the tool is now **NAIP-primary with
  ESRI fallback**, 1 s pacing, 0/6/15 s retries, source named in the header.
- Mac: rsync the `_z[123].png` frames and the four JSONs, then
  `judge_packets.py <slug> --tiles DIR --skip-judged <every store>` writes one
  packet per lot (prior, tags, serves, walk, context, tile paths) in chunks of
  15, and a prompt per chunk from `judge_agent_prompt.md`.
- Ten `general-task-execution` judge agents in parallel, each with
  `judge_protocol.md` + `judge_lessons.md` + its chunk. Current agents rewrite
  only their assigned `<slug>_verdict_continue_NN.json` after every lot; the
  host captures and validates that continuation once, then merges it into the
  canonical draft under the area lock. Canonical drafts/checkpoints are never
  agent-owned. 149 lots took about 40 minutes of wall clock across two launches.
- `merge_drafts.py data/co_verdicts_osm.json <slug>` validates (schema,
  coverage against `_pub.txt`, Z3 required for a surveyed-prior DROP) and
  prints every DROP with evidence and tile path; current human review uses
  `judge_review_sheet.py <slug> --authority-run <run>`, which writes one
  deterministic self-contained HTML page plus a self-hashed review receipt
  under `.review-artifacts/<slug>/<run-id>/`. Review generation holds the area
  `LOCK_EX` through both immutable installs; a sheet without its exact receipt
  is not authority. Indian Peaks historically used loose HTML and `--set
  FID=VERDICT` for two flips; neither can create current authority. Current
  flips require receipt-bound `--decide --review-receipt`; every `--decide`,
  `--confirm`, and `--write` mutation also requires explicit `--judged
  YYYY-MM-DD`. For `--reviewed KEEP=sample`, `calibration.py add` requires the
  canonical absolute `--review-receipt`; `--legacy-sample-manifest` is only for
  explicit replay of the obsolete sample manifest. `--write --resolver-run
  PATH` folds one terminal area into the store with
  `lat/lon/rings/name/judged` embedded, so **no Colorado dossier is committed**
  (`build-parking-verdicts.py` places a self-contained entry from the entry).
- **Result: 109 KEEP (51 certain / 38 strong / 20 leaning), 40 DROP, 0 REVIEW.**
  Every DROP fails SERVES; the two judge REVIEWs were flipped to DROP by the
  user and became lesson 10. Sweep removed 13 shipped pins in the four
  overlapping areas (Cozens Ranch 6, Roosevelt NF 5, Arapaho NF 1, RMNP 1);
  `--add-keeps` added 14 new pins; pool 30,885 → 30,886.
- **Scale finding:** the judge set per area is the served set (~50–150 lots),
  not the shipped count (7 here), so Colorado is roughly 15–25k judgments
  before `--skip-judged` dedupe. Areas overlap heavily; the dedupe matters.
- **Calibration** (`tools/calibration.py`, `data/calibration.json`): one area
  in, DROP 38 reviewed / 0 flipped (miss rate ≤ 9.2% at 95%), KEEP unmeasured.
  189 zero-flip DROP reviews reach a 2% bound, 381 reach 1%.

What was prepared beforehand, still true:
`/mnt/raid/trekdex/parking-adjud/co/` (1.2 GB) ✅.

- `co_areas.json` — **261 areas, 2,090 lots, 6,116 trails** ✅. Each row:
  `{slug, name, bbox, lots, trails, rel}`.
- `osm/ctx/` — **261 per-area `_ctx.osm.pbf` extracts already built** (769 MB) ✅.
  The slow part is done.
- `osm/colorado-latest.osm.pbf` (360 MB), `osm/colorado_parking.osm.pbf` (4.2 MB).
- `run_co.sh` — the per-area driver.
- `data/`, `tiles/`, `artifacts/` — empty at hand-off; Indian Peaks now lives in
  `~/south-mountain-explorer/scripts/parking-adjud/work/co/` on the homelab
  (dossier, serves, context, walk, 447 frames) and its verdicts in the repo store.

Biggest areas by lot count: White River NF 227, Roosevelt NF 146, Pike NF 82,
Rocky Mountain NP 79, San Juan NF 77, Cherry Creek SP 73.

`run_co.sh` is in the repo and now resolves its own directory; it already exports
`PADJ_PARKING_PBF=.../colorado_parking.osm.pbf`, which is the setting that makes
a batch this size feasible at all — see the timings in section 9. At the regional
rate, 261 areas is roughly an hour of dossier building plus the foot-route and
context passes; at the national rate it would be about 30 hours.

---

## 18. Checking you have not broken it

Three things to run before and after any change to the rules or the stores.

**1. The complete rooted corpus and committed sidecar still agree.**

```bash
python3 scripts/build-parking-verdicts.py --check
```

This validates the literal-pinned publication root before deeper semantics; the
exact five store/proof-registry-state/floor images; the schema-v2 legacy
baseline and 11 rooted dossiers; current proof/floor policy; and the canonical
1,203-cluster sidecar compiled from 1,273 source rows.

**Historical original-four subcorpus check only.** This produced the section 7
numbers, but it is not the authoritative five-store publication gate:

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

### 18A. Shadow trust replay — autonomous by default, promotion by evidence

`tools/trust_engine.py` is pure policy code and `tools/replay_trust.py` is its
read-only corpus harness. The CLI verifies that the five source stores still
compile exactly to the committed sidecar, reconstructs pre-override judge calls,
joins the calibration ledger, and coordinate-replays historical labels. It has
no write path into drafts, stores, sidecars, geom, pools, workflows, or live
data.

```bash
cd scripts/parking-adjud
python3 tools/replay_trust.py --format summary
python3 tools/replay_trust.py --include-items \
  --out work/shadow/parking-trust-shadow-v1.json --format summary
python3 tools/replay_trust.py --work-area "$SLUG" --tmp "$PADJ_TMP" \
  --out "$PADJ_TMP/shadow/${SLUG}_trust-shadow-v1.json" --format summary
```

Default output is deterministic JSON on stdout. A report file is allowed only
outside the repository or under ignored `work/`. The item form is the machine
queue for the next autonomous stages:

- `AUTONOMOUS_REFRESH`: rebuild legacy/schema-deficient evidence with the current
  packet and a fresh judge;
- `AUTONOMOUS_BLIND_CHALLENGE`: independently challenge a clean certain/strong
  call without exposing the primary answer;
- `AUTONOMOUS_ARBITER`: use only host-frozen offline evidence to resolve
  leaning, REVIEW, fallback, no-route, or coverage-gap cases;
- `PRESERVE_HUMAN_AUTHORITY`: retain an existing explicit human-influenced
  outcome without counting it as a model success;
- `HUMAN_EXCEPTION`: only after the autonomous stages still conflict. The
  committed-corpus replay creates zero such work.

Measured 2026-09-26 baseline after Staunton: 1,273 source rows fold to 1,203
unique clusters (863 KEEP / 340 DROP); 965 pass the current schema and 238 are legacy/not-current.
There are 90 explicit human labels and 97 rows in the broader human-influenced
union. Only 38 binary primary calls have a per-lot review denominator, all DROP
from one area/source and shown alongside the model answer; 0 observed flips still
means a 9.2% Wilson U95. KEEP is unmeasured. The strict evidence screen finds 100
KEEP + 20 DROP candidates; it **does not approve them**.

Direct model promotion requires the exact class to meet KEEP ≤1% or DROP ≤2%
Wilson U95 from per-decision `blind_reviews` records, across at least three
areas and two verified reviewer families. The primary must carry
`judge_provenance`; each reference binds a full independently produced decision,
a known promotion-grade reviewer identity/family/kind, frozen primary and
reference decision hashes, a shared packet hash, and distinct prompt/evidence
hashes. Identity keys are trimmed, case-folded and slug-validated before any
unknown/same-family comparison or breadth count. Unknown, noncanonical or
same-ID/family review, model-visible area booleans, model-only challengers,
mismatched packets, and malformed/mismatched hashes cannot promote a class. Current promotion evidence is n=0
for both classes. Same-model fan-out never counts as independent because it
shares prompts, imagery, packet construction, and systematic errors.
Historical Zion labels are recorded as in-sample regression (39 labels, 31
predicted agreements, 8 missing predictions); Griffith's 28 labels are model
self-consistency only (20 agreements, 8 differences), not human truth.

This policy spends compute first: refresh → blind challenge → autonomous
arbiter. It does not manufacture confidence from final verdicts, self-reported
confidence, circular labels, or unblinded agreement.

**Resolver activation:** `trust_resolution.py` and `resolve_trust.py` now
consume the machine queue through deterministic prepare, read-only status, and
an explicit one-chunk `--apply`. The selected complete decision/evidence lands
at top level; immutable full primary/challenger/arbiter envelopes and every
model/packet/prompt/evidence/decision hash stay under `trust_resolution`.
`original_judge_projection` still returns the primary, while a second checkpoint
vector binds the pre-human machine result. Human `override` remains an outer
layer and is never used for autonomous work.

Distinct-family agreement requires exact verdict and EXISTS/PUBLIC/SERVES
calls. An arbiter resolves only when it exactly matches a confident parent and
its model ID and family differ from every available parent. Any duplicate model
ID or family among participating v2 votes—including same-family parents plus an
otherwise independent arbiter—is a human exception. Every remaining same-family
v2 outcome is a human exception regardless of novel citations. The host
stable-captures each external manifest, artifact, and metadata file once through
pre/post no-follow fd+entry checks, writes only those buffers into
content-addressed run paths, and fully reloads/validates the completed run before
returning it for dispatch. Arbiter orchestration must disable browser/network
tools and assign only the generated
prompt plus named run-local files; these preparers do not supply a sandbox.
Arbiters may name only catalog-assigned IDs and structured support; they cannot invent
paths, locators, timestamps, or hashes. The envelope persists the HTTPS source
locator, UTC retrieval/update timestamps, exact content/manifest/metadata
hashes, and axis-local claims, but none of that grants same-family authority.
Preparation schema v2 binds the absolute run root, closed
model/source/item/assignment schemas, complete primary decisions, host evidence
catalog, prompt templates, exact `judge_protocol.md`/`judge_lessons.md` bytes,
and exact dossier/`_pub.txt` bytes plus normalized aliases/location/rings/name.
Area slugs are separator-free. Resolver prepare owns the canonical area
`LOCK_EX` from source capture through completed-run reload. Every consumer
requires `prepare.json` to be strict duplicate-free JSON whose exact bytes equal
the canonical serialization; whitespace-only and duplicate-key rewrites fail
before review, authority, or publication. Canonical
resolver/authority/publication target installation pins and hashes source bytes, copies them into a fresh private
single-link inode in the trusted target directory, fsyncs and verifies it,
renames that unpredictable private entry, then verifies the installed inode and
bytes. It does not rename the deterministic source stage. The shared
`trusted_filesystem.py` boundary requires target/lock parents owned by the
current euid with no group/world write and trivial descriptor-bound ACLs.
Unsupported or failed ACL inspection and inherited/extended ACLs fail before
mutation. Lock inodes must be regular, single-link, mode 0600, with repeated
fd↔live-entry identity checks. Canonical old 0644 lock
inodes are tightened in place without rotation. Portable POSIX cannot bind the
final private-name lookup against a malicious same-UID peer inside that trusted
directory, so every such process must obey the canonical lock. `merge_drafts`
owns every opened resource/area handle in one outer cleanup scope; normal
completion, unexpected post-lock exceptions, and partial open/`flock` failures
all drain the acquired handles while preserving the original failure. The run
retains immutable pre-apply packet/draft/checkpoint buffers for route
rechecks, so an applied sibling cannot change another chunk's prepared route.
Packet JSON, dossier, judge set, drafts, and checkpoints are each parsed/hashed
from one immutable read. Each source PNG is accepted only when its pre/post fd
and ladder-entry device/inode/mode/link/size/mtime/ctime stay identical around
one read, then copied to `tiles/<sha256>.png`; later routing/proof uses only
frozen run-local packets. `judge_packets` similarly commits only one stable
continuation capture, writes a deterministic full-hash archive, and moves—not
deletes—the live entry into owner-only quarantine. A newer final-race entry is
preserved at a reported recovery path, never silently lost.
Apply shares a canonical area lock with `judge_packets`; calibration/store
writers take their global resource lock first and then sorted area lock(s), so
cross-area whole-file updates serialize without reversing lock order. Under
those locks the resolver rechecks current authority and, on the first real
apply, freezes the whole run's role outputs into content-hashed `sealed-inbox/`
bytes plus `output-seal.json`, including the all-items READY resolution snapshot.
Planning, receipts, and recovery thereafter ignore mutable live inbox files; a
journal without that matching seal, with changed resolution, or with any
unresolved sealed sibling cannot write canonical state. It enforces draft+checkpoint CAS, durable backup,
PREPARED journal bound to the seal, one atomic draft replacement, exact receipt,
and retry recovery. No ready-looking chunk can plan, write a preservation
receipt, or begin a transaction while any sibling is pending, blocked, or in
recovery; only an already-durable journal may finish. A live journal dominates
any receipt, and preserved-only chunks get durable terminal receipts.

`--decide` and `--confirm` use pure planner helpers, but the CLI commands are
mutating receipt/draft/checkpoint transactions. Before either command, generate
canonical review evidence with `judge_review_sheet.py SLUG --authority-run RUN`.
The renderer uses only the validated frozen run and embeds, for every item, the
complete packet payload except replaced tile paths, the full normalized
publication facility as canonical JSON, the exact primary decision, and the
frozen PNG bytes. Under one area `LOCK_EX` it installs immutable owner-only
single-link `review.html` and self-hashed `review-receipt.json` under
`.review-artifacts/<area>/<run-id>/`; arbitrary `--out` is forbidden. A sheet
alone is non-authoritative, and an exact retry completes the pair without inode
rotation. The human command must pass that exact canonical `--review-receipt`.
Sample calibration with `calibration.py add --reviewed KEEP=sample` also requires
the canonical absolute `--review-receipt`; the mutually exclusive
`--legacy-sample-manifest` flag exists only to replay the obsolete manifest.

Under the global store lock then area lock, the CLI reconstructs the exact
request and reviewed item from the frozen authority run, validates
schema/packet/overlap, complete review sheet/receipt hashes, and whole-area
packet/draft/checkpoint/dossier/public-set freshness, stages exact backups and
after-images, writes a durable journal, then installs receipt → draft →
checkpoint. The checkpoint changes only `draft_sha256`. New confirmation v2,
override v3, and authority receipt v2 bind prepare/primary/packet/source,
reviewer/date/note/effective decision, and review receipt/sheet/item hashes.
Failed preflight leaves canonical targets unchanged. Legacy receipt-bound
schemas remain replayable but cannot be newly emitted or published as current
authority. A live authority journal blocks
merge, packet generation, calibration/review-sheet writes, resolver work, and
replay. Recovery requires rerunning the complete original `--decide` or
`--confirm` invocation with the same authority run, canonical review receipt,
and explicit `--judged` date; any changed field or noncanonical
target/stage/backup fails without advancing files. The unbound `--set` CLI is retired. Store merge reopens receipts/source runs and
full packets/tiles, rejects draft-injected `src` or geometry, and expands the
full transitive alias component across same- and cross-area holders. Every write accepts one area and requires
an explicit terminal `--resolver-run` whose bound dossier/judge-set bytes still
match, so PENDING/BLOCKED primary rows and later dossier aliases cannot bypass
the resolver. Merge writes run-bound publication fields plus a row reference to
the separate canonical `<store>_publication_proofs.json` registry. That proof
contains the actual prepare, output seal, byte-exact base64 sealed outputs and
rendered prompts, chunk receipts with complete per-fid terminal vectors,
packet bindings, exact final decision/authority rows, and each current human
authority receipt plus an exact deduplicated review bundle (receipt, sheet,
prepare, and every frozen source artifact needed to reconstruct it). New proof
and attestation v2 independently replay the wrapper → authority receipt → review
receipt → sheet/source chain; legacy v1 proof remains replay-only. Canonical replay independently derives assignment and prompt hashes,
reconstructs persisted envelopes from sealed outputs, recomputes whole-run
READY, exact-compares final rows, reconstructs the complete terminal checkpoint
manifest and validates its raw draft/checkpoint hashes, then validates
machine/human terminal membership. The strict canonical
`publication-trust-root-v1.json` is the Git-reviewed trust anchor for the
complete publication image. Its exact bytes are pinned by the literal
`PUBLICATION_TRUST_ROOT_SHA256` in `build-parking-verdicts.py`; schema v1 binds
generation/parent lineage, all five configured store images, all five
proof-registry states (including exact `null` absence), all five self-hashed
append-only publication floors, the exact schema-v2 1,273-row/11-dossier
baseline, and the sorted exact 11-dossier byte inventory. The generated sidecar
is derived and deliberately not rooted. Replay/build take root `LOCK_SH`;
terminal publication takes root `LOCK_EX`, the target publication resources
exclusively, and sibling resources shared.

The rooted journaled transaction installs exact after-images in one valid
prefix order: proof → floor → store → non-authoritative successor candidate.
The floor preserves every admitted key's first authority, attestation, and
proof identity. Every stage is exact-byte verified before the journal is
archived. A prefix crash after proof, floor, store, candidate, or archive
installation recovers only by rerunning the exact original one-area
`merge_drafts.py ... --resolver-run RUN --judged DATE --write` command;
different runs, dates, bytes, or malformed stages/backups/candidates/archives
fail closed. The tracked root and code pin never change automatically. Review
the generated candidate, replace the tracked root with those exact bytes, and
update the literal pin plus pin assertions in one reviewed Git change. Until
that promotion, replay/build and every second publication reject the live image
against the old root. Sidecar builder and replay hold sorted shared resource
locks through root/store/proof-state/floor/journal/baseline/dossier capture,
semantic validation, compilation, and final sidecar comparison/write. Any live journal blocks. Every current OSM key
must occur in its attested alias vector; proofless rows must exactly match the
separately self-pinned schema-v2 1,273-row/11-dossier
`proofless-source-baseline-v1.json`, whose exact file bytes are also rooted. A
current key cannot downgrade to that baseline through publication. Captured
dossier
bytes, canonical store keys, `judge_provenance`, and exact emitted source/date
defaults all participate in the same builder/replay alias closure, so
key-only/provenance conflicts cannot fold silently. Real terminal-run direct-human precedence preserves the human record while
rebuilding host geometry and aliases.
It cannot write a store, sidecar, geom, pool, workflow, or live object. Exact
commands and schemas: `tools/trust_resolver_schema.md`. The Staunton field pilot
ran this path end to end: all four new lots terminated as
`PRESERVE_MACHINE_RESOLUTION`, with zero human exceptions, and its four DROPs
were published and live-verified.

**Operational/manual freeze — not code-enforced:** do not publish
`bear-creek-lake-park-co`. Its available resolver run and 4 historical human
decisions predate the current prepare/review-receipt authority schemas and
cannot be newly published. Regenerate/re-evaluate all 50 rows, rebind accepted
decisions through current frozen review evidence, resolve every exception, pass
hostile review, and obtain an explicit thaw before store, sidecar, geom, pool,
R2, or live publication.

### Recovery commands

No journal authorizes itself and there is no generic recovery command:

| Live transaction | Only supported recovery |
|---|---|
| Judge continuation | Rerun the identical `judge_packets.py SLUG ...` command; it validates and commits the one captured continuation image. |
| Resolver apply | Rerun `resolve_trust.py apply --run RUN --chunk N --apply`; status/plan-only calls do not finish it. |
| Human authority | Rerun the complete original `merge_drafts.py ... --confirm/--decide ... --authority-run RUN --review-receipt RECEIPT --judged DATE` invocation. |
| Store publication | Rerun the complete original `merge_drafts.py STORE SLUG --resolver-run RUN --judged DATE --write` invocation. |
| Parking geom sweep | Rerun the identical `sweep-parking-verdicts.py` apply command; retained sidecar bytes and the approved historical reviewed-empty policy remain authoritative. |

Do not edit, remove, or hand-build journals, stages, backups, candidates, or
archives. A retry accepts only the exact trusted before/after images
independently derived from its original inputs. Exact publication recovery
finishes proof/floor/store/candidate installation but does **not** promote the
candidate. Build/replay and another publication remain blocked until a separate
reviewed Git change installs the candidate as the tracked root and updates the
literal code pin.

---

## 19. What could still be wrong

Written deliberately, because a handoff that only lists what is known produces an
overconfident successor.

- **Colorado is no longer an unseen morphology, but it dominates the corpus.**
  Indian Peaks + Pike contribute 907/1,203 unique verdicts, so a micro-average
  mostly measures one judge protocol in alpine/national-forest terrain. Hold out
  whole areas and source families; do not call Colorado volume generalization.
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
- **Confidence is self-reported, not calibrated probability.** 162/1,203
  current outcomes are `leaning`, but even certain/strong classes cannot bypass
  blind challenge until their exact class passes the trust engine's independent
  error and breadth gates. Treat confidence as routing metadata, never proof.
- **Same-model fan-out is throughput, not independent evidence.** Shared prompts,
  imagery, packet construction and source data create correlated errors. The
  trust engine routes clean rows to a blind challenger and disagreements to an
  autonomous offline arbiter using host-frozen evidence; only unresolved
  contradictions reach the user.

---

## 20. First actions for the next agent

1. ~~Repoint `TMP` in the tools~~ — **done 2026-09-13**, section 10. The tools and
   data are in the repo and run from it.
2. ~~**Settle the ID question**~~ — **done 2026-09-13**, section 14 and `TASKS.md` #53.
3. ~~**Build the sidecar and its consumer**~~ — **done 2026-09-13.** 292 judged lots
   landed through `public/areas/parking-verdicts.json`; the pool moved 30,840 →
   30,832 (8 DROPs were on the map; 74 never were) and the sweep removed the same
   8 from geom. The 66 judged-KEEP lots the pool lacked were read the same
   day: the 53 `certain` + `strong` ones now ship (`--add-keeps` in the R2 sync,
   pool 30,885); the 13 `leaning` ones are held and listed on every run until
   `--add-leaning-keeps`.
4. **Graduate `dossier.py`, `context_classify.py`, `foot_route_area.py` and
   `judge_protocol.md` into `scripts/`**, the way
   `scripts/build-nonhiking-list.py` and `scripts/sweep-nonhiking-trails.py` were
   graduated. They are durable logic living in a scratch directory.
5. **Then scale through the autonomous trust router** — Indian Peaks (149) and
   Pike (758) are judged and shipped; 258 Colorado source areas remain. Per area:
   `run_co.sh` + `ladder_tiles.py` on the homelab, `judge_packets.py
   --skip-judged <every store>`, fresh judge, `replay_trust.py` routing,
   `resolve_trust.py prepare/status`, blind challenger, then arbiter only when
   required. Apply each READY chunk explicitly; only a contradiction that
   survives arbitration becomes a human exception. Run the separate one-area
   store publication next. It emits a non-authoritative successor root; review
   that candidate and promote its exact bytes plus the literal code pin in one
   reviewed Git change. Verify replay and `build-parking-verdicts.py --check`
   against the promoted root before any sidecar/sweep/pool publication. Do not
   begin a second store publication before promotion.

---

## 21. Where everything lives

### In the repo — travels with git, works on any machine

| Path | What |
|---|---|
| `docs/parking-adjudication-handoff.md` | this document |
| `scripts/parking-adjud/README.md` | how to run the tools |
| `scripts/parking-adjud/data/co_verdicts_osm.json` | the self-contained Colorado store (lat/lon/rings embedded; no CO dossiers in git) |
| `scripts/parking-adjud/data/calibration.json` | human-vs-judge agreement ledger, per area (`tools/calibration.py report`) |
| `scripts/parking-adjud/tools/trust_engine.py` | pure shadow routing, candidate policy, calibration bounds and historical replay logic |
| `scripts/parking-adjud/tools/replay_trust.py` | read-only deterministic corpus/current-area route manifest plus lock-held proof/baseline validation |
| `scripts/_parking_verdict_source.py` | shared immutable store/dossier snapshot capability, strict publication-root/floor/baseline schemas, key/alias/provenance normalization |
| `scripts/parking-adjud/publication-trust-root-v1.json` | strict canonical exact-byte root for the complete authoritative publication corpus; SHA-256 is literally pinned in `build-parking-verdicts.py` |
| `scripts/parking-adjud/proofless-source-baseline-v1.json` | schema-v2 exact allowlist for 1,273 proofless rows plus the exact 11-dossier inventory/bytes; separately self-pinned and exact-byte-rooted |
| `scripts/parking-adjud/data/*_publication_floor.json` | one self-hashed append-only first-authority/attestation/proof floor per configured store |
| `scripts/parking-adjud/tools/review_evidence.py` | deterministic frozen review sheet/receipt construction and validation |
| `scripts/parking-adjud/tools/trusted_filesystem.py` | descriptor-relative trusted-parent, lock, read, and atomic-write primitives |
| `scripts/parking-adjud/tools/trust_resolution.py` | pure decision envelopes, consensus, hashes and persisted-resolution validation |
| `scripts/parking-adjud/tools/resolve_trust.py` | deterministic prepare/status and journaled one-chunk canonical apply |
| `scripts/parking-adjud/tools/trust_{challenger,arbiter}_prompt.md` | blind role prompts; neither exposes prior decisions |
| `scripts/parking-adjud/tools/trust_resolver_schema.md` | exact resolver artifacts, policy, transaction and recovery contract |
| `scripts/test_parking_trust_engine.py` | shadow/no-write, provenance, promotion, routing and canonical-corpus regressions |
| `scripts/test_parking_trust_resolver.py` | prepare/status/consensus/apply/tamper/crash/store integration simulations |
| `scripts/parking-adjud/tools/` | adjudication, checkpoint, review and trust tools plus 2 shell drivers and the judge protocol |
| `scripts/parking-adjud/data/` | five verdict stores, five publication floors, 11 rooted dossiers, serves/context/walk inputs, `groundtruth.json`, `coverage_gaps.json`, `QUALITY_REPORT.md`, and `co_areas.json` |
| `scripts/parking-adjud/work/` | scratch, git-ignored, created on demand |

**The tools no longer hardcode operational data paths.** Paths come from
`PADJ_TMP`, `PADJ_GEOM`, `PADJ_PARKING_PBF`, `PADJ_US` and `PADJ_OSM`, each
falling back to a repo-relative default. The shadow replay is standard-library
only and has its own no-write regression suite.

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

### What is deliberately NOT in the repo, and why

Asked and answered with two tests rather than an assumption.

**Rendered tiles (91 MB, 139 files) — excluded because they are reproducible.**
✅ Re-fetched one August NAIP frame at the same bbox on 2026-09-13 and compared:
**mean absolute difference 0.0 / 255** over a plain 300×300 corner. NAIP returns
the same pixels for the same request. The overlays on top are drawn by committed
code (`z2render.py`) from committed data (dossier rings, shipped geom), so a tile
is a pure function of things the repo already holds.

✅ Proved it on the hardest case — an Arizona tile whose original was lost when
the job tmp was wiped. Rebuilt `pinnacle-peak-park-az` fid 0 from the committed
dossier plus `public/areas/geom` plus one NAIP fetch: the mapped lot outlined in
red with cars visible in it, the yellow shipped trail at the corner. A judgeable
frame, reconstructed from the repo.

Cost to regenerate: about a second per tile, paced for NAIP. The 80 New England
frames are roughly two minutes.

**Review artifacts (35 MB of HTML) — excluded because they are derived.**
`padjart2.py` and `ne_review2.py` build them from tiles plus verdicts, both of
which the repo can produce. The New England one is published instead, so nobody
has to run anything to look at it (section 6).

**OSM extracts (182 MB regional + 1.2 GB Colorado) — excluded because they are
large and regenerable.** `osmium extract --bbox=...` from the national file
rebuilds any of them. Keeping multi-GB pbfs in git would slow every one of the
twelve workflows that check this repo out, for no gain.

**Everything else is in.** Tools, judge protocol, dossiers, serves gates,
contexts, walks, every verdict store, the ground truth, the coverage gaps, the
quality report, the Colorado area list, and the original raid README kept
verbatim at `scripts/parking-adjud/ORIGINAL-README.md` for provenance.

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
