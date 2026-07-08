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

## What's already here — the assembler is WRITTEN and unit-tested

The cloud session built and tested the whole pipeline against synthetic
fixtures (it just can't reach OSM's servers). **Your job is to RUN it on
real data and iterate**, not to write it from scratch.

| Path | What | State |
|---|---|---|
| `SPEC.md` | Extraction+assembly spec (tag set, algorithm, tools) | done |
| `assemble/model.py` | The core algorithm: relations-first → name-stitch → spur-attach. Pure Python, **11 unit tests incl. a synthetic Devils Bridge that reaches the arch** | done, tested |
| `assemble/assemble.py` | pyosmium reader: `.osm.pbf` → assembled trails GeoJSON. Smoke-tested on a synthetic Devils-Bridge `.osm` | done |
| `tools/golden_eval.py` | Pure pass/fail scoring (reach / length / fragmentation), 6 unit tests | done, tested |
| `tools/run_golden.py` | Orchestrates the suite: bbox-cut each golden trail → assemble → evaluate → table + merged GeoJSON for the viewer | done (runs on the box) |
| `tools/verify_golden.py` | Structural validation + **`--snap`** (implemented: matches POIs in the subset to snap golden coords) | done |
| `golden/golden.json` | 20-trail regression suite, each encoding a failure mode | done |
| `extract/prefilter.sh` | Streaming tags-filter, tag set incl. steps/via_ferrata + POI nodes | done |
| `extract/aoi.sh` | Sedona-default bbox cutter | done |
| `viewer/` | MapLibre QA viewer: OSM raw vs assembled trails vs golden markers | done |
| `Makefile` | `setup·download-extract·prefilter·aoi·assemble·golden·verify-golden·qa·test` | done |

Everything above passes `make test` in the sandbox. What only the box can
do: run against **real** OSM (the synthetic fixtures prove the logic; real
data proves coverage + tuning).

Also relevant, read-only: `data-pipeline/build/route_index.py` — a working
two-pass pyosmium pattern that resolves route **superrelations**
transitively (the Te Araroa fix). Reuse the approach in the assembler.

## Your steps (mostly RUN, then tune)

1. **Bootstrap:** `make setup && make download-extract && make prefilter`
   (north-america first — planet later once the loop works). **Record the
   subset size + prefilter runtime in this file** — that validates the
   ≤16 GB streaming thesis (SPEC.md §4/§7 Q1).
2. **Prove Devils Bridge on real data:**
   `make aoi && make assemble && make qa` → open
   `http://localhost:8000/viewer/?aoi=sedona`. Check the assembled
   "Devils Bridge Trail" (orange) now reaches the arch — the founding
   fix, on real OSM this time.
3. **Snap the golden coords:** `make verify-golden SNAP=1` → writes
   `golden/golden.snapped.json` (seeded coords → real OSM POIs). Eyeball a
   few snaps in the viewer.
4. **Run the suite:** `make golden`. Read the pass/fail table. Devils
   Bridge should PASS. For failures, open that trail in the viewer and use
   the click-to-inspect welds to see what assembled vs what OSM has.
5. **Tune** `assemble/model.py` against real failures — spur-attach
   thresholds (`SPUR_MAX_MI`, `SPUR_POI_REACH_FT`), POI set, name
   normalization. `make test` guards the synthetic cases; `make golden`
   measures the real ones. Commit each improvement.
6. **Scale:** continent → planet prefilter; measure; then incremental
   updates via replication diffs (SPEC.md §4). Compare coverage vs
   System 2 on a shared country (SPEC.md §7 Q4).

## Definition of done for the first milestone

`make golden` shows **devils-bridge: PASS** on real OSM — one trail named
"Devils Bridge Trail" reaching within 150 ft of the arch, staircase
included — with the rest of the suite as a baseline to push up. (The
synthetic version of this already passes in `make test`.)

## Working agreement

Commit early and often to `trailforge/**` on feature branches; PR to main
(pipeline-only changes have no CI gate — run `make test` locally). Keep
this file updated as the machine's runbook: real runtimes, disk sizes,
gotchas. The cloud sessions can't reach OSM/Geofabrik — anything needing
OSM data happens here.
