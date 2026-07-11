# SPEC.md — System 3 extraction + assembly (research-derived)

Distilled from the deep-research report (5 tracks, adversarial 3-vote
verification). Each load-bearing claim below is tagged with its
confidence: **[C]** = confirmed 3-0 with a cited source; **[E]** =
engineering judgment the homelab must measure/verify (the research run hit
the org spend limit during the final tooling/serving verifications, so A3
memory numbers and A4/A5 specifics are marked [E] and left for empirical
confirmation on the box).

## 0. The reframe (read this first) — [C]

**AllTrails' quality is hand-curated, not algorithmically assembled.** Its
"verified routes" (the thick green lines) are manually curated; its stated
goal is a hand-curated verified route for every trail.
(support.alltrails.com/hc/en-us/articles/4410231246100)

So "AllTrails-quality via pure automated OSM extraction" has a ceiling —
we will not match hand-curation with an algorithm. **But** the automated
best-in-class is well established and OSM-native, and it fixes Devils
Bridge: model trails **relations-first**, fall back to **name-stitching**,
then **attach spurs**. That is this spec. Where OSM lacks a route relation
or complete geometry, we surface that as a data gap (fixable by an OSM
edit) rather than papering over it.

**Reference implementation to mirror:** OpenTrailMap / **osmus/tileservice**
(`renderer/layers/trails.yml`, `pedestrian.yml`) — OpenStreetMap US's
production trail tile schema, published as Planetiler-style YAML. Treat it
as the authoritative tag spec and adapt it; don't reinvent it. [C]
(github.com/osmus/OpenTrailMap)

## 1. Extraction tag set — [C] for includes, [E] for exact exclusions

Ways to INCLUDE as candidate trail segments (superset of Systems 1/2; the
additions are the fix):

```
highway = path, footway, steps, track, bridleway, via_ferrata, cycleway, pedestrian
abandoned:highway = *            (lifecycle-flagged, low confidence)
```

`steps` and `via_ferrata` are explicitly trail ways in the OSM US schema —
their absence is the Devils Bridge / Angels Landing bug. [C]

EXCLUDE (at classification time, not the prefilter — prefilter stays
broad, refine in assembly):
- `footway=sidewalk` and `footway=crossing` (urban furniture, not trails)
- `indoor=yes`, `highway=corridor`
- `man_made=pier`
- `trail=no`
- `track` **only** kept with trail-appropriate access (not farm/forest
  service roads gated `motor_vehicle=yes` / road-name heuristic — reuse
  `data-pipeline/build/stage_osm.py`'s `_vehicle_or_utility_road`). [C]/[E]

Also fetch, as first-class inputs (Systems 1/2 never did):
- **Route relations:** `type=route` + `route=hiking|foot|walking|running`
- **Destination POI nodes:** `natural=peak|arch|saddle|cliff|volcano`,
  `waterway=waterfall`, `tourism=viewpoint|alpine_hut|wilderness_hut`,
  `mountain_pass=yes`, `natural=hot_spring`, and trailheads
  (`highway=trailhead` / `information=trailhead`). Used to define a trail's
  payoff endpoint and to weld the final spur. [E — POI list is judgment]

`osmium tags-filter` pulls referenced members by default, so matching a
route relation brings its member ways (incl. `steps` members) along even
when a member wouldn't match the highway filter alone. [E — verify on box]

## 2. Assembly algorithm (the core) — [C]

Ordered. Output: one record per assembled **trail object**.

### 2.1 Relations-first — [C]
Every `type=route`/`route=hiking|foot|walking` relation IS a trail object.
- **Member order is meaningful** — stitch geometry in relation order, do
  not re-sort by proximity. (wiki: Walking_Routes)
- **Resolve superrelations recursively** — a route relation may contain
  child route relations (stage-divided long trails: GR20, Te Araroa,
  Queen Charlotte). Walk members transitively. Reuse the two-pass
  pyosmium pattern already working in
  `data-pipeline/build/route_index.py`. (wiki: Walking_Routes)
- **Honor member roles** — `main` (empty role = main), `alternative`,
  `approach`, `excursion`, `connection`. The trail's canonical line +
  length come from `main`; `approach`/`connection` are access spurs;
  `alternative`/`excursion` are variants kept as attributes, not folded
  into the main length. (wiki: Walking_Routes)

### 2.2 Name-stitch the remainder — [C-adjacent]
Ways not claimed by any relation: group by **normalized name**, stitching
**across highway-type boundaries** (path→steps→path must not break a
trail) when segments are connected end-to-end (shared OSM node). This is
the direct fix for System 1's name-only, type-fragmenting grouping.
Normalization: canonical `name`; keep `name:en` and localized `name:<lang>`
for display fallback (reuse `stage_osm.area_name` pattern). Non-Latin
primary names (Mt Fuji 富士山) group by canonical name, emit a displayable
label.

### 2.3 Attach spurs — [E]
An unnamed (or differently-named) segment that (a) connects at a shared
node to exactly one assembled trail and (b) terminates at/near a
destination POI is **welded onto that trail**. This is the missing 838 ft
of Devils Bridge: the final `steps` spur to `natural=arch`. Guard against
over-merging: only single-parent, POI-terminating, short spurs; log every
weld for the QA viewer's "why did these merge" inspector.

### 2.4 Emit — [C for attributes]
Per trail: stable id, display name, full geometry (MultiLineString in
member/stitch order), length (from `main`), destination POI(s) reached,
and raw per-way attributes for downstream scoring —
`informal, operator, trail_visibility, surface, smoothness, width,
incline, sac_scale, access[+conditional], network, bicycle/foot/horse/…`,
plus QA tags `fixme/check_date/survey:date`. (OTM attribute vocabulary,
[C]). Scoring stays a separate tunable policy (System 2's split was
right); do not bake a score into geometry.

## 3. Global normalization without per-country code — [C]

- **trail_visibility**: 6 values → 3 buckets — followable
  (excellent/good), sometimes-hard (intermediate/bad), pathless
  (horrible/no). **Missing = no signal → include by default** (the tag is
  applied mostly to informal/backcountry paths; most maintained trails
  lack it). Never treat absent as poor. [C] (wiki: Key:trail_visibility)
- **network** grade `iwn>nwn>rwn>lwn` is a global signal (reuse
  route_index ranking). US/NZ/EU/JP idioms converge on route relations +
  these keys — no per-country branching needed; operator/network carry the
  authority signal generically.

## 4. Homelab tooling (≤16 GB) — [E], measure and record in HOMELAB.md

Design intent (numbers to confirm empirically on the box):
- **Filter-first streaming.** `osmium tags-filter` the planet (~80 GB
  PBF) → hiking subset. Streaming, bounded RAM; expected subset a few GB.
  This is the whole reason 16 GB suffices — never import the planet into a
  DB. [E]
- **Assembly on the subset** via pyosmium two-pass (pass 1: index
  relations + node coords + POIs; pass 2: build geometries). Bounded by
  subset size, not planet size. [E]
- **Planetiler** for final vector tiles: feasible at 16 GB for the
  filtered subset with `--nodemap-type=array --storage=mmap` (spills to
  disk); a full-planet Planetiler run is tight at 16 GB — prefer tiling
  the *subset*, not the planet. **Measure.** [E]
- **Incremental updates** via OSM replication diffs (`pyosmium-up-to-date`
  / `osmium apply-changes`) so we don't re-download the planet weekly. [E]
- **Fast AOI loop:** `osmium extract --bbox` a small area (Sedona) →
  assemble in seconds. This is the dev inner loop (`make aoi`). [E]

## 5. QA / eval — [E]

- **Golden harness** (`golden/golden.json`, done): per trail, cut an AOI,
  assemble, assert (1) reach: geometry within `reach_tolerance_ft` of the
  destination; (2) length within `length_tolerance_pct`; (3) fragmentation:
  the destination trail is ONE object, not N. Print a pass/fail table;
  Devils Bridge is the founding case.
- **Snap-verify**: golden coords are seeded approximations — snap to real
  OSM POIs on the box before trusting reach (`verify_golden.py --snap`).
- **Visual**: the local MapLibre viewer (done) overlays our trails on OSM
  raster + (reference) Waymarked Trails. AllTrails comparison is **manual
  visual only — no scraping** (their ToS forbids reproduction; this was
  being verified when the run stopped — treat as no-scrape regardless). [E]

## 6. Serving — [E], decide after the pipeline works

- Candidates: pmtiles (single-file, range-request from R2, MapLibre-native)
  vs per-area GeoJSON (System 1's model, simple/offline-friendly).
- Publish under a **new** R2 prefix; never touch `trekdex-areas` (Sys 1)
  or `trekdex-areas-dev` (Sys 2).
- Defer the format choice until assembly quality is proven; it doesn't
  block Phases D–E.

## 6b. Trail identity + deferred refinements (South Mountain QA)

**Identity by name, not by route.** A trail = the ways sharing a `name`
(`model.merge_same_name`, assemble step 4 folds same-named pieces into one
object — e.g. a "National Trail" split across a route relation + standalone
ways). Route relations (Maricopa Trail, West Highland Way) are a SEPARATE
overlapping layer — their own completable objects that route over named
trails; the overlap is real (two things share pavement), not a bug.

**Trail↔area is clip-based.** `--only-area` clips each trail to the area
boundary (`areas.clip_features_to_area`), keeping only its in-park portion
and discarding whatever runs outside. A connector that leaves the park to
reach a road (DC-Ray Connector) keeps its in-park piece; a trail entirely
outside clips to nothing and drops on its own — no fraction threshold. A
clipped trail's `length_mi` becomes its in-park length (`full_length_mi`
preserved, `clipped: true` flagged); a sub-`min_inside_mi` remnant is a
boundary sliver and is dropped. This is the first half of area-routing.

Deferred (do when full area-routing lands):
- **Per-area merge.** Same-name merge is AOI-wide (blind). The correct scope
  is per-area: assign each trail its area(s), then merge same-name WITHIN an
  area, so two different "Loop Trail"s in different parks never fuse.

  *Why it matters (AZ statewide scale run):* unclipped, 145 scattered ways
  named "Trail" fused into one 131-mi/119-island Frankenstein; "Ridge Trail"
  = 12 different parks' trails fused; 511/5,812 objects were provably
  scattered (bbox span > path length). At park scale (clipped to one area)
  the blind merge is CORRECT — that's why a Frankenstein only appeared in the
  unclipped whole-state test.

  *Connectivity-gating was considered and REJECTED.* It looked cheap, but:
  (1) `merge_same_name`'s "two disconnected same-name ways = one trail with a
  gap" is a DELIBERATE decision (see test_same_name_merges_into_one_object)
  that makes National Trail — a real trail with a **4.57-mi** internal gap —
  come out as one object; a connectivity gate reverses it. (2) National Trail
  proved a distance threshold can't separate "one trail with a big gap" from
  "two different trails nearby" — the distinguisher is AREA, not distance or
  connectivity. So the fix is genuinely per-area, not a gate.

  *Design being built:* `merge_same_name(trails, area_of=None)` — when
  `area_of` is None (park runs, existing tests) behavior is unchanged (blind
  within the one implicit area); when provided, group by (area, name) so
  merges never cross an area boundary. Backcountry trails in NO named area
  don't cross-merge. Area assignment by a representative point via stdlib
  ray-cast (keeps model.py pure-stdlib), areas from `assemble_areas`.
- **Hide long-distance routes in a park view.** *Data side landed:* every
  object now carries `kind: trail|route` (`model.classify_kind` — regional+
  network grade, a composite `--` name, or the word "Route"). Routes (Hayduke
  #13, the Narrows top-down/bottom-up routes, the Angels Landing composite)
  are kept but flagged so a park view can suppress them. Remaining: the
  viewer/app layer toggle that acts on `kind`.

## 6c. Canonical hikes — HARVEST, don't synthesize (EXPERIMENTAL)

**The completion unit is the *hike*, not the physical trail.** What people
mean by "Angels Landing" is the whole Grotto→summit journey — a route over
named trails — not the 0.43 mi spur OSM literally names "Angels Landing
Trail". Overlap between a hike and the trails it runs over is expected (a
hike is a curated overlay); the checklist is per-hike, not per-mile, so
double-counting miles is fine (AllTrails lists both Angels Landing AND West
Rim Trail).

**Decision (scalability): harvest OSM's route relations, do NOT synthesize
routes via graph search.** OSM route relations already ARE the crowd-sourced
canonical hikes — a human mapped the real popular route. Consuming them is
zero-heuristic and rides community curation that only grows; synthesizing
would need per-park heuristic tuning (trailhead selection, path realism) =
hand-curation in disguise. Where OSM has no route, we degrade gracefully to
the named-trail checklist and fill in as the map improves.

`model.promote_hikes` (assemble step 6): a *local* route (kind=route, NOT
network rwn/nwn/iwn) that terminates at a named destination POI is promoted
to `kind="hike"`, renamed from the payoff ("...--West Rim Trail" → "Angels
Landing Trail"). Thru-routes (Hayduke) stay `route`; named trails untouched.

Tiered plan (only tier 1 built): **1** harvest+rename (this); **2** deferred
destination-synthesis (only if coverage gaps prove painful — rejected as
default, not scalable); **3** a tiny hand-curated override table for marquee
spots where OSM is wrong.

Known open items on the branch: a promoted hike ("Angels Landing Trail") may
name-collide with the physical spur fragment OSM also names that — decide
whether to absorb/suppress the covered fragment. Checkpoint to revert the
whole experiment: branch `checkpoint/pre-canonical-hikes`.

**Worked example — the harvest ceiling biting (Ben Nevis, UK run).** Ben
Nevis has NO route relation, so tier-1 harvests nothing — and OSM splits the
ascent into two overlapping named trails: `Ben Nevis Trail` (the fuller
approach, but modeled to END at the junction, *short* of the summit) and
`Ben Nevis Mountain Path` (reaches the summit POI, otherwise a subset of the
Trail). Neither object is the clean "whole hike to the top." NOT a bug (we
render OSM's relations faithfully; the summit is reachable — Ben Nevis golden
PASSES via the Mountain Path), but it's the identity gap made visible: the
marquee object stops short of its payoff and the summit segment lives in a
different named way. This is the case tier-2 (synthesize trailhead→summit,
stitching the final segment regardless of which way owns it) or tier-3 (a
one-line marquee override) would fix — kept as the concrete test case for
when we decide which. Contrast South Mountain's clean zero (no split, just
no hikes); Ben Nevis is the *awkward* ceiling.

## 6d. Known gap — super-relation stitching (TODO, Scotland/UK run)

**Staged long-distance trails fragment.** The West Highland Way is a
super-relation whose children are per-stage route relations, each carrying a
distinct name (`West Highland Way (Kinlochleven to Fort William)`,
`… (High Route to Fort William)`). Relations-first emits each stage as its
own object, and because the stage names differ, `merge_same_name` can't
recombine them — so a 96-mi trail shatters into stage-fragments, none
spanning both endpoints. Golden `West Highland Way` stays FAIL
(assembly-gap, not missing-data) and is the tracker for this.

Fix (SPEC §2.1, not yet wired): when a `type=route` relation is a MEMBER of
another route relation, resolve the parent transitively and attribute the
child's ways to the parent (parent name wins), instead of emitting each
child as a top-level object. Generalizes to GR20, Te Araroa, any staged
thru-trail. Affects the route layer only (thru-routes are `kind=route`), not
the named-trail checklist — bounded, so it's logged, not urgent.

Otherwise the UK run VALIDATED "worldwide without per-country code": Ben
Nevis + Old Man of Storr golden PASS, `nwn`/`rwn` classified as routes
correctly, Gaelic (Latin-script) names clean, zero promotion misfires.

## 6e. Planned upgrade — real difficulty from elevation gain (global DEM)

**Difficulty today is a length bucket.** `serve/to_app_json.py::difficulty`
is: `sac_scale` harder than `hiking` → Hard; else `>4mi` → Hard, `>2mi` (or
`trail_visibility=intermediate`) → Moderate, else Easy. But `sac_scale` /
`trail_visibility` are European conventions rarely present on US/most trails,
so in practice difficulty ≈ pure length, with arbitrary 2mi/4mi cutoffs. This
is the weakest part of the "AllTrails-quality" claim: it ignores **elevation
gain**, the dominant difficulty factor — a flat 5mi path reads Hard, a 2mi
climb with 2,000ft gain reads Moderate (backwards).

**Fix — sample a global DEM ourselves.** Elevation is genuinely absent from
2D OSM ways, so it can't be derived from geometry; but the *data* is free and
global and the *gain math* is ours (no US-only source, no paid API):

- **Source:** Copernicus DEM **GLO-30** (ESA, 30m, truly global incl. poles,
  free/attribution) — or **AWS Terrarium terrain tiles** (global pre-merged
  SRTM+Copernicus+3DEP, PNG RGB-decoded, free on AWS Open Data), which are the
  easiest to sample. Both license-clean. (Plain SRTM/NASADEM stop ~60°N — use
  Copernicus to avoid the high-latitude gap.)
- **Compute:** per trail, densify the line to ~30m spacing, look up elevation
  at each point, sum positive deltas = gain (+ max grade). Difficulty becomes
  a real function of gain + length; ship the gain as a per-trail stat too.
- **Mandatory smoothing:** raw 30m DEM elevation is noisy and naive
  up-tick summing MASSIVELY inflates gain (4,000ft on a rolling 3mi trail).
  Smooth the profile (small moving window) before summing — same as
  AllTrails/Strava. Skip it and the numbers are garbage.

**Pipeline fit:** homelab downloads the DEM tiles covering the region
(cached), samples during/after assembly. Sandbox can't reach DEM sources —
homelab work. Logged now; build once coverage is stable (highest-leverage
quality lever remaining). Not US-only by design.

## 7. Open questions the homelab loop resolves

1. Actual hiking-subset size + prefilter runtime on planet (validates the
   ≤16 GB thesis).
2. Spur-attach precision/recall — does §2.3 fix Devils Bridge without
   over-merging elsewhere? (golden suite answers.)
3. Planetiler-at-16 GB on the subset vs. per-area JSON.
4. Coverage delta vs System 2 on the same country (Switzerland/NZ) — how
   much do relations-first + steps actually add?

## Sources (confirmed claims)
- AllTrails verified-routes vs OSM: support.alltrails.com/hc/en-us/articles/4410231246100
- OsmAnd routes (relations as first-class): osmand.net/docs/user/map/routes/
- OSM Walking Routes (relations, superrelations, roles, order): wiki.openstreetmap.org/wiki/Walking_Routes
- OpenTrailMap / osmus schema (tag set incl. steps/via_ferrata, attributes): github.com/osmus/OpenTrailMap
- trail_visibility buckets + default-include: wiki.openstreetmap.org/wiki/Key:trail_visibility
