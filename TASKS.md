# Open tasks

**Why this file exists.** The task list Claude keeps in-session (`TaskList` /
`TaskGet`) does NOT survive into a new session — it is session-scoped state, and
a fresh session starts with nothing. These 13 tasks carry hard-won measurement
in their descriptions: national counts, named counter-examples, and approaches
already ruled out with evidence. Losing them means re-deriving all of it, or
worse, re-proposing something already disproved. So they live here, in the repo,
and a new session should re-create them with `TaskCreate` from this file.

Numbers are the original task ids. Gaps (#19–#20, #22–#25, #27–#30, #32,
#38–#43, #45, #48) are completed work — see git log and `TODO.md`.

Last synced from the live task list: **2026-07-29**.

| # | Task | Kind |
|---|---|---|
| [44](#44) | Emit the parking pool BEFORE ownership | data · MEASURED, ready to build |
| [35](#35) | Fragmentation quality score | data · measured |
| [21](#21) | Road-as-trail: ~2,780 still unsolved | data · 4 approaches ruled out |
| [31](#31) | Split thru-routes into road-bounded sections | data · measured |
| [33](#33) | Over-fragmented connectors as standalone trails | data · not measured |
| [34](#34) | Do bridleways ship as hiking trails? | policy call |
| [36](#36) | Trail source for route relations | policy call |
| [37](#37) | Group nested-sibling areas in search | app + data |
| [26](#26) | Mid-hike "complete the area" routing mode | app · large |
| [46](#46) | Ship Canada | pipeline · large |
| [47](#47) | Crash and stability pass on device | QA |
| [49](#49) | Live Activity + distance-to-next-turn | app |
| [50](#50) | Paid Applications Agreement | user-side |

---

<a name="44"></a>
## #44 — Emit the parking pool BEFORE ownership (USFS all, NPS trailhead-named only)

Emit the pool from `add-parking.py` after the containment + road gates but
BEFORE `assign_federal`.

⭐ **MEASURED END TO END 2026-07-29, every real gate applied, zero Overpass
calls. The answer is 1,462-1,636 new lots, NOT the 2,348 first reported.**

    road-gate survivors                                    2,596 / 2,689
    STRICT (inside a polygon we hold)   2,390 -> 2,383 after same-name collapse
    LOOSE  (orphans kept as well)       2,596 -> 2,589 after same-name collapse

    STRICT   1,462 genuinely NEW lots   (921 already in the shipping pool)
    LOOSE    1,636 genuinely NEW lots   (953 already in the shipping pool)

    trails gaining a lot inside the 805 m display gate
      STRICT   919 trails (1.0% of 92,279) across 167 areas
      LOOSE    959 trails (1.0% of 92,279) across 172 areas
      biggest single move: +610 trails into the "parking under 100 m" band

**2,348 WAS A CEILING, not an answer** — it counted every point the ownership
rule discards with the road gate switched off and no dedup against the pool that
already ships. Roughly a third of it is already in the pool or dies at the gate.
Keep the ceiling in mind when someone quotes it: the deliverable is ~1.0% of
trails, not 2.5% of lots.

Corpus: 9,060 shipped areas, 2,771 with a usable boundary polygon, 2,899 blank
pre-fill.

**ARE THESE REAL TRAILHEADS OR CAR PARKS AT A MALL?** Asked 2026-07-29 and worth
re-asking. They cannot be mall lots because they never come from OSM's generic
`amenity=parking`: USFS comes from `EDW_RecreationOpportunities_01` filtered
SERVER-SIDE to `MARKERACTIVITY='Trailhead'`, NPS from `NPS_Public_ParkingLots`
cut to names matching /trailhead/, and both then have to sit inside an area
boundary with a drivable road within 250 m. Composition of the 1,639 LOOSE
pre-collapse set: **1,583 USFS + 56 NPS; 1,468 carry "trailhead" in the name and
171 do not, none unnamed.** A 22-row stratified sample weighted onto that 171 was
put to the user with map links; the user accepted the set without needing to walk
it. If this is ever re-litigated, that 171 is the bucket to look at.

**NAMED CASUALTIES** (all real, all filed under an overlapping unit): Peralta
Trailhead (main Superstition access), South Fork, Carr Canyon, Miller Canyon,
String Lake (Grand Teton), Two Medicine Lake (Glacier), Bighorn Pass / Cascade
Lake / Gneiss Creek / Soda Butte / Wram Creek (Yellowstone), Kehoe Beach (Point
Reyes), Alamo Boundary (Bandelier), Rattlesnake Creek (Dixie NF).

⛔ **NPS MUST BE FILTERED TO TRAILHEAD-NAMED ONLY.** The user judged a sample and
confirmed Grand Canyon "Clinic Parking B", "Muav Court Parking E", "Paiute Circle
Parking", "Entrance Sign Parking" are staff-housing/facility lots that must never
ship.

1. **Landowner attributes DO NOT WORK here**, unlike USFS `trail_type`. The NPS
   layer HAS `LOTTYPE` and `OPENTOPUBLIC` and they are EMPTY: `LOTTYPE` blank on
   all 805 AZ+NM lots, `OPENTOPUBLIC="Unknown"` on 803/805, `PUBLICDISPLAY`
   identical on all. Every judged lot, good and bad, is byte-identical.
   **Don't re-check this layer.**
2. ⛔ **OSM-CORROBORATION DISTANCE FAILED** — and it was proposed on n=8 before
   the corpus refuted it. Perfect separation on 8 (good 8/8/9/34 m, bad
   163/184/>300/>300 m) did NOT survive 6,570 nationwide: real trailheads AND
   staff lots in EVERY band. 0–40 m holds "Lower Residence Parking B" (staff
   housing, 8 m); 400–1000 m holds "Wawona Trailhead Parking" (Yosemite, 428 m);
   100–200 m holds Two Medicine Lake and Lightning Spring, both real.
   **DO NOT RE-PROPOSE A DISTANCE CUT.**
3. ✅ **WHAT SURVIVES: NPS NAMES its trailheads.** 162 of 6,570 (2.5%) match
   `/\btrail\s*head\b/i` and the sample is clean — Bright Angel, Widforss, Cinder
   Cone, Wawona, Laguna (Point Reyes), Osceola Ditch, Alpine Pond. This is an
   INCLUSION rule: failure mode is missing lots, never adding a clinic. It accepts
   that the 90% unnamed middle ("Parking Lot", "SOUTH BEACH PARKING") is unusable,
   which loses real ones like "Appalachian Trail Parking - Skyline Dr". 457 of
   6,570 (7%) match staff/facility vocabulary — a scale check only, NOT a rule.

**SCOPE:** pool only. NPS pins already written into blank areas stay — they are
the only parking those areas have.

**BUILT 2026-07-29.** `add-parking.py --pool-sidecar PATH` emits the road-gated
federal trailheads between `road_gate` and `assign_federal`, through the new pure
`pool_candidates()` (USFS all, NPS only where the NAME says trailhead) and the
existing `clean_federal_lots` same-name collapse. All three snags are addressed:

- **the fetch was conditional** — it ran only when a state had blank areas, so a
  state OSM had fully mapped contributed nothing. `--pool-sidecar` is now reason
  enough to fetch, independent of `blank_ids`;
- **the dedup was per-area** — the sidecar is collapsed per state on the way out,
  and `build-parking-pool.py --extra` folds it through the SAME global 40 m dedup
  the pool already ran, geom first so a shipped lot keeps its exact position;
- **the workflow is a per-state matrix** — each state uploads a `pool-<STATE>`
  artifact and `scripts/merge-parking-pool-sidecars.py` fans them in, additively,
  replacing only the states present.

Measured on the real gated set: pool **29,196 -> 30,808 (+1,612 new)**, with
**0 existing positions lost and 0 names changed**.

DRY RUN, Vermont, every row read (the counts alone would have hidden this):

    KEEP [usfs] Clark Brook Trailhead / Cooley Glen-Emily Proctor Trailhead /
                East Dorset Trailhead / Skylight Pond Trailhead
    KEEP [usfs] Chatfield Loop / Lye Brook Wilderness / Michigan Brook Road /
                Mountain Top      <- no "trailhead" in the name, and RIGHT to
                                     keep: USFS is trailhead-only at source, so
                                     name-filtering it would delete real ones
    drop [nps]  CARETAKERS PARKING / COLLECTIONS STORAGE PARKING /
                DANCE HALL PARKING / RV AND OVERFLOW PARKING /
                Visitor Parking / Accessible Parking

Ownership gave that state 3 pins across 2 areas; the pool gets 8. The sidecar
`public/areas/parking-pool.json` is COMMITTED (the roll has network and shapely;
`sync-geom-to-r2` installs neither) and was added to that workflow's push paths,
because a roll can change ONLY the sidecar and would otherwise never rebuild the
pool.

**IMPLEMENTATION:** emit the road-gated `fed` list between `road_gate`
(~`add-parking.py:1198`) and `assign_federal` (~`:1205`), `source=="usfs"` plus
NPS matching the trailhead regex. Snags: (a) the federal fetch only runs when a
state has blank areas, so states with none contribute nothing; (b)
`clean_federal_lots` dedup runs per-area after assignment and the pool needs it
globally; (c) `trailforge-parking.yml` is a per-state matrix → per-state artifacts
plus a fan-in like `merge-published-geom.py`.

`build-parking-pool.py` must stay dependency-free: `sync-geom-to-r2.yml` installs
NOTHING (verified — no `pip install`, no `setup-python`), while
`trailforge-parking.yml` installs shapely and has network. So the natural shape is
a committed sidecar written during the parking roll and merged by the pool builder,
following `aliases.json` / `nonhiking-trails.json`.

**MEASURING IS LOCAL AND CHEAP:**

    us-parking.osm.pbf     115 MB, 425 s, one osmium tags-filter pass over
                           us-latest.osm.pbf. 1,456,699 amenity=parking +
                           10,435 highway=trailhead. Replaces the Overpass
                           parking query.
    us-boundaries.osm.pbf   43 MB, 423 s, `osmium getid -r -t --id-file relids.txt`
                           over the 2,794 area relation ids -> 2,854 relations.
                           Replaces the per-state Overpass boundary fetch.

Debian pyosmium has NO `osmium.area` module, so assemble polygons with the CLI:
`osmium export ... -f geojsonseq --geometry-types=polygon --add-unique-id=type_id`,
then map area id → relation id as `(id-1)//2` for odd ids. Verified 2,771/2,771.
Only the ArcGIS point layers still need the network.

**STATUS 2026-07-29: the measurement is DONE and the road gate no longer needs
Overpass at all.** The 2026-07-28 attempt died on HTTP 504 through three retries;
so did the 2026-07-29 retry, on the first of 45 chunks, with both mirrors
(`overpass-api.de`, `overpass.kumi.systems`) failing together for 26 minutes. The
gate was answered from the local extract instead and the run finished offline.

**THE ROAD GATE RUNS LOCALLY — the data was already downloaded.**
`/mnt/raid/trekdex/osm/us-access.osm.pbf` is cut from `us-latest` by
`trailforge/extract/prefilter-access.sh`, whose own comment says it exists so
"the road gate and this extract agree about what counts as reachable by car".
Nothing had ever wired the gate to it: `fetch_roads_near` only builds an Overpass
query. `fetch-us-extract.sh --update` regenerates the derived extracts on every
base update, so the local copy does not silently go stale.

Recipe (~13 min, no network, no rate limit):

    osmium tags-filter us-access.osm.pbf w/highway=<_DRIVABLE_HW + _link variants>
      -> 126 s, 345,187,352 nodes / 39,930,095 drivable ways
    osmium cat -f opl | scan node coords against a 0.0025-deg cell dict
      -> 629 s, 130,714 nodes in a candidate neighbourhood

Cut by TAG, not by polygon: in a roads-only file every node IS a road node, so
no way membership has to be rebuilt. `_road_gate_filter` still does the
arithmetic, per candidate against its own 3x3 cells.

**VERIFIED against the thing it replaces:** 60 candidates sampled every 44th
across the national list, gated both ways — Overpass returned 15,701 road nodes —
**agreement 60/60**, both keeping 58 of 60.

⛔ **`osmium extract -p` HONOURS ONLY THE FIRST POLYGON, SILENTLY.** This is why
the first local answer was wrong (2,544 instead of 2,596), and osmium 1.16.0 exits
0 every time:

| polygon file | result |
|---|---|
| bare `{"type":"Polygon"}` | errors loudly: `Expected 'type' value to be 'Feature'` |
| `Feature` + `Polygon`, box A | 112 nodes, 7 ways |
| `Feature` + `Polygon`, box B | 408 nodes, 20 ways |
| `FeatureCollection` [A, B] | 112 / 7 — B ignored |
| `FeatureCollection` [B, A] | 408 / 20 — A ignored |
| `Feature` + `MultiPolygon` [A, B] | **0 nodes, 0 ways** |

A 2,689-box `MultiPolygon` cut therefore produced a 6.3 MB file holding 15,873
drivable ways that LOOKED like a plausible national cut. It was caught only
because the 60-point Overpass comparison disagreed on ONE row — `#978 M.F. Bull
River Trailhead` — and chasing that row found zero local road nodes within 2 km
of it while `National Forest Development Road 2722` has one 58 m away, present in
`us-access.osm.pbf` the whole time. 98.3% agreement looked like rounding; it was
a broken method. The repo is NOT affected: `trailforge/extract/aoi.sh` uses
`osmium extract --strategy=smart --bbox`, not `--polygon`.

**FOR THE BUILD:** `trailforge-parking.yml` runs on `ubuntu-latest` with no local
extract, so a local road gate is a HOMELAB acceleration, not a CI one — production
`fetch_roads_near` must keep its Overpass path. `road_gate` already separates the
fetch from the pure `_road_gate_filter` and already returns an `ok` flag so an
outage is never read as "no roads here", so a local source is one new fetch
function behind a flag, with no change to the gate's semantics.

The harness is `~/.claude/jobs/*/tmp/measure44_real.py` (imports `add-parking.py`
so the gates are production code, caches gate survivors to `m44-gated.json` so a
re-run costs no network — seed that file from the local gate and the whole
measurement runs offline in ~18 min). Two bugs were found and fixed in it: the
ArcGIS bbox wants `[lonmin, latmin, lonmax, latmax]` and returns 0 features
WITHOUT raising if you get it wrong, and the grid search span must come from the
caller's cap or every distance band past ~835 m silently reads zero.

⚠️ **BIGGER GAP FOUND WHILE MEASURING, not on this list:** only **2,794 of 9,060**
shipped areas carry an `osm_relation_id` at all. For the other 6,266 there is no
boundary polygon, so containment can never be evaluated and no buffer or gate can
ever fill them. That dwarfs the 2,348 lots #44 recovers.

---

<a name="35"></a>
## #35 — Fragmentation quality score: de-feature poorly-mapped trails

OSM gaps are often MISSING DATA, not road-walks (confirmed on Midland-Mackinaw —
holes where the trail exists on the ground but isn't mapped). Can't fill missing
data, so SCORE trails by data completeness and let well-mapped ones rise:
`score = gap-miles/length + gap count`; de-rank/flag heavily-fragmented trails in
browse/area so clean local loops surface first.

AUDIT (baseline 2026-07-26, `python3 scripts/audit-trail-quality.py`):

- 2,756 of 92,548 trails have ≥1 gap; median gap/length ratio 0.36; 1,542 above 30%
- pick a "poor" threshold (e.g. ratio >0.30 OR ≥3 gaps) → that set is de-featured
- sanity: Midland-Mackinaw scores poor; South Mountain "National Trail" (0 gaps)
  scores clean
- after: count featured vs suppressed; confirm no clean trail suppressed

---

<a name="21"></a>
## #21 — Road-as-trail: 18 snow routes dropped, the ~2,780 road population STILL UNSOLVED

PARTIAL 2026-07-27/28. PR #497 dropped 18 trails / 35.2 mi / 13 areas. The
suspected road-as-trail population is NOT solved and four approaches are ruled out
with evidence — **read this before proposing a fifth.**

MEASURED NATIONALLY (exact geometry join off the local extract, not Overpass
sampling — `tiger:cfcc` IS present in `/mnt/raid/trekdex/osm/us-hiking.osm.pbf`,
916,411 track ways carry TIGER attributes, so this is a local osmium pass rather
than days of API calls): of 92,297 shipped trails, 13,584 are backed by a
`highway=track` way — 2,346 have `foot=yes|designated`, 2,171 a plain road CFCC
(A41/45/61/73), 609 A51 "4WD vehicular trail", 4,673 tracks with no TIGER
attribution. So ~2,780 are road-classified with nothing protecting them,
corroborating the 2026-07-26 estimate of ~2,763 by a completely different method.

⛔ **RULED OUT, each measured:**

1. **Name patterns.** Only 9 of 2,171 road-CFCC trails match any road word.
   `model._FOREST_ROAD` wants a known prefix (NF-418C, BLM 1048, FR 236) or a bare
   code as the WHOLE name; the dominant real form is `<code> <place name>` —
   "4W653 Bethel Oak", "10021 Little Park Creek" — invisible to all three
   patterns. Adding "4W-style" starts the next round of whack-a-mole across 50
   states.
2. **TIGER `cfcc` gate.** Stale 2000s provenance (a way that was a road then and
   is a trail now still carries it), US-only so dead for Canada, covers only 20%
   of track-backed trails, and 1,118 of the road-CFCC set are named "…Trail".
3. **Inverting to "prove it's a hike".** 5,803 of 9,884 have some positive
   evidence but 5,112 qualify ONLY via the word "Trail" in the name — the exact
   evidence this project already proved unreliable. Strip that and real tag
   evidence covers ~2,600, so a strict rule deletes ~7,200.
4. ⭐ **THE DECISIVE ONE:** the user spot-checked 10+ examples from EACH of four
   evidence buckets (road-CFCC+foot / road-CFCC+"Trail" name / road-CFCC+nothing /
   bare track+nothing) and found real trails AND truck roads in EVERY bucket. The
   distinction is not in OSM's tags. OSM describes a way's FORM; we need its
   PURPOSE.

✅ **WHAT WORKED — ask the landowner.** `EDW_TrailNFSPublish_01` (same ArcGIS host
`add-parking.py` uses; also `EDW_RoadBasic_01`) carries `trail_type` =
TERRA/SNOW/WATER per trail plus a managed-use matrix. 4W653 Bethel Oak matched
BETHEL RIDGE SNOWMOBILE at 83%: every source was right and none said "snowmobile".
Over 170 NF/grassland areas, 13,701 of our trails matched an NFS trail (11,943
didn't), 13,457 TERRA / 244 SNOW. Layer cached at `/mnt/raid/trekdex/nfs_cache`
(170 areas) so re-slicing needs no network. Harness:
`scripts/build-nonhiking-list.py`.

**DROP RULE, three independent agreements** — each condition earned by a dry-run
false positive:

- (a) matched NFS feature is `trail_type = SNOW`;
- (b) the FS's own NAME says snow (SNOWMOBILE/X-C SKI/SKI/WINTER/SNOW-/GROOMED/
  SNOWSHOE);
- (c) no TERRA trail shares the tread, `terraShare <= 0.25`.

Why each: (a) alone flagged 347 because indexing only the SNOW subset credits any
trail merely OVERLAPPING a winter route; requiring snow to merely BEAT terra still
kept Howlock Mountain (51 vs 50%) and Thielsen Creek (63 vs 59%), both real Mount
Thielsen hikes groomed in winter; and dropping on "no hiker use recorded" (2,346)
would have deleted Eagle National Recreation Trail (23.3 mi), General Crook Trail
#140 and Overland Trail #615 — a blank attribute is not a negative, and those
fields hold seasonal DATE RANGES ("05/15-09/15") so they say WHEN not WHETHER.

**MECHANISM for future verdicts:** `public/areas/nonhiking-trails.json`,
`{areaId: {trailId: {reason, evidence, share, terraShare}}}` — every verdict
carries the agency asset that justified it, so a wrong call is one line to remove.
Built by `scripts/build-nonhiking-list.py`, applied by
`scripts/sweep-nonhiking-trails.py` (minutes, no republish), honoured by
`publish_areas.py` (reads the FILE, no agency call at publish). Both sweep and
publisher REFUSE to empty an area. Bridleways (#34) belong in this file, not a new
mechanism.

**WHAT'S LEFT:** the ~2,780 road-classified trails. Untried avenues — match against
`EDW_RoadBasic_01` directly (a trail whose geometry is a Forest Service ROAD and
matches no NFS trail is a strong drop candidate; Bethel Oak scored road 3% / trail
83%, so the roads layer alone is not sufficient), and non-FS land has no equivalent
dataset at all. Consider de-featuring rather than deleting (#35) for anything that
cannot be adjudicated.

---

<a name="31"></a>
## #31 — Split thru-routes into road-bounded continuous sections

Replace the teleporting merged blob with continuous sections a person recognizes.

**RULES:** (a) split at internal gaps > 2 mi; (b) split at MAJOR-road crossings
(primary/secondary/trunk/state-highway) not minor ones — approximates colloquial
extent (Midland-Mackinaw stopping at M-61); (c) assign each section to the area(s)
it lies in — STOP shipping the whole route identically into every unit; (d) never
draw straight lines across unmapped gaps.

AUDIT (baseline 2026-07-26, `python3 scripts/audit-trail-quality.py`):

- trails with any gap ≥2 mi: 996 → ~0
- gap-dominated (gap-mi > trail-mi): 689 → ~0 (1,542 exceed 30% gap ratio)
- max internal gap: 392 mi / 631 km (California Coastal Trail @ golden-gate) → <2 mi
- buckets ≥1/2/5/20/50 mi: 1630/996/516/180/43
- route duplication (same trail byte-identical in >1 area, e.g. Midland-Mackinaw
  44.65 mi in ≥2 Au Sable units): compute + drive to each section assigned once
- spot-check: our Midland-Mackinaw Gladwin section ≈ AllTrails' 18.3 mi (currently
  44.65 mi)

See auto-memory `area-quality-grayling-audit`.

---

<a name="33"></a>
## #33 — Over-fragmented connectors shown as standalone trails

A trail network gets split into many tiny named path fragments that each ship as
their own "trail" with a checkmark. Au Sable Grayling: "(Nordic)" = 0.11 mi
`highway=path` — a real connector in a Nordic ski loop, not a destination. NOT junk
(don't delete — it links the network), but noise as a standalone entry. Consider
merging tiny same-network connectors into their parent trail/loop, or not surfacing
sub-threshold connectors as independent completable trails. Interacts with the
0-length gate (#30) — connectors are the reason that gate must be near-zero, not
just "short". Found in the Grayling Unit audit 2026-07-26.

---

<a name="34"></a>
## #34 — Decide whether bridleways (horse trails) should ship as hiking trails

The pipeline ingests `highway=bridleway` as trails: Au Sable "Shore to Shore Trail"
and its "- North Spur" are `highway=bridleway` (horse trails). Many bridleways are
walkable and legit hikes; some aren't. Decide the policy: keep bridleway (walkable)
vs gate it (`foot=yes|designated` required) vs label it. Same family as the
track/road access question. Low urgency; note it exists. Found in the Grayling Unit
audit 2026-07-26.

Per #21: the verdict belongs in `public/areas/nonhiking-trails.json`, not a new
mechanism.

---

<a name="36"></a>
## #36 — Decide trail source for route relations: relation members vs name-stitch

We build route-tagged trails by NAME-STITCH ("all ways named X"), which frays at
the seams — it catches orphan named ways the relation omits (e.g. way 1034301003)
and misses the unnamed connectors + co-aligned trails the route relation uses.
Neither is "correct". Decide the source of truth for `kind=route` trails and make
it consistent.

AUDIT (measure the divergence, then decide):

- baseline example: Midland-Mackinaw name-stitch = 44.7 mi vs relation off-road
  members = 49.5 mi (~10% + different edges); the relation also pulls in 2.0 mi
  Shore-to-Shore + 6.7 mi unnamed connectors + road-named tracks
- national: number of route-relation trails where name-stitch geometry vs relation
  off-road members diverge by >10% — baseline TBD
- decision to record: relation-first (curated intent) vs name-stitch (pure name) vs
  hybrid; then re-audit divergence → ~0 under the chosen rule

See auto-memory `area-quality-grayling-audit`.

---

<a name="37"></a>
## #37 — Group nested-sibling areas of one place (Saguaro 4, Au Sable 4) in search

One place still shows as N separate area entries — the case B+D (#475) deliberately
did NOT touch. Two flavors:

- (a) **DISTINCT sibling units** with different rel ids (Au Sable State Forest =
  Gladwin/Grayling/Roscommon + Scenic River = 4 entries; not subsets, so
  alias-dedup correctly left them);
- (b) **DISTRICT subsets below the 0.75 nested threshold** (Saguaro NP 98 trails +
  Rincon district 57 (0.58) + Tucson district 43 (0.44) + Wilderness 65 (0.66) —
  all <0.75 so #475 kept all 4).

Fix is NOT deletion (they're real distinct polygons with their own trails) — it's
search/browse GROUPING: collapse siblings under one "Saguaro National Park" /
"Au Sable State Forest" heading, most-iconic designation as the parent. Needs a
synthesized parent (no whole-place area exists) or a name+containment cluster.

See auto-memory `nested-area-dedup` + `parking-feature`.

---

<a name="26"></a>
## #26 — Mid-hike "complete the area" routing mode

USER FEATURE REQUEST 2026-07-25. A mode you flip on mid-hike that routes you
through the still-UNCOMPLETED, connected trails in the area with minimum total
added distance, starting from your current GPS position. A completionist route
optimizer.

**SHAPE:** a Route Inspection / Chinese Postman variant on the UNCOMPLETED subgraph
— junctions = nodes, trail segments = weighted edges, dead-end spurs require
out-and-backs. Start node = snapped current location. Output = a route line + step
prompts.

**DEPENDENCIES:** trail-network topology from geom (build the junction graph); live
per-trail completion state; current GPS location.

⚠️ **CRITICAL PRECEDENT from the parking work — READ BEFORE BUILDING.** The exact
trail-network graph this needs (union-find over trail vertices, 50 m junction snap)
was already prototyped for parking reachability. Two hard findings apply directly:

1. **Our trail networks are FRAGMENTED.** In Helena-Lewis and Clark NF, ~34% of
   trails sit in a component with no connection to the rest, and connectivity
   measured 45% reachable at best. So "route through the connected uncompleted
   trails" only works WITHIN a connected component — the mode must handle the
   common case where the area is several disjoint islands, and either pick the
   user's current island or present islands as separate legs.
2. **The within-trail-vertex union step is essential and easy to get wrong** (it
   was got wrong on the first pass: joining only cross-trail vertices made
   connectivity look 3× worse than reality). Chain a trail's own vertices before
   junctioning across trails.

Also: the segment-order / disconnected-segment problem (#19, fixed at bake time by
`_chain_segments`) means a single "trail" can be physically discontinuous — the
graph builder must use real vertex geometry, not assume a trail is one contiguous
edge.

**SCOPE:** substantial. Graph build is bake-time-cacheable per area; the route
solve is live. Likely a per-area precomputed adjacency shipped in geom + an
on-device solver. Chinese Postman is NP-hard in general but tractable on these
small sparse subgraphs; a greedy nearest-unvisited-edge heuristic is a fine v1.

---

<a name="46"></a>
## #46 — Ship Canada: 375 areas have index rows and trail counts but zero geom

375 `-ca-*` rows sit in `public/areas/index.json` with real trail counts (Banff
421, Jasper 199) but no geom is published for any of them, so they cannot be
browsed. There is no Canada publish path: `trailforge-publish-us.yml` is hardcoded
to 51 US state codes and `filter-ios-bundle.py` ships NA-only from clean geom.
This is a real pipeline lift (extract, AOI cut, publish workflow, bundle filter),
not a re-run. Deferred by the user 2026-07-19; kept open because it is the ONLY
remaining coverage gap now that the US is closed.

See auto-memory `coverage-gap-missing-areas`.

---

<a name="47"></a>
## #47 — Crash and stability pass on device

The one genuinely open QA item. Exercise the app on a real iPhone through
record/pause/resume, app-kill recovery (`restoreActiveRecording`, 12 h window),
backgrounding during a long hike, area switching, export/import, and unit toggling
— watching for crashes, hangs and memory growth. v1.0 is in App Store review, so a
crash found here is worth a point release.

⚠️ Do NOT run destructive flows (Reset / force-delete) against real hike data
without confirming a fresh Export exists first. User hike data is irreplaceable.

---

<a name="49"></a>
## #49 — Live Activity (#147) and distance-to-next-turn banner (#144)

Two long-standing drafts, both still valid ideas, neither built.

- **#147** — a Live Activity / Dynamic Island presence during an active recording
  showing distance, time and coverage so the hiker doesn't have to unlock.
- **#144** — an in-map banner giving distance to the next turn on the followed
  trail.

Both are app-code features needing a TestFlight build; neither is blocked by
anything. Parked, not rejected. Both PRs are still open on GitHub with their
branches intact (`claude/live-activity`, `claude/distance-to-next-turn`).

---

<a name="50"></a>
## #50 — Sign the Paid Applications Agreement and finish tax/banking

User-side, not code. The Paid Applications Agreement plus tax and banking details
are unfinished in App Store Connect. Not a blocker for the free v1.0 currently in
review, but it blocks any future paid tier or in-app purchase — nothing can be
sold until the agreement is active and the tax forms clear. Only the account
holder can do this.
