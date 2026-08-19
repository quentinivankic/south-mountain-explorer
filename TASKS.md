# Open tasks

**Why this file exists.** The task list Claude keeps in-session (`TaskList` /
`TaskGet`) does NOT survive into a new session — it is session-scoped state, and
a fresh session starts with nothing. These tasks carry hard-won measurement
in their descriptions: national counts, named counter-examples, and approaches
already ruled out with evidence. Losing them means re-deriving all of it, or
worse, re-proposing something already disproved. So they live here, in the repo,
and a new session should re-create them with `TaskCreate` from this file.

(18 open tasks as of 2026-08-15.)

Numbers are the original task ids. Gaps (#19–#20, #22–#25, #27–#30, #32,
#38–#43, #45, #48) are completed work — see git log and `TODO.md`.

Last synced from the live task list: **2026-08-15**. That sync found #52
present only in the session task list and nowhere in this file — exactly the loss
this file exists to prevent. It is written up below with what is actually known,
which is not much.

| # | Task | Kind |
|---|---|---|
| [51](#51) | Boundary ids: 2,226 areas still have none | data · roll BLOCKED by #53 |
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
| [49](#49) | Live Activity (the turn banner SHIPPED in #555) | app |
| [50](#50) | Paid Applications Agreement | user-side |
| [53](#53) | Adjudicate parking by aerial + vision, per lot | **data · THE TRUTH for parking; 11 areas done** |
| [54](#54) | Trailhead spurs trimmed by `_trim_to_parks` — trails end short of the trailhead | data · pipeline |
| [52](#52) | One car park mapped as many OSM polygons ships as many pins | data · unmeasured |
| [55](#55) | Device-test the rebuilt area sheet | QA · needs a TestFlight build |
| [56](#56) | Difficulty calls short brutal climbs Moderate | data · needs a non-threshold answer |

---

<a name="53"></a>
## #53 — Adjudicate parking by aerial + vision. Tooling built; 11 areas done.

> ### DECIDED 2026-08-12: this is the source of truth for parking KEEP/DROP.
>
> The user chose the per-lot vision verdict over any geometric gate, and wants to
> resume within a couple of days. Everything else that proposes to remove parking
> defers to it — see the blocker now on [#51](#51) and the note on [#52](#52).
>
> **Why, in one fact.** The vision protocol's own SERVES axis is the app's rule
> (nearest lots within 805 m of a trail's endpoints, 5 km fallback), NOT
> containment — because trailheads sit OUTSIDE park polygons by nature. They are
> roadside pull-offs on the approach road. That is the same fact that makes
> `_trim_to_parks` sever access spurs in [#54](#54).
>
> **Where it lives, verified 2026-08-12:** `/mnt/raid/trekdex/parking-adjud/` —
> `README.md`, `tools/` (17 files incl. `judge_protocol.md`), `data/` (82 files,
> 99 MB, 14 verdict stores), `artifacts/`, `osm/`. Read the README first.
>
> ⚠️ **The root disk is at 94% with 5.7 GB free.** A disk-full has already
> truncated a verdict file once. Check `df -h /` before a run, and keep every pbf
> on `/mnt/raid`.
>
> **Resume path:** the reversible `public/areas/parking-verdicts.json` sidecar
> keyed by OSM id, shaped like `nonhiking-trails.json`, honoured by the pool
> builder, and unable to empty an area. Then graduate `dossier.py`,
> `context_classify.py` and `judge_protocol.md` into `scripts/`.

**State 2026-08-01: the method is built and exercised on 6 areas; nothing ships
yet.** Durable home (job tmp is ephemeral): **`/mnt/raid/trekdex/parking-adjud/`**
— `README.md` (full pipeline + lessons), `tools/`, `data/` (dossiers, serves,
verdicts, `groundtruth.json`), `artifacts/`, `osm/`. Auto-memory
`parking-vision-adjudication` mirrors the lessons.

**Verdict = 3 axes, KEEP needs all:** EXISTS (real lot, not a pullout — vision;
a surveyed `amenity=parking` stays unless imagery positively contradicts it),
PUBLIC (not `access=private/no/customers`), SERVES (a trail we ship, within
overflow range). Serves = the app's `nearestParkingWithFallback` (nearest 3
within 805 m of trail endpoints, else nearest 2 within a **5 km fallback cap**),
run area-agnostically. DROP only = private / not-real-parking / non-public
facility lot / >1 mile walk. **Overflow is real: a public lot the app surfaces is
a KEEP even if it mainly serves a ball field — facility-adjacency is NOT a drop
or a flag.** OSM context (`context_classify.py`) drives the DROP side
(apartment/resort/church/commercial by edge-adjacency), never public overflow.
Imagery: **NAIP primary** (ESRI throttles under burst); zoom to ~0.14 m/px, draw
the polygon, feed the tags to the judge (the #1596 miss came from a wide frame +
hidden tags).

**Results (keep / drop, 0 reviews left):** Zion 40/17 · Griffith 43/14 · Phoenix
Mtns Preserve 28/25 · Pinnacle Peak 11/6 · Usery 21/7 (Camelback/Echo Canyon judged
too, 16/6, folded into Preserve via bbox overlap — not published separately).
Griffith+Zion generalization measured **0 confident-wrong** vs the user's own calls.

**NEXT:** (1) reversible `public/areas/parking-verdicts.json` sidecar keyed by
**OSM id** (fids are run-local), shape of `nonhiking-trails.json`, honoured by the
pool builder, can't empty an area. (2) Graduate `dossier.py`+`context_classify.py`+
`judge_protocol.md` into `scripts/`; sub-agents inherit `judge_protocol.md` for
scale. (3) Feed trailhead coverage-gaps (Narrows, Right Fork, Zion #190 spur) to
the trail work.

---

<a name="54"></a>
## #54 — Trailhead access spurs get trimmed by `_trim_to_parks`; trails end short of their trailhead

**FOUND + CONFIRMED IN CODE 2026-08-02**, from the NE parking review (Grafton Loop East
trailhead, review lot #1). Shipped trail geom often stops 1–2 km short of the real
trailhead — and it is NOT missing OSM data.

**Evidence (Grafton Loop Trail):** OSM runs continuously to the trailhead lot — nearest
point **9 m**. Of 97 OSM vertices in the trailhead corridor, our shipped geom keeps 6 and
drops a contiguous run of 91 (~1.5 km), stopping at the last in-park point 1,267 m from the
lot. (Reproduce: `osmium tags-filter <ctx.pbf> w/name="Grafton Loop Trail"` vs the shipped
`grafton-notch-state-park-me.json`.)

**Mechanism:** `trailforge/serve/publish_areas.py::_trim_to_parks` (the "DC-Ray fix", PR
#338) clamps every trail to the UNION of park boundaries and TRIMS any dangling end that
dead-ends outside all parks (in→gap→in cross-park connectors are kept whole). It exists to
strip residential dead-ends (a connector running into a neighbourhood). A trailhead access
spur dead-ends at a lot on the approach road just OUTSIDE the park, so it looks exactly like
a residential dangle and gets cut.

**Why it matters:** trailheads sit outside park polygons by nature (roadside pull-offs), so
this SYSTEMATICALLY severs the last stretch to the real trailhead. It is the root cause of
"trailhead parking reads as 1–2 km from any trail" and why the parking serves-gate needs its
5 km fallback (#53). The trail-clip and the parking coverage-gap are the same bug from two
sides.

**ANALYSIS — validated 2026-08-02; the naive in-trim fix does NOT hold up:**
- Systematic, confirmed on 2 independent cases: Grafton Loop Trail (OSM reaches the lot at
  9 m, ours stops 1,267 m short) and Pumpelly Trail / Monadnock (OSM 141 m, ours 2,206 m).
- Blast radius bounded: 2,924 of 40,339 geom parking lots (7%) sit in the 805 m–3 km
  fallback band — an UPPER bound on affected lots (many are far for unrelated reasons).
- `highway=trailhead` is too sparse to anchor the fix: Grafton's real trailhead has NO such
  node (the only one in the whole area is 10.9 km away). The parking lot at the spur end is
  the signal that actually fires — the trailhead-node half wouldn't even fix the motivator.
- BUT parking data does not exist at trim time — `_trim_to_parks` runs in `publish_areas.py`;
  parking is added LATER by `add-parking.py`. So this is NOT a predicate tweak in the trim;
  it needs new data plumbing or a different layer.
- `_trim_to_parks` is load-bearing: #338 built it because residential tails made trails FAIL
  the majority-length test and get SILENTLY dropped — loosening it risks that regression.
- Harm is modest: the parking serves-gate ALREADY surfaces the trailhead lot via its 5 km
  fallback, so the app-facing "where do I park" is met. The trail-side loss is cosmetic-ish
  (trail starts short; elevation/orientation/spur-completion).

**RECOMMENDATION:** do NOT modify `_trim_to_parks` inline as first proposed. Either
**(A, preferred)** keep this as a coverage-gap punch-list and make NO pipeline change — the
parking fallback already covers the app need and the harm is small; or **(B, if we fix it)**
build it as a post-process in `add-parking.py`, where the parking data lives: when a
fallback-only trailhead lot has OSM trail geometry connecting it to a shipped trail's free
end, re-attach that spur, anchored on the PARKING lot (not the sparse trailhead node), and
preserve the majority-test guard. B is real work (needs the OSM spur geometry publish
discarded) and must be measured against the #338 DC-Ray Connector + the 2,924-lot candidate
set first. Repro for the two confirmed cases: `osmium tags-filter <ctx.pbf> w/name="<trail>"`
vs the shipped geom endpoints.

See auto-memory `parking-vision-adjudication` lesson #7. Related: #53 (parking), #35
(fragmentation), #51 (boundaries).

---

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

<a name="51"></a>
## #51 — Boundary ids: 4,303 recovered, 1,848 areas still have none

**MOSTLY DONE 2026-07-29 (PR #513).** `add-parking.py`'s containment gate needs a
boundary polygon, looked up by id, and only **2,909 of 9,060** shipped areas had
one. The rest fell back to proximity, which cannot tell "inside the park" from
"across the road".

Cause: `seed-areas.py` records the id only for RELATIONS, because the app
computes an Overpass area id as `osmId + 3_600_000_000` — the relation-only
offset — so a way id there would point its live fallback at the wrong polygon.
Way-sourced areas got None and nothing else wrote it down.

FIXED BOTH WAYS: `assemble/areas.py` now returns `a.orig_id()`/`a.from_way()`
(it always had them) and `to_app_json` writes a SEPARATE `osm_way_id`; and
`scripts/backfill-area-boundary-ids.py` recovered the existing set by matching
park polygons from the local extract on name + position + **≥90% trail
coverage**. Coverage **32% -> 80%** (2,916 ways + 1,387 relations added).
Vermont's loaded boundaries went 29/103 -> 82/103.

**WHAT IS LEFT:**

1. **1,848 areas still have no boundary.** 1,713 were REJECTED by the 90% trail
   gate (the matched polygon holds too little of the area), 130 matched a name in
   the wrong place, 5 matched no name at all. The rejects are the interesting
   set: publish clips to a UNION of same-named siblings, so a single polygon can
   legitimately hold only part. Recording the whole SET of ids, rather than one,
   would recover most of them.
2. ⚠️ **BLOCKED 2026-08-12 — do NOT re-run the roll.** The user decided the
   per-lot vision verdict ([#53](#53)) is the truth for parking, and this roll is
   the competing instrument. It would apply containment nationally, which is the
   criterion the vision runs measured as WRONG for trailheads: they are roadside
   pull-offs outside the park polygon by nature. Cady Hill Forest's three lots sit
   179-556 m outside its boundary, which is the shape of a real trailhead.

   **And there is no safety net.** Verified in code 2026-08-12:
   `add-parking.py::pool_candidates` states that callers apply the containment and
   road gates FIRST, so a lot the gate rejects never reaches
   `public/areas/parking-pool.json` and never reaches the global pool either. The
   pin disappears from the whole app, not just from that area.

   **The two halves are separable and half is still worth doing.** Recovering the
   boundary ids helps trail clipping and any future gate, and changes no parking
   on its own. Measured 2026-08-12 over shipped geom: 6,848 of 9,074 areas carry a
   boundary id (75.5%); 2,226 do not; 1,437 of those ship 9,420 lots admitted by
   proximity alone. Those 9,420 are what a re-run puts at risk.

   Original note kept below for the numbers.

3. ⚠️ **THE ROLL HAS NOT BEEN RE-RUN, and doing so REMOVES parking.** With a real
   boundary the gate drops lots that proximity had been admitting. Vermont's dry
   run: 4 areas cleared, e.g. Cady Hill Forest's three lots sitting 179-556 m out
   and outside the boundary; Breadloaf Wilderness loses two USFS trailheads that
   the global pool still carries. That is the gate working, but it is a
   user-visible removal and needs a decision before a national roll.

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
  off-road members diverge by >10% — still TBD (needs the OSM relation re-read)
- **geom-side symptom baseline MEASURED 2026-08-02** (`scripts/audit-namestitch-teleport.py`,
  off shipped geom, no network, ~35 s): **1,647 shipped trails teleport** — split into
  ≥2 spatially-disjoint chunks whose two biggest are ≥1 km apart (1,119 ≥2 km, 722 ≥4 km).
  **840 of those are INVISIBLE to #31's profileGaps measure** (they are separate segments
  with no in-segment gap), so this is additive, not a re-count. 990 distinct names, 332
  appearing in >1 area (route-duplication overlap with #31). **621 have generic/color/
  short names — the high-confidence welds:** smoking gun **"Yellow" in adirondack-park,
  two chunks 111 km apart**; "Sawmill", "White Rocks Trail" likewise. CAVEAT: the flag is
  the union of real welds AND genuinely long routes with missing-data gaps (#35, Mokelumne
  Coast to Crest 174 km) — geom can't split those two; that IS the relation-vs-name-stitch
  decision below.
- ⚠️ the motivating "Crawford Notch name-stitch bug" (a parking by-product) was a
  MISDIAGNOSIS — verified 2026-08-02, the Tuckerman Ravine Trail is one continuous ~4 km
  line (max vertex gap 128 m); it was the parking dossier's region buffer pulling real
  Mt-Washington trailheads into crawford's per-area run, not a trail bug. See auto-memory
  `parking-vision-adjudication` lesson #9.
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
- **#144** — ~~an in-map banner giving distance to the next turn~~ **SHIPPED
  2026-08-12 as #555**, rebuilt on current main rather than merged: PR #144 sat
  380 commits behind and proposed a single-vertex angle detector, which was
  measured over a random 120-area sample and is unusable — at 25 degrees the
  median gap between consecutive "turns" is 27 m. A sustained heading change over
  50 m at 60 degrees gives 150 m instead. `PolylineMath.nextTurn`, 11 tests. Close
  #144; only the Live Activity half of this task is left.

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


---

<a name="52"></a>
## #52 — One car park mapped as many OSM polygons ships as many pins

**Recorded 2026-08-12 with an explicit hole in it.** This task existed only in a
session-scoped task list — one line, no measurement, no named example. The
session that raised it is gone, so what follows is what the code says, not what
was originally measured. Treat every number here as absent, not as zero.

**Deferring to [#53](#53) as of 2026-08-12.** Any merge that REMOVES a pin is a
parking KEEP/DROP call, and the per-lot vision verdict is the truth for those. A
pure de-duplication that draws one pin where two described one lot is fine.

The phenomenon: OSM often maps a single car park as several adjacent polygons
(separate aisles, separate surface patches, a mapper splitting on a kerb). Each
polygon centroids to its own pin, so one lot can draw as three or four "P"s.

Partial mitigation already shipped: `scripts/build-parking-pool.py` dedupes at
`DEDUP_M = 40.0` m when building the global pool, matching `PARKING_DEDUP_M` on
the `add-parking.py` side. So pins closer than 40 m already collapse. What is NOT
known is how much survives that: a long lot split lengthwise can easily place two
centroids more than 40 m apart while being one car park.

**Before doing anything here, measure it** — count areas where two pool lots sit
within, say, 150 m AND their OSM polygons share an edge or are separated only by
a service road. Union-find on shared geometry is the honest test; a distance
threshold alone will merge genuinely separate lots at a trailhead complex, which
is the same over-merge trap named in `parking-vision-adjudication`.

---

<a name="55"></a>
## #55 — Area sheet: FIXED WITH VISUAL PROOF, awaiting the device verdict

**The loop that ended it.** Builds 296-299 all shipped area-sheet "fixes" that
were reasoned from source and never seen — `which xcodebuild swift` exits 1 on
the homelab. The user's phone was the test harness for seven-plus rounds. It is
not any more:

```
gh workflow run ios-screenshots.yml --ref <branch> -f test_class=AreaSheetAuditTests
gh run download <id> -n appstore-screenshots     # then actually LOOK at the PNGs
gh run view <id> --log | grep AUDIT              # element frames, y / maxY / screenH
```

`ios/SouthMountainExplorerUITests/AreaSheetAuditTests.swift` photographs eight
states and asserts two invariants. ~19 min per run, measured over four runs.

| Build / run | Verdict |
|---|---|
| 296 `7c71eee6c` | last build with no crash. Clipped. |
| 297 `a0070fcaf` | crashed on trail tap and on drag-to-min |
| 298 `cef6913e8` | crash gone. Still clipped. |
| 299 `1cc53e921` | still clipped — user screenshot showed a sliced first row |
| audit 32201804788 | FAILED before reaching the sheet — Stats' Area Progress row moved below the fold when #550 inlined Insights. Fixed by scrolling to it. |
| audit 32203094649 | found 3 defects |
| audit 32204672482 | min stop + scroll clean; 2 defects left |
| audit 32206102097 | **all 8 states clean** |

**What the photographs found — none of it guessed:**

1. **Sliced fourth row at the min stop.** The sheet stands one home-indicator
   band taller than the detent height asks for, and the browse pages let the
   list run into it. The band is now subtracted from the browse stop. Record
   keeps it as air — finite content, photographed clean.
2. **Scrolling parked a headless `4.08 mi · 515 ft` caption under the search
   field** — the "clipping behind the header" report, verbatim. Rows and their
   dividers are one cell now, marked `.scrollTargetLayout()`, with
   `.scrollTargetBehavior(.viewAligned)`: a flick settles on a whole row. The
   hand-tuned `+3` divider fudge is gone, because the cell measurement includes
   the divider.
3. **The trails page sat 16pt high after deselect.** `min-idle` search-field
   `y=746` vs `min-trail-deselected` `y=730`, with `area-name y=651` in both —
   the sheet was right, the page inside was not. The page-level
   `.ignoresSafeArea(edges: .bottom)` went stale on sheet SHRINK. Removed; the
   list's own ScrollView carries the ignore. Run 32206102097: both read `y=730`.
4. **The selected row's chart was cut.** The re-scroll ran one hop after
   selection, but the stop commits 140 ms later (`minHeightCommit`), so it used
   the old viewport. Now 260 ms.
5. **The header stopped hiding.** Hiding the park name at the min stop was the
   same "control that vanishes" mistake as the search bar's, and gave one
   measurement two heights to carry.

**Refuted along the way, recorded so it is not re-proposed:** header churn as the
cause of defect 3 (run 32204672482 shows the header already static and the shift
surviving), and the layout-cycle fix in #579 as the cause of the 297 crash (`git
show` finds the identical cycle in 296, which does not crash).

**Still unverified:** how `.viewAligned` snapping feels in the hand, and whether
the partial bottom row at the medium/half stops reads as clipping — it is
ordinary scrolling-list behaviour, and the min stop is the one sized to fit
exactly.

**Rule going forward:** no TestFlight build for a UI change until the simulator
has photographed it. One capture run costs ~19 min; a bad build costs a hike.


---

<a name="56"></a>
## #56 — Difficulty calls the hardest short climbs "Moderate"

**Reported 2026-08-15: Echo Canyon Trail on Camelback reads Moderate.** It is
about the hardest hike in the Phoenix valley short of a 20-miler.

**Why, measured.** `difficulty_label` in `trailforge/serve/elevation.py` has two
tests and Echo Canyon fails both.

- NPS rating `sqrt(2 * gain * miles)`: Echo Canyon scores **51.9**, Hard needs
  **80**. The formula scales with the square root of DISTANCE, so a short brutal
  climb structurally cannot reach it. This is the real defect; the floor below is
  a patch over it.
- Steepness floor: Echo Canyon is 1,390 ft in 0.97 mi = **1,433 ft/mi**, the
  floor is **1,500 ft/mi**. Misses by 4.5%.

**The floor never caught the trail it was written for.** Its docstring says it
exists because Acadia's Precipice Trail — iron rungs bolted to a cliff — was
reading Easy. Precipice is 966 ft in 0.67 mi = **1,442 ft/mi** and misses 1,500
as well. The floor moved it Easy -> Moderate and stopped.

**DO NOT just lower the number.** The user's standing position, restated on
2026-08-15: *"you know i don't like thresholds numbers so we need to be smarter."*
For the record, dropping the floor to 1,400 was measured over all **92,400**
trails carrying a DEM gain and re-rates **127 of them (0.14%)** — Echo Canyon,
Precipice, Mount Colden, Manamana, Henry Stimson. That is the SIZE of the
problem, not the fix.

**Directions worth exploring, none of them chosen:**

- **Rank, not magnitude.** "Hard" as a percentile of steepness rather than a
  ft/mi cut. Says something true relative to what exists, and never needs
  retuning as coverage grows.
- **Fit to known trails.** Build a ground-truth list the user can grade from
  memory — Echo Canyon hard, Piestewa moderate, Precipice hard — and choose
  whatever function reproduces it. Same shape as the parking groundtruth in
  `parking-vision-adjudication`.
- **Use the profile, not the average.** `profileFt` ships at 32 samples/mile, so
  SUSTAINED steepness over a window is computable. A trail with one savage pitch
  and a trail evenly graded to the same average are not the same hike, and an
  average grade cannot tell them apart.
- Per CLAUDE.md #12, put a stratified sample in front of the user's eyes before
  building whichever rule wins.

**Architectural note that decides how the fix ships.** The label is baked into
geom at publish, so changing it today means republishing all 50 states. Both
inputs (`gainFt`, `distanceMi`) and now `profileFt` already ship to the app, so
the label COULD be computed on the phone. Then this is a build rather than a
national roll, and the next revision costs one line instead of a re-publish.
