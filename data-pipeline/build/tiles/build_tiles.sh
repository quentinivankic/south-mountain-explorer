#!/usr/bin/env bash
# build/tiles/build_tiles.sh — pack a region's normalized trail + area
# layers into a single .pmtiles archive (spec §7).
#
# Usage: build_tiles.sh <region> <trails.geojson> <areas.geojson> <out.pmtiles>
#
# Layers (spec §7.1):
#   trails — carries the §4.1 raw signals + §4.2 precomputed flags.
#            NO baked `confidence` score (§4, §7.1 — the device scores).
#   areas  — authority_rank, name, source_id, referential.
#
# curation_mode is read from config/regions.json:
#   shipped_filter (B, default) — tile ALL legally-shippable trails.
#   baked (A)                   — caller pre-filters trails.geojson to the
#                                 locked curation set before this runs.
# Either way, the ONLY exclusion upstream is the licensing gate.
set -euo pipefail

REGION="${1:?region}"; TRAILS="${2:?trails.geojson}"
AREAS="${3:?areas.geojson}"; OUT="${4:?out.pmtiles}"

for tool in tippecanoe; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "ERROR: '$tool' not found. Install tippecanoe (>=2.x) — it emits" >&2
    echo "       .pmtiles directly with -o foo.pmtiles. Not stubbed." >&2
    exit 3
  }
done

mkdir -p "$(dirname "$OUT")"

# tippecanoe writes .pmtiles natively (>=2.17). Attribution string is
# embedded in tile metadata; the per-region attributions.json (built by
# build/attributions.py) is the authoritative copy the app renders.
tippecanoe \
  --output="$OUT" \
  --force \
  --quiet \
  --name="trekdex-$REGION" \
  --attribution="© OpenStreetMap contributors" \
  --maximum-zoom=14 --minimum-zoom=5 \
  --drop-densest-as-needed \
  --no-tile-size-limit \
  -L"$(cat <<JSON
{"file":"$TRAILS","layer":"trails","description":"trail lines + raw scoring signals (no baked score)"}
JSON
)" \
  -L"$(cat <<JSON
{"file":"$AREAS","layer":"areas","description":"named-area polygons + authority_rank"}
JSON
)"

echo "wrote $OUT"
ls -lh "$OUT"
