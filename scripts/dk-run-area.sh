#!/usr/bin/env bash
# One Danish area through the reviewed exact pipeline (the mols_simple steps of
# trailforge-publish.yml, parametrized by registry values). Run by
# trailforge-publish-dk.yml once per selected area, from the repo root, after the
# Denmark PBF was downloaded + prefiltered and discovery was run once.
#
# Inputs (env): AREA_ID AREA_NAME BBOX (lon_min,lat_min,lon_max,lat_max) and EXACTLY ONE of
#               RELATION_ID (relation-backed boundary) or WAY_ID (way-backed boundary: a closed
#               OSM way, Denmark only; the other variable must be unset or empty)
#               W (run work dir holding discovery-report.json, pbf-provenance.json)
# Output: $W/$AREA_ID/{geom/<id>.json,index.json,trails.geojson,removed.geojson,assembly.json}
#
# The relation-ledger artifact LABELS below are the literal Mols labels on
# purpose: export_relation_members.py and assemble.py validate them as fixed
# strings (they are labels, never opened as paths). Each area runs in its own
# $W/$AREA_ID directory, so reusing the labels cannot mix evidence.
set -euo pipefail
: "${AREA_ID:?}" "${AREA_NAME:?}" "${BBOX:?}" "${W:?}"
RELATION_ID="${RELATION_ID:-}"
WAY_ID="${WAY_ID:-}"
[[ "$AREA_ID" =~ ^[a-z0-9]+(-[a-z0-9]+)*-dk$ ]]
# Exactly one boundary id: a relation id or a way id, never both, never neither.
if [ -n "$RELATION_ID" ] && [ -z "$WAY_ID" ]; then
  [[ "$RELATION_ID" =~ ^[1-9][0-9]*$ ]]
  BOUNDARY_ARGS=(--expected-area-relation-id "$RELATION_ID")
elif [ -z "$RELATION_ID" ] && [ -n "$WAY_ID" ]; then
  [[ "$WAY_ID" =~ ^[1-9][0-9]*$ ]]
  BOUNDARY_ARGS=(--expected-area-way-id "$WAY_ID")
else
  echo "SAFETY BLOCK: exactly one of RELATION_ID and WAY_ID must be set" >&2
  exit 2
fi
[[ "$BBOX" =~ ^-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?$ ]]
# Rule set comes from the registry entry (Mols = legacy, dk-v2 or dk-v3 elsewhere).
RULES="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .rules // "legacy"' \
  trailforge/golden/dk-areas.json)"
[[ "$RULES" =~ ^(legacy|dk-v2|dk-v3)$ ]]
# Optional curated exclusions (registry exclude_trails; validated by dk_publish and assemble).
EXCLUDE_JSON="$(jq -c --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .exclude_trails // []' \
  trailforge/golden/dk-areas.json)"
# Optional curated inclusions (registry include_trails; dk-v3 only, validated by dk_publish and assemble).
INCLUDE_JSON="$(jq -c --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .include_trails // []' \
  trailforge/golden/dk-areas.json)"
# Optional per-relation wide-gap acceptance (registry accept_gap; dk-v3 only, validated by dk_publish and
# assemble) and forward/backward member roles (registry accept_directional_roles; dk-v3 only).
ACCEPT_GAP_JSON="$(jq -c --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .accept_gap // []' \
  trailforge/golden/dk-areas.json)"
DIRECTIONAL_ROLES="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .accept_directional_roles // empty' \
  trailforge/golden/dk-areas.json)"
# Optional boundary-by-id opt-ins (registry boundary_by_id / container_by_id; dk-v3 only, validated by
# dk_publish and assemble): pick the polygon by the registry's relation/way id instead of by name alone.
BOUNDARY_BY_ID="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .boundary_by_id // empty' \
  trailforge/golden/dk-areas.json)"
CONTAINER_BY_ID="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .container_by_id // empty | .reason' \
  trailforge/golden/dk-areas.json)"
# Optional route-cluster container (registry container_from_routes; dk-v3 only): the container is derived
# from the include_trails routes for an area with no usable OSM polygon. Absent = no flag (byte-identical run).
CONTAINER_FROM_ROUTES="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .container_from_routes // empty | .reason' \
  trailforge/golden/dk-areas.json)"
# Optional display prefix for colour/number-only trail names (registry trail_name_prefix; dk-v3 only,
# validated by dk_publish and assemble). Empty/absent = no prefix and no flag (byte-identical run).
NAME_PREFIX="$(jq -r --arg id "$AREA_ID" '.areas[] | select(.id == $id) | .trail_name_prefix // empty' \
  trailforge/golden/dk-areas.json)"
A="$W/$AREA_ID"
mkdir -p "$A/geom"
EXCLUDE_ARGS=()
if [ "$EXCLUDE_JSON" != "[]" ]; then
  printf '%s\n' "$EXCLUDE_JSON" > "$A/dk-curated-exclude.json"
  EXCLUDE_ARGS=(--dk-curated-exclude "$A/dk-curated-exclude.json")
fi
if [ "$INCLUDE_JSON" != "[]" ]; then
  printf '%s\n' "$INCLUDE_JSON" > "$A/dk-curated-include.json"
  EXCLUDE_ARGS+=(--dk-curated-include "$A/dk-curated-include.json")
fi
if [ "$ACCEPT_GAP_JSON" != "[]" ]; then
  printf '%s\n' "$ACCEPT_GAP_JSON" > "$A/dk-accept-gap.json"
  EXCLUDE_ARGS+=(--dk-accept-gap "$A/dk-accept-gap.json")
fi
if [ "$DIRECTIONAL_ROLES" = "true" ]; then
  EXCLUDE_ARGS+=(--dk-accept-directional-roles)
fi
if [ "$BOUNDARY_BY_ID" = "true" ]; then
  EXCLUDE_ARGS+=(--dk-boundary-by-id)
fi
if [ -n "$CONTAINER_BY_ID" ]; then
  EXCLUDE_ARGS+=(--dk-container-by-id)
fi
if [ -n "$CONTAINER_FROM_ROUTES" ]; then
  EXCLUDE_ARGS+=(--dk-container-from-routes)
fi
if [ -n "$NAME_PREFIX" ]; then
  EXCLUDE_ARGS+=(--dk-name-prefix "$NAME_PREFIX")
fi
ROOT="$(pwd)"
SOURCE_PBF="$ROOT/trailforge/data/raw/denmark.osm.pbf"

# Discovery lists protected areas only. An area with a discovery candidate is validated
# exactly as before. An area without one (forest, plantation, heath, island) must have
# passed the registry-relation cross-check in prepare-areas (area.json cross_check);
# anything else fails closed here. A way-backed area never takes the discovery branch
# (discovery lists relations): prepare-areas must have cross-checked its own PBF way.
if [ -z "$WAY_ID" ] && jq -e --arg id "$AREA_ID" '[.candidates[]? | select(.index_row[0] == $id)] | length > 0' \
    "$W/discovery-report.json" >/dev/null; then
  python3 trailforge/tools/validate_publish_pilot.py discovery \
    --discovery-report "$W/discovery-report.json" \
    --pbf-provenance "$W/pbf-provenance.json" \
    --source-pbf "$SOURCE_PBF" \
    --expected-osm-relation-id "$RELATION_ID" \
    --expected-area-id "$AREA_ID" \
    --expected-area-name "$AREA_NAME" \
    --selected-record-out "$A/selected.json" \
    --index-out "$A/discovery-index.json" \
    --result-json "$A/discovery-validation.json"
else
  if [ -n "$WAY_ID" ]; then
    jq -e --arg id "$AREA_ID" --argjson way "$WAY_ID" \
      '.id == $id and .status == "ok" and .cross_check == "registry-way" and .way_id == $way' \
      "$A/area.json" >/dev/null
    printf '{"stage":"discovery","status":"skipped","reason":"way-backed area; registry-way cross-check passed in prepare-areas"}\n' \
      > "$A/discovery-validation.json"
  else
    jq -e --arg id "$AREA_ID" '.id == $id and .status == "ok" and .cross_check == "registry-relation"' \
      "$A/area.json" >/dev/null
    printf '{"stage":"discovery","status":"skipped","reason":"no discovery candidate; registry-relation cross-check passed in prepare-areas"}\n' \
      > "$A/discovery-validation.json"
  fi
fi

cd trailforge
HIKING=data/hiking.osm.pbf NAME="$AREA_ID" BBOX="$BBOX" \
AOI_RECEIPT="$A/scope.aoi.json" \
AOI_RELATION_MEMBERS="$A/relation-members.aoi.json" \
AOI_WAY_TOPOLOGY="data/aoi/$AREA_ID.way-topology.json" \
AOI_GITHUB_OUTPUT="$A/aoi-outputs.txt" \
AOI_PARENT_LABEL=data/hiking.osm.pbf \
AOI_OUTPUT_ARTIFACT=data/aoi/mols-bjerge.osm.pbf \
  bash extract/aoi.sh
python3 tools/build_relation_slice.py --in data/raw/denmark.osm.pbf \
  --roots-from "$A/relation-members.aoi.json" --out "$A/raw.osm.pbf" \
  --receipt "$A/scope.raw.json" --scope raw-denmark \
  --source-label data/raw/denmark.osm.pbf \
  --output-artifact data/source/mols-bjerge.raw.osm.pbf \
  --upstream-stage relation-root-closure-extract \
  --github-output "$A/raw-outputs.txt"
python3 tools/export_relation_members.py --in "$A/raw.osm.pbf" \
  --scope raw-denmark --roots-from "$A/relation-members.aoi.json" \
  --out "$A/relation-members.raw.json" --github-output "$A/raw-outputs.txt"
python3 tools/build_relation_slice.py --in data/hiking.osm.pbf \
  --roots-from "$A/relation-members.aoi.json" --out "$A/prefilter.osm.pbf" \
  --receipt "$A/scope.prefilter.json" --scope prefiltered-denmark \
  --source-label data/hiking.osm.pbf \
  --output-artifact data/source/mols-bjerge.prefilter.osm.pbf \
  --upstream-stage relation-root-closure-extract \
  --github-output "$A/prefilter-outputs.txt"
python3 tools/export_relation_members.py --in "$A/prefilter.osm.pbf" \
  --scope prefiltered-denmark --roots-from "$A/relation-members.aoi.json" \
  --out "$A/relation-members.prefilter.json" \
  --github-output "$A/prefilter-outputs.txt"
python3 assemble/assemble.py --in "data/aoi/$AREA_ID.osm.pbf" \
  --out "data/aoi/$AREA_ID.trails.geojson" \
  --only-area "$AREA_NAME" --require-exact-area \
  "${BOUNDARY_ARGS[@]}" \
  --relation-authority "$A/relation-members.raw.json" \
  --prefilter-authority "$A/relation-members.prefilter.json" \
  --aoi-relation-members "$A/relation-members.aoi.json" \
  --min-length-mi 0.1 --per-area-merge --region dk --dk-rules "$RULES" \
  ${EXCLUDE_ARGS[@]+"${EXCLUDE_ARGS[@]}"} \
  --report-json "$A/assembly.json"

# Exact park polygon + exact assembly, no elevation, one --only-area per call.
python3 serve/publish_areas.py \
  --trails "data/aoi/$AREA_ID.trails.geojson" \
  --hiking "data/aoi/$AREA_ID.osm.pbf" \
  --state Denmark --index "$A/index.json" \
  --out-dir "$A/geom" --only-area "$AREA_ID" \
  --no-boundary-fetch --no-elevation \
  --exact-boundary "data/aoi/$AREA_ID.exact-area.geojson"

# The geom dir must hold exactly this area's file.
test "$(ls "$A/geom")" = "$AREA_ID.json"
cp "data/aoi/$AREA_ID.trails.geojson" "$A/trails.geojson"
cp "data/aoi/$AREA_ID.removed.geojson" "$A/removed.geojson"
# Drop the bulky per-area intermediates; reports and evidence stay.
rm -f "$A/raw.osm.pbf" "$A/prefilter.osm.pbf" "data/aoi/$AREA_ID.osm.pbf" \
  "data/aoi/$AREA_ID.raw.geojson"
echo "area $AREA_ID exact pipeline done"
