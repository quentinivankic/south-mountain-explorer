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
| Tooling | Built, on `/mnt/raid/trekdex/parking-adjud/tools/` (16 files) |
| Areas adjudicated | **11**, 0 reviews outstanding 📋 |
| Distinct OSM lots with a banked verdict | **299** ✅ (`python3` count over the 4 verdict stores) |
| Global pool the app actually serves | **30,840 lots** ✅ (ran `scripts/build-parking-pool.py`) |
| So: fraction adjudicated | ~1% |
| `public/areas/parking-verdicts.json` | **DOES NOT EXIST** ✅ (`ls`); nothing in the repo references it except `TASKS.md` ✅ (`grep -rn`) |
| Colorado batch | Inputs prepared, **never run** — 261 areas / 2,090 lots ✅ (`co_areas.json`) |
| Blocking | Nothing external. This is unblocked work. |
| It blocks | TASKS **#51**'s containment roll and **#52**'s polygon merge 📋 |

**The single most important open decision is in section 12 (the ID problem). Read
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
access spurs) — see section 14.

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

## 6. Killed ideas — do not re-propose without new evidence

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

## 7. The pipeline — running one area end to end

⚠️ **Read section 8 first. The tools will not run as-is.**

```bash
D=/mnt/raid/trekdex/parking-adjud
SLUG=<area-slug>                # matches public/areas/geom/<slug>.json

# 1. Per-area OSM context extract (footways + context features), ~3.5 min from
#    the national file. dossier.py and foot_route_area.py BOTH read
#    <slug>_ctx.osm.pbf from their TMP dir, so name it exactly that.
osmium extract --bbox=<lon0,lat0,lon1,lat1> \
  -o $TMP/${SLUG}_ctx.osm.pbf /mnt/raid/trekdex/osm/us-latest.osm.pbf
#    bbox = the geom bbox expanded by BUF=0.06 degrees on every side.

# 2. Dossier: per-lot full tags + real OSM ids + polygons + the edge-distance
#    serves gate. Emits <slug>_dossier.json and <slug>_serves2.json.
python3 $D/tools/dossier.py $SLUG

# 3. Foot-network walk distance to our nearest shipped trail (Dijkstra over the
#    walkable OSM network). Emits <slug>_walk.json and joins walk_m into the dossier.
python3 $D/tools/foot_route_area.py $SLUG

# 4. OSM context classifier — pass the pbf path explicitly.
python3 $D/tools/context_classify.py $SLUG $TMP/${SLUG}_ctx.osm.pbf

# 5. Render the zoom tiles for the public-served set (NAIP primary).
python3 $D/tools/z2render.py          # Z2 only, or ladder_tiles.py for Z1/Z2/Z3

# 6. JUDGE each served lot. Vision. By hand, or fan out to sub-agents each
#    handed tools/judge_protocol.md plus the nine lessons above plus this
#    area's dossier / serves / context / tiles. Store verdicts KEYED BY OSM ID.

# 7. Artifact for human review.
python3 $D/tools/padjart2.py $SLUG "Display Name"
```

Cross-area helpers:

- `review_queue.py` — scans every area's verdicts and builds ONE cross-area
  REVIEW-only page (undecided lots, plus any KEEP beyond 1 mile).
- `score2.py <area> <verdicts.json> <dossier.json>` — scores verdicts against
  `data/groundtruth.json`. **The bar is 0 confident-wrong, not 100% match.**
  `leaning` rows may land REVIEW freely.

---

## 8. ⚠️ Resurrection — what is broken right now and how to fix it

**Every tool has a hardcoded `TMP` pointing at a directory that no longer
exists.** ✅ (`grep -n "^TMP" tools/*.py` and `ls -d /home/quentin/.claude/jobs/a4a4c50a/tmp`)

```
TMP="/home/quentin/.claude/jobs/a4a4c50a/tmp"     # GONE
```

Affected: `dossier.py`, `foot_route_area.py`, `context_classify.py`,
`ladder_tiles.py`, `z2render.py`, `padjart2.py`, `review_queue.py`,
`padjudicate.py`, `serves_relative.py`, `phx_apply.py`, `make_ne_review.py`,
`ne_review2.py`, `run_ne.sh`.

**First action: repoint `TMP` to a working directory and copy `data/*` into it**
(the scripts read their inputs from `TMP` and write their outputs there too).

⚠️ **`run_co.sh` sets `PADJ_TMP` / `PADJ_PARKING_PBF` / `PADJ_US` environment
variables, but the tools on `/mnt/raid` do not read any environment variable** ✅
(`grep -rn "PADJ_\|os.environ\|getenv" tools/*.py` returns nothing). The
env-var-aware versions of the tools lived only in the wiped job tmp and are
**lost**. Either re-add env support (recommended — it is a 3-line change per
tool) or edit `TMP` by hand.

Other hardcoded paths, all currently valid ✅ (`ls`):

| Constant | Value | Exists |
|---|---|---|
| `GEOMDIR` | `/home/quentin/south-mountain-explorer/public/areas/geom` | yes |
| `US` (dossier) | `/mnt/raid/trekdex/osm/us-access.osm.pbf` | yes, 3.7 GB |
| national parking pbf | `/mnt/raid/trekdex/osm/cache/parking-only.osm.pbf` | yes, 120 MB |
| full planet-US | `/mnt/raid/trekdex/osm/us-latest.osm.pbf` | yes, 12 GB |

**Python dependencies are all installed** ✅ (`python3 -c "import osmium, shapely, PIL"`):
osmium, shapely 2.0.3, Pillow 10.2.0.

### Stale warnings you can ignore

📌 **The README, `TASKS.md` #53 and the auto-memory all warn that the root disk is
at 94% with 5.7 GB free, and that a disk-full once truncated a verdict file.**
That is **no longer true** ✅ (`df -h /`): the root filesystem is now 232 GB, 46%
used, **121 GB free**. The habit of keeping pbfs on `/mnt/raid` is still correct
(they are huge and belong with the other extracts), but the disk panic is over.

📌 **The auto-memory carries a TODO: "filter cyan to hiking-relevant ways before
reusing the overlay elsewhere."** That work is **already done** ✅ — see
`tools/ne_review2.py::_hike_ok` (line 66), which drops ski pistes
(`piste:*`), bare `highway=track` without `foot=yes`, `bicycle=designated`
bike-park trails, and sidewalk/crossing footways. It is wired into the render
path at line 108.

---

## 9. Tool reference

All in `/mnt/raid/trekdex/parking-adjud/tools/`.

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

## 10. Data reference

All in `/mnt/raid/trekdex/parking-adjud/data/` (99 MB, 73 files) ✅.

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

## 11. The verdict schema

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

## 12. ⚠️ THE ID PROBLEM — read before designing the sidecar

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

## 13. The consumer side — what the sidecar must respect

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

## 14. What is banked

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

**Generalization result:** Griffith + Zion scored **0 confident-wrong** against
the user's own calls, same protocol, no re-tuning, across a desert wilderness and
a dense city 📋 (`score2.py`, 2026-08-01).

**The New England batch is the proof the fan-out works.** 67 of the 80 lots were
judged by **4 parallel per-area sub-agents**, each handed `judge_protocol.md`,
the lessons, and that area's dossier / serves / context / Z2 tiles. Verified
afterwards: all 12 drops are far-fallback serves (2.1–7.3 km walks, none a close
lot wrongly dropped), and 4 tiles were spot-read by hand — the agents were
correct. User-approved 2026-08-02: *"all looks good"*.

---

## 15. The Colorado batch — prepared, never run

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

## 16. First actions for the next agent

1. **Repoint `TMP`** in the tools, or add env-var support. Nothing runs until
   this is done (section 8).
2. **Settle the ID question** (section 12). It determines the sidecar's shape,
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

## 17. Where everything lives

| What | Where |
|---|---|
| Tooling, data, lessons | `/mnt/raid/trekdex/parking-adjud/` — **read its `README.md` first** |
| Task, with full measurement history | `TASKS.md` #53 (and #51, #52, #54 which defer to it) |
| Auto-memory | `parking-vision-adjudication.md`, plus `parking-feature.md`, `always-spatial-index.md`, `prefer-homelab-over-network.md`, `verify-before-asserting.md` |
| Global pool builder | `scripts/build-parking-pool.py` |
| Parking enrichment | `scripts/add-parking.py` |
| Sidecar precedent | `public/areas/nonhiking-trails.json` + `scripts/sweep-nonhiking-trails.py` |
| App parking model | `ios/SouthMountainExplorer/Models/Area.swift`, `Services/ParkingPoolService.swift` |
| R2 sync | `.github/workflows/sync-geom-to-r2.yml` |
| Colorado batch | `/mnt/raid/trekdex/parking-adjud/co/` |

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
