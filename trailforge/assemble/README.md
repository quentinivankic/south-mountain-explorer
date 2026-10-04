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
   for a working two-pass pyosmium pattern). Outside Denmark, relation members
   retain the original standalone `_is_trailish` eligibility contract. For the
   Denmark pilot only, a signed hiking relation may restore main-line
   `residential`, `unclassified`, `living_street`, and `pedestrian` members.
   `service` additionally requires `foot=yes|designated|permissive`; every
   present semicolon-separated token is case/space/hyphen/underscore normalized
   and must belong to the explicit ordinary allowlist (`alley`). Parking,
   parking-space/aisle, driveway/drive-through, emergency-access, bus, blank,
   and unknown single or composite forms fail closed; a missing subtype follows
   the positive-foot policy above. Tracks still pass the legacy trail gate; an
   explicitly shared motor track additionally requires positive foot access.
   Denmark restoration parses positive integer `lanes` components separated by
   semicolons: any component >=2 or any nonempty malformed/unknown value is
   road-like and denied. Road-like names/codes and unsafe ATV/OHV-style tags
   also prevent restoration even when foot access is present. Outside Denmark,
   the legacy standalone lane disposition remains unchanged, including malformed
   historical values, and known agency road-code prefixes remain unanchored.
   Sidewalks, restricted or indoor ways, `trail=no`, non-hiking pistes,
   motorways/trunks/construction/raceways, and 4WD/OHV ways remain excluded.
   Restored ways do not become standalone checklist entries. The QA report emits
   one measurement per relation using only exact-area-clipped, raw-bound direct
   member ways; same-name linework and welded spurs cannot dilute the denominator.
   Aggregate mileage counts each `(relation, way)` tuple once. First-pilot
   acceptance requires at most 1.0 restored mile and 10% per relation, plus at
   most 10% aggregate. Every accepted Denmark route also emits a complete,
   ordered direct-member audit with raw decisive tags, effective role, shared
   policy decision, and inclusion/exclusion reason. An excluded member, missing
   source object, or entirely filtered route is unresolved and fails the pilot.
   A `steps` member remains part of the trail.
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

For exact-area Denmark QA, clipping and the sliver floor use unrounded geometry
and mileage. The legacy `area_name is None` path preserves the prior rounded
three-decimal floor. Source-connected relations and name stitches split only by
the selected boundary carry the independent `boundary_induced_split=true`
fact; blocking `quality_disposition` values remain intact. Before assembly, the
pilot's standalone pyosmium exporter reads the same runner-local AOI PBF and
atomically writes a sealed canonical `relation-members.json` graph. The final
pilot validator binds every serialized relation hierarchy and ordered direct
membership claim to that graph, binds graph way tags and serialized source
coordinates to the independent `raw.geojson` `w<id>` export, treats missing
source evidence explicitly, and reconciles ingest diagnostics against only the
final post-clip, post-quality route population. The compact run-33 replay and
its deterministic local-only generator under `testdata/` are test-only and
never app output.

## Definition of done (first milestone)

`make aoi && python3 assemble/assemble.py --aoi sedona` produces a
`Devils Bridge Trail` feature whose geometry passes within
`reach_tolerance_ft` of the golden destination. Then run the full golden
suite and iterate in the viewer (`make qa`).
