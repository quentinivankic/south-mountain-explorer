# assemble/ — trail-object assembly (v3)

**Implemented and unit-tested** (`model.py` + `assemble.py`, `test_model.py`
— 11 tests incl. a synthetic Devils Bridge that reaches the arch). The
homelab RUNS and tunes it on real OSM. Algorithm below is the contract the
code follows, distilled from `../SPEC.md`.

## The algorithm shape locked in by the plan

Input: `data/aoi/<name>.osm.pbf` (or the full hiking subset).
Output: `data/aoi/<name>.trails.geojson` — **one Feature per assembled trail**.

1. **Relations first.** Every `type=route` + `route=hiking|foot|walking`
   relation IS a trail object. Resolve superrelations transitively
   (the Te Araroa/GR20 lesson — see `data-pipeline/build/route_index.py`
   for a working two-pass pyosmium pattern). Member ways contribute
   geometry regardless of their `highway` value — a `steps` member is
   still part of the trail.
2. **Name-stitch what remains.** Ways not claimed by a relation group by
   normalized name across `highway` type boundaries (path→steps→path must
   NOT break a trail) when connected end-to-end (shared nodes). This
   replaces Systems 1/2's name-only grouping that fragmented at every
   type change.
3. **Attach spurs.** Unnamed (or differently-tagged) segments that connect
   to exactly one assembled trail and terminate at (or near) a destination
   POI — `natural=peak/arch`, `waterway=waterfall`, `tourism=viewpoint`,
   `mountain_pass=yes` — are welded onto that trail. This is precisely the
   missing 838 ft of Devils Bridge.
4. **Emit trail records:** stable id, display name (canonical `name`, with
   `name:en` fallback for display), full MultiLineString geometry, length,
   destination POI(s) reached, and raw curation signals (sac_scale, access,
   informal, network, …) for downstream scoring — scoring itself stays a
   separate, tunable policy exactly as in System 2.

## Definition of done (first milestone)

`make aoi && python3 assemble/assemble.py --aoi sedona` produces a
`Devils Bridge Trail` feature whose geometry passes within
`reach_tolerance_ft` of the golden destination. Then run the full golden
suite and iterate in the viewer (`make qa`).
