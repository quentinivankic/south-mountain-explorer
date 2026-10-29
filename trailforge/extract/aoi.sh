#!/usr/bin/env bash
# trailforge AOI loop — cut a small bbox from the hiking subset and export
# GeoJSON for the viewer. This is the seconds-fast iteration cycle:
#   edit assembler -> make aoi -> reload viewer -> compare to OSM.
#
# Default AOI: Sedona (contains Devils Bridge, the founding regression).
set -euo pipefail

HIKING="${HIKING:-data/hiking.osm.pbf}"
NAME="${NAME:-sedona}"
# lon_min,lat_min,lon_max,lat_max
BBOX="${BBOX:--111.90,34.80,-111.70,34.98}"
AOI_RECEIPT="${AOI_RECEIPT:-}"
AOI_RELATION_MEMBERS="${AOI_RELATION_MEMBERS:-}"
AOI_WAY_TOPOLOGY="${AOI_WAY_TOPOLOGY:-}"
AOI_GITHUB_OUTPUT="${AOI_GITHUB_OUTPUT:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$SCRIPT_DIR/../tools"

PILOT_PROVENANCE=0
if [[ -n "$AOI_RECEIPT" || -n "$AOI_RELATION_MEMBERS" || \
      -n "$AOI_WAY_TOPOLOGY" || -n "$AOI_GITHUB_OUTPUT" ]]; then
  PILOT_PROVENANCE=1
fi

if (( PILOT_PROVENANCE )); then
  MISSING=()
  [[ -n "$AOI_RECEIPT" ]] || MISSING+=(AOI_RECEIPT)
  [[ -n "$AOI_RELATION_MEMBERS" ]] || MISSING+=(AOI_RELATION_MEMBERS)
  [[ -n "$AOI_WAY_TOPOLOGY" ]] || MISSING+=(AOI_WAY_TOPOLOGY)
  if (( ${#MISSING[@]} )); then
    echo "aoi: provenance mode requires AOI_RECEIPT, AOI_RELATION_MEMBERS, and AOI_WAY_TOPOLOGY (missing: ${MISSING[*]})" >&2
    exit 2
  fi
  if ! command -v osmium >/dev/null 2>&1; then
    echo "aoi: provenance mode requires osmium before extraction" >&2
    exit 2
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "aoi: provenance mode requires python3 before extraction" >&2
    exit 2
  fi
  for TOOL in export_relation_members.py export_way_topology.py build_pbf_receipt.py; do
    if [[ ! -f "$TOOLS_DIR/$TOOL" ]]; then
      echo "aoi: provenance mode requires $TOOLS_DIR/$TOOL before extraction" >&2
      exit 2
    fi
  done
  if ! python3 -c 'import osmium' >/dev/null 2>&1; then
    echo "aoi: provenance mode requires pyosmium before extraction" >&2
    exit 2
  fi
fi

OUT_DIR="data/aoi"
mkdir -p "$OUT_DIR"
AOI_PBF="$OUT_DIR/$NAME.osm.pbf"
RAW_GEOJSON="$OUT_DIR/$NAME.raw.geojson"

# --strategy=smart completes multipolygon/boundary relations that straddle
# the bbox, so area boundaries (e.g. a park relation) assemble into whole
# polygons for the trail↔area filter.
AOI_COMMAND=(
  osmium extract --strategy=smart --bbox "$BBOX" "$HIKING"
  -o "$AOI_PBF" --overwrite
)
"${AOI_COMMAND[@]}"

if (( PILOT_PROVENANCE )); then
  LEDGER_OUTPUTS=()
  if [[ -n "$AOI_GITHUB_OUTPUT" ]]; then
    LEDGER_OUTPUTS=(--github-output "$AOI_GITHUB_OUTPUT")
  fi

  # Discover selected hiking roots independently from the complete AOI topology.
  python3 "$TOOLS_DIR/export_relation_members.py" \
    --in "$AOI_PBF" \
    --scope aoi \
    --out "$AOI_RELATION_MEMBERS" \
    "${LEDGER_OUTPUTS[@]}"

  # Pilot raw evidence includes closed polygons. The ordinary AOI viewer path
  # intentionally retains its original linestring+point export below.
  osmium export "$AOI_PBF" \
    -f geojson --geometry-types=linestring,polygon,point \
    --add-unique-id=type_id \
    -o "$RAW_GEOJSON" --overwrite

  # Record every AOI way and relation directly from the PBF. The validator
  # independently restricts smart-completion authority to pinned area selectors.
  python3 "$TOOLS_DIR/export_way_topology.py" \
    --in "$AOI_PBF" \
    --source-artifact "${AOI_OUTPUT_ARTIFACT:-$AOI_PBF}" \
    --out "$AOI_WAY_TOPOLOGY" \
    "${LEDGER_OUTPUTS[@]}"

  # Seal the exact argv only after every pilot evidence export completes.
  AOI_COMMAND_JSON="$(python3 - "${AOI_COMMAND[@]}" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1:], separators=(",", ":")))
PY
)"
  RECEIPT_OUTPUTS=()
  if [[ -n "$AOI_GITHUB_OUTPUT" ]]; then
    RECEIPT_OUTPUTS=(--github-output "$AOI_GITHUB_OUTPUT")
  fi
  python3 "$TOOLS_DIR/build_pbf_receipt.py" \
    --kind scope \
    --scope aoi \
    --parent "$HIKING" \
    --parent-label "${AOI_PARENT_LABEL:-$HIKING}" \
    --output "$AOI_PBF" \
    --output-artifact "${AOI_OUTPUT_ARTIFACT:-$AOI_PBF}" \
    --upstream-stage aoi-extract \
    --command-json "$AOI_COMMAND_JSON" \
    --roots-from "$AOI_RELATION_MEMBERS" \
    --aoi-name "$NAME" \
    --aoi-bbox "$BBOX" \
    --receipt "$AOI_RECEIPT" \
    "${RECEIPT_OUTPUTS[@]}"
else
  # Preserve the original generic AOI behavior and dependency surface.
  osmium export "$AOI_PBF" \
    -f geojson --geometry-types=linestring,point \
    --add-unique-id=type_id \
    -o "$RAW_GEOJSON" --overwrite
fi

# Area boundaries are assembled from the AOI PBF directly by the assembler
# (libosmium area assembler via pyosmium) when --only-area is used — no
# separate polygon export (osmium export doesn't reliably emit them).

echo "aoi: $NAME ($BBOX)"
echo "  raw ways+POIs -> $RAW_GEOJSON (viewer layer: 'OSM raw')"
if (( PILOT_PROVENANCE )); then
  echo "  way/relation topology -> $AOI_WAY_TOPOLOGY (pilot source authority)"
fi
echo "  next: run the assembler to produce $OUT_DIR/$NAME.trails.geojson"
