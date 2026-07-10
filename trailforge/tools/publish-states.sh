#!/usr/bin/env bash
# Whole-US publish loop for trailforge (System 3).
#
# For each US state: download the Geofabrik extract -> prefilter to the hiking
# subset -> delete the raw extract (disk) -> assemble with connectivity merge
# (--per-area-merge) -> publish_areas into public/areas/geom for the areas
# already seeded in the app index. Continues past a failing state and prints a
# summary at the end. Does NOT git commit/push — that's a single reviewed step
# afterward so one R2 sync covers the whole batch.
#
# Usage (run from the trailforge/ dir):
#   tools/publish-states.sh                 # all 50 states + DC
#   tools/publish-states.sh arizona utah    # a subset (verification run)
#   DRYRUN=1 tools/publish-states.sh utah   # assemble + publish --dry-run (no writes)
#   RAW_DIR=/mnt/raid/trekdex/raw tools/publish-states.sh   # stage extracts on the RAID
#
# Env:
#   RAW_DIR   where to download extracts (default data/raw). Point at the RAID
#             if the system disk is tight; each extract is deleted after
#             prefilter regardless, so only one lives at a time.
#   MINLEN    min trail length mi (default 0.1)
#   DRYRUN    if set, publish_areas runs with --dry-run (no geom/index writes)
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1          # -> trailforge/
REPO_ROOT="$(cd .. && pwd)"
DATA=data
RAW_DIR="${RAW_DIR:-$DATA/raw}"
OUT_DIR="$REPO_ROOT/public/areas/geom"
MINLEN="${MINLEN:-0.1}"
GEOFABRIK="https://download.geofabrik.de/north-america/us"
mkdir -p "$RAW_DIR" "$DATA/aoi" "$OUT_DIR"

# slug|StateName for all 50 states + DC. slug = Geofabrik us/ subregion.
STATES=(
alabama"|"Alabama alaska"|"Alaska arizona"|"Arizona arkansas"|"Arkansas
california"|"California colorado"|"Colorado connecticut"|"Connecticut
delaware"|"Delaware district-of-columbia"|"District\ of\ Columbia florida"|"Florida
georgia"|"Georgia hawaii"|"Hawaii idaho"|"Idaho illinois"|"Illinois
indiana"|"Indiana iowa"|"Iowa kansas"|"Kansas kentucky"|"Kentucky
louisiana"|"Louisiana maine"|"Maine maryland"|"Maryland massachusetts"|"Massachusetts
michigan"|"Michigan minnesota"|"Minnesota mississippi"|"Mississippi missouri"|"Missouri
montana"|"Montana nebraska"|"Nebraska nevada"|"Nevada new-hampshire"|"New\ Hampshire
new-jersey"|"New\ Jersey new-mexico"|"New\ Mexico new-york"|"New\ York
north-carolina"|"North\ Carolina north-dakota"|"North\ Dakota ohio"|"Ohio
oklahoma"|"Oklahoma oregon"|"Oregon pennsylvania"|"Pennsylvania
rhode-island"|"Rhode\ Island south-carolina"|"South\ Carolina south-dakota"|"South\ Dakota
tennessee"|"Tennessee texas"|"Texas utah"|"Utah vermont"|"Vermont
virginia"|"Virginia washington"|"Washington west-virginia"|"West\ Virginia
wisconsin"|"Wisconsin wyoming"|"Wyoming
)

# optional subset from argv (match by slug)
WANT=("$@")
want() {
  [ ${#WANT[@]} -eq 0 ] && return 0
  for w in "${WANT[@]}"; do [ "$w" = "$1" ] && return 0; done
  return 1
}

ok=(); failed=(); skipped=()
for entry in "${STATES[@]}"; do
  slug="${entry%%|*}"; name="${entry#*|}"
  want "$slug" || continue
  echo; echo "==================== $name ($slug) ===================="

  raw="$RAW_DIR/$slug.osm.pbf"
  url="$GEOFABRIK/$slug-latest.osm.pbf"
  echo ">> download $url"
  if ! curl -fL --retry 3 -C - -o "$raw" "$url"; then
    echo "!! no extract for $slug — skipping"; skipped+=("$slug"); rm -f "$raw"; continue
  fi

  echo ">> prefilter -> $DATA/hiking.osm.pbf"
  if ! bash extract/prefilter.sh "$raw" "$DATA/hiking.osm.pbf"; then
    echo "!! prefilter failed for $slug"; failed+=("$slug prefilter"); rm -f "$raw"; continue
  fi
  rm -f "$raw"                              # free disk before assemble

  trails="$DATA/aoi/$slug.trails.geojson"
  echo ">> assemble -> $trails"
  if ! python3 assemble/assemble.py --in "$DATA/hiking.osm.pbf" --out "$trails" \
        --min-length-mi "$MINLEN" --per-area-merge; then
    echo "!! assemble failed for $slug"; failed+=("$slug assemble"); continue
  fi

  echo ">> publish --state '$name'"
  dry=(); [ -n "${DRYRUN:-}" ] && dry=(--dry-run)
  if ! python3 serve/publish_areas.py --trails "$trails" --hiking "$DATA/hiking.osm.pbf" \
        --out-dir "$OUT_DIR" --state "$name" "${dry[@]}"; then
    echo "!! publish failed for $slug"; failed+=("$slug publish"); continue
  fi
  ok+=("$slug")
done

echo; echo "==================== SUMMARY ===================="
echo "published (${#ok[@]}): ${ok[*]:-none}"
echo "skipped   (${#skipped[@]}): ${skipped[*]:-none}"
echo "failed    (${#failed[@]}): ${failed[*]:-none}"
echo
echo "No git changes were committed. Review with:  git -C $REPO_ROOT status"
echo "Then commit public/areas + regenerate the app bundle for one R2 sync."
