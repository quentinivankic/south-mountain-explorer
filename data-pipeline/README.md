# Trekdex Trail & Area-Boundary Data Pipeline

Offline build pipeline that turns open trail + protected-area data into
per-region `.pmtiles` archives served from Cloudflare R2. Implements
`TRAIL_DATA_PIPELINE_SPEC.md` (the source of truth). This directory is
self-contained; it does not touch the iOS app build.

**Pilot region: New Zealand** (spec §10) — the best-provisioned country
(DOC tracks + PCL boundaries and LINZ, all CC-BY-4.0), used to prove the
pipeline end-to-end before generalizing to the rest of Wave 1.

## The two gates (do not conflate them — spec §0)

1. **Licensing gate** — build-time, **fail-closed**, non-negotiable. The
   *only* thing that removes a trail/area from the tiles. A source ships
   **iff** `commercial_ok == true AND redistribute_ok == true`. Anything
   else — `false`, `null` (unverified), or missing — is denied.
   Implemented once in [`sources/validate_registry.py`](sources/validate_registry.py);
   every build target depends on `make gate`.
2. **Curation gate** — developer-controlled, decides what the shipped app
   *draws and counts*, never what is *stored*. Under `shipped_filter` (B,
   default) ALL legally-shippable trails are tiled and the app applies a
   fixed curation config; under `baked` (A) the locked curation is applied
   at build time to shrink tiles.

### Non-negotiables baked into this pipeline
- Every legally-shippable trail ships to R2 with its raw scoring signals.
  **The confidence score never removes a trail from storage** — it is a
  developer authoring aid, computed on-device, never baked into tiles.
- **No confidence UI in the shipped app.** Scoring/weights/sliders live
  only in the authoring build (spec §8).
- **Never ship WDPA/Protected Planet geometry** (blocklist). Use WDPA IDs
  only as cross-reference keys via PAD-US/CDDA.
- **Never scrape** AllTrails/Trailforks/Komoot/Strava/YAMAP/Yamareco.
- **Never call Overpass at runtime** — Geofabrik extracts at build time.
- **Do not launch mainland China** (S&M law + GCJ-02 offset).

## Layout (spec §1)

```
sources/registry.json        SINGLE SOURCE OF TRUTH for provenance + licensing
sources/validate_registry.py the fail-closed gate (+ CLI)
sources/downloaders/         one idempotent script per source -> raw/
build/trails.sql             DuckDB: OSM trails -> Bucket A raw signals (§4.1)
build/areas.sql              DuckDB: OSM+Overture+authoritative area polygons (§4.3)
build/confidence.py          attach Bucket B precomputed flags ONLY (§4.2) — no score
build/scoring_reference.py   canonical on-device score (§4.3) — authoring aid + Swift-port spec
build/weights.default.json   default tunable weights (§4.3)
build/attributions.py        generate per-region attribution strings (§2, §8)
build/tiles/build_tiles.sh   tippecanoe -> .pmtiles (§7)
conflation/match.py          OSM<->authoritative buffer matching (§5.1) — needs shapely
conflation/flags.py          QA flag derivation: phantom/coverage-gap/mismatch (§5)
qa/assert_inclusion.py       post-build guard: risky trails survived, no baked score (§7.1)
config/regions.json          region -> countries/sources/curation_mode/trust (§6)
workers/pmtiles-handler/     Cloudflare Worker: PMTiles range requests from R2 (§7.4)
tests/                       pure-logic unit tests (48, no geo deps) — the policy core
Makefile                     download -> stage -> conflate -> build -> tile -> publish
```

## Tooling

Pure-logic core (the two gates, Bucket B flags, scoring, attribution, QA
guards) is **pure Python stdlib** — runs and is fully unit-tested with no
extra deps. The geo-heavy steps shell out to external tools:

| step        | tool                              |
|-------------|-----------------------------------|
| OSM extract | `osmium` + Geofabrik `.osm.pbf`   |
| stage       | `build/stage_osm.py` (pure stdlib) — or `duckdb` at planet scale |
| convert     | `ogr2ogr` (GDAL)                  |
| conflate    | Python + `shapely` (+ spatial idx)|
| tile        | `tippecanoe` (emits `.pmtiles`)   |
| publish     | `aws s3` → Cloudflare R2 (S3-compatible) |

`build/stage_osm.py` turns an `osmium export` GeoJSON directly into the
normalized trails/areas layers (Bucket A schema), so the pilot needs only
`osmium` + `tippecanoe` as untested external deps — the staging + all
policy logic is pure Python and unit-tested. `build/trails.sql` /
`build/areas.sql` (DuckDB) remain the documented path for planet scale.

## CI: one-click region build (dispatch-only)

`.github/workflows/build-region-tiles.yml` runs the whole pipeline for a
region in GitHub Actions and (optionally) publishes to R2. It is
**dispatch-only** (never auto-triggers), mirroring `build-trail-index.yml`.

- Inputs: `region` (default `new-zealand`), `publish` (default false),
  `include_linz` (default false).
- Always runs the fail-closed gate + the 58 unit tests first, then installs
  the geo toolchain and runs download→stage→conflate→build→tile.
- `publish=true` uploads `<region>.pmtiles` + `attributions.<region>.json`
  to the `trekdex-areas-dev` bucket (needs the `R2_*` repo secrets the
  `sync-geom-to-r2` workflow already uses). Build-only runs need no secrets
  and leave the `.pmtiles` as a downloadable Actions artifact.

`make doctor` reports what's installed. Each geo script fails loudly with
an install hint rather than silently degrading.

## Quick start

```bash
make doctor            # what's installed
make test              # 48 pure-logic tests — run anywhere, no geo deps
make gate REGION=new-zealand      # fail-closed licensing gate
make region REGION=new-zealand    # full pipeline (needs the geo tools above)
```

### What runs today without the geo stack
The **policy-critical spine** is fully runnable and tested here:

```bash
# licensing gate
python3 sources/validate_registry.py                       # table of all sources
python3 sources/validate_registry.py --require osm nz_doc nz_linz

# Bucket B flags -> inclusion guard -> live score, on a synthetic sample
python3 build/confidence.py --trails tests/fixtures/nz_sample_trails.geojson \
  --matches staging/nz_matches.json --region-trust high --out staging/flagged.geojson
python3 qa/assert_inclusion.py --trails staging/flagged.geojson
python3 build/scoring_reference.py < staging/flagged.geojson

# per-region attribution strings
python3 build/attributions.py --region new-zealand \
  --sources osm nz_doc nz_linz --countries NZ
```

## New Zealand build (spec §10), once geo tools are present

```bash
# 1. download raw (Geofabrik OSM + DOC tracks/PCL; LINZ optional w/ key)
make download REGION=new-zealand
#    LINZ needs: export LINZ_API_KEY=...

# 2. stage: osmium tags-filter the PBF to trail ways + area polygons,
#    ogr2ogr each to GeoParquet in staging/ (see Makefile `stage`)

# 3. normalize (DuckDB): build/trails.sql -> Bucket A signals
#    build/areas.sql -> area polygons + authority_rank

# 4. conflate OSM vs DOC tracks + PCL boundaries (shapely)
make conflate REGION=new-zealand

# 5. attach Bucket B flags + inclusion guard
make build REGION=new-zealand

# 6. tile to .pmtiles + generate attributions.json
make tile REGION=new-zealand

# 7. publish to the R2 dev bucket, deploy the Worker
make publish REGION=new-zealand
cd workers/pmtiles-handler && wrangler deploy
```

## VERIFY-before-build (spec §2, §10)

Every source with `verify_before_build: true` must have its live license +
access re-confirmed before its data ships. Licenses have changed before
(IGN went open 2021; OS→OGL 2015). For the NZ pilot specifically:
- DOC tracks/PCL layer URLs (ArcGIS endpoints drift — overridable via
  `DOC_TRACKS_URL` / `DOC_PCL_URL`).
- The exact LINZ layer id + that it is CC-BY-4.0.
- DOC/LINZ attribution wording (already fixed in `registry.json`).

## Status (pilot session)

Done: repo scaffold + Makefile; `registry.json` + fail-closed validator;
OSM/DOC/LINZ downloaders; pure-Python OSM stager + DuckDB SQL; conflation
matcher + QA flag logic; thin Bucket-B `confidence.py` (flags only, no
score); on-device scoring reference + default weights; attribution
generator; post-build inclusion guard; tippecanoe→pmtiles tiling; R2
Workers PMTiles handler; **58 passing unit tests** over the policy core;
end-to-end spine demonstrated on a synthetic NZ sample; **dispatch-only
CI workflow** that runs the full pipeline + publishes to R2.

Next: dispatch `build-region-tiles.yml` (region=new-zealand) — build-only
first to confirm the geo steps, then `publish=true` once the `R2_*`
secrets are set and the DOC/LINZ endpoints are VERIFY'd. Then wire the
authoring-build on-device scorer + weight sliders in the iOS app (port
`scoring_reference.py`), validate on-device point-in-polygon area
attribution against the shipped DOC/LINZ polygons, and generalize to the
rest of Wave 1.

> Not legal advice — flag ODbL share-alike + per-country terms for real
> legal review before any public release (spec §9).
