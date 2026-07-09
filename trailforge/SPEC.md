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

Deferred (do when area-routing lands):
- **Per-area merge.** Same-name merge is currently AOI-wide (blind). The
  correct scope is per-area: assign each trail its area(s), then merge
  same-name WITHIN an area, so two different "Loop Trail"s in different
  parks never fuse. Low risk today (we work in park-sized AOIs).
- **Hide long-distance routes in a park view.** Keep route objects
  (`network=rwn/nwn/iwn`) but let a park/area view suppress them so a local
  view isn't cluttered by a county/thru route crossing it.

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
