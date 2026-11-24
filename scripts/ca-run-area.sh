#!/usr/bin/env bash
# One Canadian area through the GENERIC US-path pipeline (no --region, no --dk-* flags, no discovery
# validator, no relation-slice ledgers). Run by trailforge-publish-ca.yml once per selected area, from
# the repo root, after the province PBF was downloaded and prefiltered (plain prefilter.sh, no
# PREFILTER_DK_* env) to trailforge/data/hiking.osm.pbf.
#
#   aoi.sh (plain mode) -> ca_publish check-boundary-unique -> assemble.py (--only-area, exact relation
#   id when relation-backed) -> ca_trail_filter.py -> publish_areas.py (--state Canada, one-row index,
#   --no-boundary-fetch; DEM elevation is sampled inline, --no-elevation is refused)
#
# Inputs (env): AREA_ID AREA_NAME BBOX (lon_min,lat_min,lon_max,lat_max), EXACTLY ONE of RELATION_ID or
#               WAY_ID, W (run work dir holding $AREA_ID/area.json and $AREA_ID/index.json from
#               ca_publish.py prepare-areas).
# Output: $W/$AREA_ID/{geom/<id>.json,index.json,trails.geojson,removed.geojson,assembly.json,ca-filter.json}
#
# The raw province PBF is trailforge/data/raw/canada-<prov>.osm.pbf (workflow download); this script only
# reads the prefiltered file, so no hard-coded Denmark path or label is involved.
set -euo pipefail
ROOT_REGISTRY="$(pwd)/trailforge/golden/ca-areas.json"
: "${AREA_ID:?}" "${AREA_NAME:?}" "${BBOX:?}" "${W:?}"
RELATION_ID="${RELATION_ID:-}"
WAY_ID="${WAY_ID:-}"
[[ "$AREA_ID" =~ ^[a-z0-9]+(-[a-z0-9]+)*-ca-(ab|bc|mb|nb|nl|ns|on|pe|qc|sk|yt|nt|nu)$ ]]
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
# Optional id-pinned boundary (registry boundary_by_id: true; relation-backed only). Absent = no flag and
# no --region, so every other area runs exactly as before. Validated by ca_publish.py and assemble.py.
BOUNDARY_BY_ID="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | if has("boundary_by_id") then (.boundary_by_id | tostring) else empty end' \
  "$ROOT_REGISTRY")"
if [ -n "$BOUNDARY_BY_ID" ]; then
  if [ "$BOUNDARY_BY_ID" != "true" ] || [ -z "$RELATION_ID" ]; then
    echo "SAFETY BLOCK: boundary_by_id must be true on a relation-backed area" >&2
    exit 2
  fi
  BOUNDARY_ARGS+=(--region ca --boundary-by-id)
fi
[[ "$BBOX" =~ ^-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?$ ]]
A="$W/$AREA_ID"
test -f "$A/area.json" && test -f "$A/index.json"
# Optional per-relation engine opt-ins (registry accept_gap / keep_superroute; relation-backed only). Absent
# = no flag, so every other area runs exactly as before. `--region ca` is added once, only when one is set.
# Entries are validated by ca_publish.py (load_registry) and again by assemble.py; each fails closed there.
CA_RULE_FLAGS=()
for RULE_KEY in accept_gap keep_superroute; do
  RULE_COUNT="$(jq -r --arg id "$AREA_ID" --arg k "$RULE_KEY" '.areas[] | select(.id == $id) | if has($k) then (.[$k] | length | tostring) else empty end' \
    "$ROOT_REGISTRY")"
  if [ -n "$RULE_COUNT" ]; then
    if [ "$RULE_COUNT" = "0" ] || [ -z "$RELATION_ID" ]; then
      echo "SAFETY BLOCK: $RULE_KEY must be a non-empty list on a relation-backed area" >&2
      exit 2
    fi
    jq -c --arg id "$AREA_ID" --arg k "$RULE_KEY" '.areas[] | select(.id == $id) | .[$k]' "$ROOT_REGISTRY" > "$A/ca-$RULE_KEY.json"
    CA_RULE_FLAGS+=("--ca-${RULE_KEY//_/-}" "$A/ca-$RULE_KEY.json")
  fi
done
# Optional ring-way dedupe (registry dedupe_ring_way: true; relation-backed only): a same-name outer ring
# way of the area's own relation is not a second boundary. Absent = no flag.
DEDUPE_RING_WAY="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | if has("dedupe_ring_way") then (.dedupe_ring_way | tostring) else empty end' \
  "$ROOT_REGISTRY")"
if [ -n "$DEDUPE_RING_WAY" ]; then
  if [ "$DEDUPE_RING_WAY" != "true" ] || [ -z "$RELATION_ID" ] || [ -n "$BOUNDARY_BY_ID" ]; then
    echo "SAFETY BLOCK: dedupe_ring_way must be true on a relation-backed area without boundary_by_id" >&2
    exit 2
  fi
  CA_RULE_FLAGS+=("--ca-dedupe-ring-way")
fi
if [ "${#CA_RULE_FLAGS[@]}" -gt 0 ] && [ -z "$BOUNDARY_BY_ID" ]; then
  BOUNDARY_ARGS+=(--region ca)
fi
jq -e --arg id "$AREA_ID" '.id == $id and .status == "ok" and (.cross_check == "registry-relation" or .cross_check == "registry-way")' \
  "$A/area.json" >/dev/null
mkdir -p "$A/geom"
ROOT="$(pwd)"

cd trailforge
HIKING=data/hiking.osm.pbf NAME="$AREA_ID" BBOX="$BBOX" bash extract/aoi.sh
# Optional park_boundary (registry; relation or way): re-add the area's own leisure=park boundary (the
# prefilter drops it) from the raw province PBF into the AOI PBF. Absent = this step does not run.
PARK_BOUNDARY="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | if has("park_boundary") then "yes" else empty end' \
  "$ROOT_REGISTRY")"
if [ -n "$PARK_BOUNDARY" ]; then
  python3 ../scripts/ca_park_boundary.py --registry "$ROOT/trailforge/golden/ca-areas.json" --area-id "$AREA_ID" \
    --raw-pbf "data/raw/canada-${AREA_ID##*-}.osm.pbf" --aoi-pbf "data/aoi/$AREA_ID.osm.pbf" \
    --report "$A/ca-park-boundary.json"
fi
python3 ../scripts/ca_publish.py check-boundary-unique --area-id "$AREA_ID" --pbf "data/aoi/$AREA_ID.osm.pbf"
python3 assemble/assemble.py --in "data/aoi/$AREA_ID.osm.pbf" \
  --out "data/aoi/$AREA_ID.trails.geojson" \
  --only-area "$AREA_NAME" \
  ${BOUNDARY_ARGS[@]+"${BOUNDARY_ARGS[@]}"} \
  ${CA_RULE_FLAGS[@]+"${CA_RULE_FLAGS[@]}"} \
  --min-length-mi 0.1 --per-area-merge \
  --report-json "$A/assembly.json"
cp "data/aoi/$AREA_ID.removed.geojson" "$A/removed.geojson"
# Optional adopt_ways (registry; relation-backed only): name unnamed OSM ways as one agency-listed trail,
# appended to the assembled trails before the filter. Absent = this step does not run.
ADOPT_COUNT="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | if has("adopt_ways") then (.adopt_ways | length | tostring) else empty end' \
  "$ROOT_REGISTRY")"
if [ -n "$ADOPT_COUNT" ]; then
  if [ "$ADOPT_COUNT" = "0" ] || [ -z "$RELATION_ID" ]; then
    echo "SAFETY BLOCK: adopt_ways must be a non-empty list on a relation-backed area" >&2
    exit 2
  fi
  # An entry with `relax` may name a service/unclassified way the hiking prefilter dropped: pass the raw province
  # PBF then (only the relax-listed ids missing from the area extract are read from it). Else the argv is unchanged.
  ADOPT_RAW_ARGS=()
  if [ "$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | [.adopt_ways[] | has("relax")] | any' "$ROOT_REGISTRY")" = "true" ]; then
    ADOPT_RAW_ARGS=(--raw-pbf "data/raw/canada-${AREA_ID##*-}.osm.pbf")
  fi
  python3 ../scripts/ca_adopt_ways.py --registry "$ROOT/trailforge/golden/ca-areas.json" --area-id "$AREA_ID" \
    --pbf "data/aoi/$AREA_ID.osm.pbf" --trails "data/aoi/$AREA_ID.trails.geojson" --report "$A/ca-adopt.json" \
    ${ADOPT_RAW_ARGS[@]+"${ADOPT_RAW_ARGS[@]}"}
fi
# Optional hiking_tag_wins (registry; relation-backed only): keep hiking ways the ingest filter drops for a
# skitour/nordic piste tag, appended to the assembled trails before the filter. Absent = this step does not run.
HTW_COUNT="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | if has("hiking_tag_wins") then (.hiking_tag_wins | length | tostring) else empty end' \
  "$ROOT_REGISTRY")"
if [ -n "$HTW_COUNT" ]; then
  if [ "$HTW_COUNT" = "0" ] || [ -z "$RELATION_ID" ]; then
    echo "SAFETY BLOCK: hiking_tag_wins must be a non-empty list on a relation-backed area" >&2
    exit 2
  fi
  python3 ../scripts/ca_hiking_tag_wins.py --registry "$ROOT/trailforge/golden/ca-areas.json" --area-id "$AREA_ID" \
    --pbf "data/aoi/$AREA_ID.osm.pbf" --trails "data/aoi/$AREA_ID.trails.geojson" --report "$A/ca-hiking-tag-wins.json"
fi
# Optional way_name_overrides (registry; relation-backed only; each entry carries an agency evidence URL): name
# specific ways with the signed trail name, for hikes that ride ways OSM names after a long-distance or winter
# route. Appended to the assembled trails before the filter. Absent = this step does not run.
WNO_COUNT="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | if has("way_name_overrides") then (.way_name_overrides | length | tostring) else empty end' \
  "$ROOT_REGISTRY")"
if [ -n "$WNO_COUNT" ]; then
  if [ "$WNO_COUNT" = "0" ] || [ -z "$RELATION_ID" ]; then
    echo "SAFETY BLOCK: way_name_overrides must be a non-empty list on a relation-backed area" >&2
    exit 2
  fi
  python3 ../scripts/ca_way_name_override.py --registry "$ROOT/trailforge/golden/ca-areas.json" --area-id "$AREA_ID" \
    --pbf "data/aoi/$AREA_ID.osm.pbf" --trails "data/aoi/$AREA_ID.trails.geojson" --report "$A/ca-way-name-override.json"
fi
# Optional leg_merges (registry; relation-backed only): merge the OSM legs/pieces of one agency trail into ONE
# named trail (whole route only: a gap wider than the entry's tolerance fails closed). Legs are replaced by the
# merged trail before the filter. Absent = this step does not run.
LM_COUNT="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | if has("leg_merges") then (.leg_merges | length | tostring) else empty end' \
  "$ROOT_REGISTRY")"
if [ -n "$LM_COUNT" ]; then
  if [ "$LM_COUNT" = "0" ] || [ -z "$RELATION_ID" ]; then
    echo "SAFETY BLOCK: leg_merges must be a non-empty list on a relation-backed area" >&2
    exit 2
  fi
  python3 ../scripts/ca_leg_merge.py --registry "$ROOT/trailforge/golden/ca-areas.json" --area-id "$AREA_ID" \
    --trails "data/aoi/$AREA_ID.trails.geojson" --removed "$A/removed.geojson" --report "$A/ca-leg-merge.json"
fi
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
  --no-boundary-fetch
# publish_areas writes no geom for an area with no trail inside it ("no trails touch this area").
test -f "$A/geom/$AREA_ID.json" || { echo "SAFETY BLOCK: $AREA_ID has no publishable trails after the Canada filter" >&2; exit 3; }
# --registry applies the area's optional trail_id_pins opt-in (keep a live trail id); absent = no change.
python3 ../scripts/ca_publish.py finish-publish --index "$A/index.json" --publish-index "$A/publish-index.json" \
  --geom "$A/geom/$AREA_ID.json" --area-json "$A/area.json" --registry "$ROOT/trailforge/golden/ca-areas.json"

# The geom dir must hold exactly this area's file.
test "$(ls "$A/geom")" = "$AREA_ID.json"
cp "data/aoi/$AREA_ID.trails.geojson" "$A/trails.geojson"
# Drop the bulky per-area intermediates; reports and evidence stay.
rm -f "data/aoi/$AREA_ID.osm.pbf" "data/aoi/$AREA_ID.raw.geojson"
echo "area $AREA_ID canada pipeline done"
