#!/usr/bin/env bash
# trailforge prefilter — planet/extract PBF -> hiking-relevant subset.
#
# The 16 GB homelab strategy: NEVER load the planet into RAM or a DB.
# osmium tags-filter STREAMS the ~80 GB planet and emits only the objects
# we care about; the result (a few GB) is what every downstream step —
# assembly, AOI loops, golden verification — operates on.
#
# TAG SET v0 (SPEC.md refines after research). Differences vs Systems 1/2
# are deliberate and are THE fix for the Devils Bridge class of bug:
#   + highway=steps        (Devils Bridge's final staircase; Grouse Grind)
#   + highway=via_ferrata  (Half Dome cables / chained scrambles)
#   + highway=pedestrian   (trailhead plazas that connect trails)
#   + destination POI NODES (peaks, arches, waterfalls, viewpoints, huts,
#     passes, trailheads) — trail assembly needs to know where the payoff
#     is; Systems 1/2 never fetched these at all.
#
# osmium tags-filter includes objects REFERENCED by matches by default
# (nodes of matched ways, members of matched relations), so route
# relations bring their member ways along even when a member way wouldn't
# match the highway filter on its own.
set -euo pipefail

IN="${1:?input .osm.pbf (planet or extract)}"
OUT="${2:-data/hiking.osm.pbf}"
RECEIPT="${3:-}"
RECEIPT_GITHUB_OUTPUT="${RECEIPT_GITHUB_OUTPUT:-}"

if [[ -n "$RECEIPT_GITHUB_OUTPUT" && -z "$RECEIPT" ]]; then
  echo "prefilter: RECEIPT_GITHUB_OUTPUT requires a receipt path" >&2
  exit 2
fi

mkdir -p "$(dirname "$OUT")"

COMMAND=(
  osmium tags-filter "$IN"
  w/highway=path,footway,steps,track,bridleway,via_ferrata,pedestrian
  "w/abandoned:highway"
  r/route=hiking,foot,walking,running
  wr/boundary=protected_area,national_park
  wr/leisure=nature_reserve
  wr/landuse=forest
  # Preserve the exact legacy selectors and order below for non-Denmark runs.
  n/natural=peak,arch,saddle,cliff,rock,stone
  n/waterway=waterfall
  n/tourism=viewpoint,alpine_hut,wilderness_hut
  n/mountain_pass=yes
  n/highway=trailhead
  # Exact-Denmark raw-viewer classes are ignored by the legacy POI model.
  n/tourism=attraction
  n/historic=archaeological_site,castle,ruins
  n/amenity=shelter
  -o "$OUT" --overwrite
)
"${COMMAND[@]}"

if [[ -n "$RECEIPT" ]]; then
  COMMAND_JSON="$(python3 - "${COMMAND[@]}" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1:], separators=(",", ":")))
PY
)"
  RECEIPT_OUTPUTS=()
  if [[ -n "$RECEIPT_GITHUB_OUTPUT" ]]; then
    RECEIPT_OUTPUTS=(--github-output "$RECEIPT_GITHUB_OUTPUT")
  fi
  python3 "$(dirname "$0")/../tools/build_pbf_receipt.py" \
    --kind transformation \
    --scope prefiltered-denmark \
    --parent "$IN" \
    --parent-label "${RECEIPT_PARENT_LABEL:-$IN}" \
    --output "$OUT" \
    --output-artifact "${RECEIPT_OUTPUT_LABEL:-$OUT}" \
    --upstream-stage prefilter \
    --command-json "$COMMAND_JSON" \
    --receipt "$RECEIPT" \
    "${RECEIPT_OUTPUTS[@]}"
fi

osmium fileinfo -e "$OUT" | grep -E "Number of|Bounding" || true
echo "prefilter: $IN -> $OUT"
