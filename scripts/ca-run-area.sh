#!/usr/bin/env bash
# One Canadian area through the GENERIC US-path pipeline (no --region, no --dk-* flags, no discovery
# validator, no relation-slice ledgers). Run by trailforge-publish-ca.yml once per selected area, from
# the repo root, after the province PBF was downloaded and prefiltered (plain prefilter.sh, no
# PREFILTER_DK_* env) to trailforge/data/hiking.osm.pbf.
#
#   aoi.sh (plain mode) -> ca_publish check-boundary-unique -> assemble.py (--only-area, exact relation
#   id when relation-backed) -> ca_trail_filter.py -> publish_areas.py (--state Canada, one-row index,
#   --no-boundary-fetch --no-elevation)
#
# Inputs (env): AREA_ID AREA_NAME BBOX (lon_min,lat_min,lon_max,lat_max), EXACTLY ONE of RELATION_ID or
#               WAY_ID, W (run work dir holding $AREA_ID/area.json and $AREA_ID/index.json from
#               ca_publish.py prepare-areas).
# Output: $W/$AREA_ID/{geom/<id>.json,index.json,trails.geojson,removed.geojson,assembly.json,ca-filter.json}
#
# The raw province PBF is trailforge/data/raw/canada-<prov>.osm.pbf (workflow download); this script only
# reads the prefiltered file, so no hard-coded Denmark path or label is involved.
set -euo pipefail
: "${AREA_ID:?}" "${AREA_NAME:?}" "${BBOX:?}" "${W:?}"
RELATION_ID="${RELATION_ID:-}"
WAY_ID="${WAY_ID:-}"
[[ "$AREA_ID" =~ ^[a-z0-9]+(-[a-z0-9]+)*-ca-(ab|bc|mb|nb|nl|ns|on|pe|qc|sk|yt)$ ]]
# Exactly one boundary id. A way-backed area has no assemble pin: check-boundary-unique is its guard.
if [ -n "$RELATION_ID" ] && [ -z "$WAY_ID" ]; then
  [[ "$RELATION_ID" =~ ^[1-9][0-9]*$ ]]
  BOUNDARY_ARGS=(--require-exact-area --expected-area-relation-id "$RELATION_ID")
elif [ -z "$RELATION_ID" ] && [ -n "$WAY_ID" ]; then
  [[ "$WAY_ID" =~ ^[1-9][0-9]*$ ]]
  BOUNDARY_ARGS=()
else
  echo "SAFETY BLOCK: exactly one of RELATION_ID and WAY_ID must be set" >&2
  exit 2
fi
[[ "$BBOX" =~ ^-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?$ ]]
A="$W/$AREA_ID"
test -f "$A/area.json" && test -f "$A/index.json"
jq -e --arg id "$AREA_ID" '.id == $id and .status == "ok" and (.cross_check == "registry-relation" or .cross_check == "registry-way")' \
  "$A/area.json" >/dev/null
mkdir -p "$A/geom"
ROOT="$(pwd)"

cd trailforge
HIKING=data/hiking.osm.pbf NAME="$AREA_ID" BBOX="$BBOX" bash extract/aoi.sh
python3 ../scripts/ca_publish.py check-boundary-unique --area-id "$AREA_ID" --pbf "data/aoi/$AREA_ID.osm.pbf"
python3 assemble/assemble.py --in "data/aoi/$AREA_ID.osm.pbf" \
  --out "data/aoi/$AREA_ID.trails.geojson" \
  --only-area "$AREA_NAME" \
  ${BOUNDARY_ARGS[@]+"${BOUNDARY_ARGS[@]}"} \
  --min-length-mi 0.1 --per-area-merge \
  --report-json "$A/assembly.json"
cp "data/aoi/$AREA_ID.removed.geojson" "$A/removed.geojson"
# Canadian vocabulary + registry exclusions/fixes; its drops are appended to $A/removed.geojson.
python3 ../scripts/ca_trail_filter.py --registry "$ROOT/trailforge/golden/ca-areas.json" --area-id "$AREA_ID" \
  --trails "data/aoi/$AREA_ID.trails.geojson" --removed "$A/removed.geojson" --report "$A/ca-filter.json"
# Publish with the OSM name in the one-row index: the generic publisher finds the boundary by that name.
python3 ../scripts/ca_publish.py index-for-publish --index "$A/index.json" --area-json "$A/area.json" \
  --out "$A/publish-index.json"
python3 serve/publish_areas.py \
  --trails "data/aoi/$AREA_ID.trails.geojson" \
  --hiking "data/aoi/$AREA_ID.osm.pbf" \
  --state Canada --index "$A/publish-index.json" \
  --out-dir "$A/geom" \
  --no-boundary-fetch --no-elevation
# publish_areas writes no geom for an area with no trail inside it ("no trails touch this area").
test -f "$A/geom/$AREA_ID.json" || { echo "SAFETY BLOCK: $AREA_ID has no publishable trails after the Canada filter" >&2; exit 3; }
python3 ../scripts/ca_publish.py finish-publish --index "$A/index.json" --publish-index "$A/publish-index.json" \
  --geom "$A/geom/$AREA_ID.json" --area-json "$A/area.json"

# The geom dir must hold exactly this area's file.
test "$(ls "$A/geom")" = "$AREA_ID.json"
cp "data/aoi/$AREA_ID.trails.geojson" "$A/trails.geojson"
# Drop the bulky per-area intermediates; reports and evidence stay.
rm -f "data/aoi/$AREA_ID.osm.pbf" "data/aoi/$AREA_ID.raw.geojson"
echo "area $AREA_ID canada pipeline done"
