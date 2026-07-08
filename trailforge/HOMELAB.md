# HOMELAB.md — handoff for the Claude Code session running on the homelab

You are picking up **System 3 (trailforge)**: a clean-slate, planet-scale
pipeline extracting AllTrails-quality trail objects from OSM. This doc is
your context. Read it, then `SPEC.md`, then start at **Step 1**.

## Why this exists (the founding bug)

The live app ships *Devils Bridge Trail* (Sedona, AZ) as a 0.82 mi line
that stops **838 ft short of the actual bridge** — and no trail in the
area's data gets within 600 ft of it. Verified against the committed
System-1 geometry (`public/areas/geom/coconino-national-forest-az.json`).
Two root causes, shared by BOTH prior systems:

1. **`highway=steps` excluded.** System 1's Overpass filter
   (`scripts/build-trail-counts.py:60`) and System 2's osmium tags-filter
   (`.github/workflows/build-region-tiles.yml`) both fetch only
   `path|footway|track|bridleway`. Devils Bridge's final approach is a
   staircase. Any trail whose payoff is stairs/scramble/spur is clipped.
2. **Name-only grouping.** System 1 groups geometry strictly by way name;
   System 2 stitches name+connectivity but treats route relations as a
   *scoring signal*, not as first-class trail objects. Trails fragment at
   type/name changes; spurs detach.

OSM itself has the trail complete — **the source is fine; the lens was
broken.** Your job: build the correct lens.

## Locked decisions (from the user — don't relitigate)

- **True clean slate**, but Systems 1/2 stay untouched and running
  (GitHub + Cloudflare). Never edit `scripts/`, `data-pipeline/`,
  `public/areas/**`, or their workflows from this effort.
- **Quality bar: AllTrails-like trail objects** — one named trail, full
  geometry trailhead→destination, correct length.
- **This machine (≤16 GB RAM) is the compute.** Filter-first streaming:
  `osmium tags-filter` the planet down to a hiking subset (a few GB), do
  ALL downstream work on the subset. Never a full-planet DB import.
- **OSM-first.** No per-country data sources (the NZ DOC dependency was
  removed for flakiness; that lesson stands). AllTrails comparison is
  **manual visual only — never scrape**.
- **QA viewer is local-only** (`make qa`).
- Licensing: ODbL — attribute "© OpenStreetMap contributors" everywhere
  output ships. WDPA is prohibited (commercial use). 
- When publishing samples to Cloudflare later: a **new** R2 prefix/bucket
  only; System 1 (`trekdex-areas`) and System 2 (`trekdex-areas-dev`)
  data must not be touched.

## What's already here

| Path | What |
|---|---|
| `SPEC.md` | Research-derived extraction+assembly spec (tag set, algorithm, tools) |
| `golden/golden.json` | 20-trail regression suite: destination coords + reach tolerances, each encoding a failure mode (steps-finish, superrelation, non-Latin names, …) |
| `tools/verify_golden.py` | Structural validation (works offline); `--snap` mode is **yours to implement** (Step 3) |
| `extract/prefilter.sh` | Streaming tags-filter, v0 tag set **including steps/via_ferrata + destination POI nodes** |
| `extract/aoi.sh` | Sedona-default bbox cutter + GeoJSON export — the fast iteration loop |
| `assemble/README.md` | The assembly algorithm you will implement (relations-first → name-stitch → spur-attach) |
| `viewer/` | MapLibre QA viewer: OSM raw layer vs assembled-trails layer vs golden markers |
| `Makefile` | `setup · download-extract · prefilter · aoi · verify-golden · qa · test` |

Also relevant, read-only: `data-pipeline/build/route_index.py` — a working
two-pass pyosmium pattern that resolves route **superrelations**
transitively (the Te Araroa fix). Reuse the approach in the assembler.

## Your steps

1. **Bootstrap:** `make setup && make download-extract && make prefilter`
   (north-america first — planet later once the loop works). Record subset
   size + runtimes in this file.
2. **AOI loop up:** `make aoi && make qa` → open
   `http://localhost:8000/viewer/?aoi=sedona`. You should see raw OSM ways
   incl. the steps near Devils Bridge, and the golden marker on the arch.
3. **Implement `verify_golden.py --snap`:** for each golden entry, match
   `osm_hint` against POIs in the subset near the seeded coordinate
   (pyosmium pass; Nominatim as a fallback for misses) and write
   `golden/golden.snapped.json`. Coordinates in golden.json are
   knowledge-seeded approximations — snap before trusting reach tests.
4. **Implement the assembler** (`assemble/assemble.py`) per `SPEC.md` and
   `assemble/README.md`. Milestone: Devils Bridge assembles as ONE trail
   reaching within its `reach_tolerance_ft`. Iterate in the viewer.
5. **Golden harness:** `tools/run_golden.py` — cut an AOI around each
   golden trail, assemble, assert reach/length/fragment-count; print a
   pass/fail table. Wire a `make golden` target and surface results in
   the viewer dashboard.
6. **Scale:** continent → planet prefilter; measure; then incremental
   updates via replication diffs (see SPEC.md tooling track).

## Definition of done for the first milestone

`make golden` shows **devils-bridge: PASS** — one trail object named
"Devils Bridge Trail" whose geometry reaches within 150 ft of the arch,
with the staircase included — plus no regressions in the rest of the
suite that already passed.

## Working agreement

Commit early and often to `trailforge/**` on feature branches; PR to main
(pipeline-only changes have no CI gate — run `make test` locally). Keep
this file updated as the machine's runbook: real runtimes, disk sizes,
gotchas. The cloud sessions can't reach OSM/Geofabrik — anything needing
OSM data happens here.
