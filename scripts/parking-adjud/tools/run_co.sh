#!/bin/bash
# Colorado per-area adjudication pipeline (dossier + serves gate + foot-walk + OSM context).
# Env-configured tools write to a LOCAL working dir (fast); OSM extracts live on the NAS.
# Usage: ./run_co.sh <area-slug>
set -e
export PADJ_TMP="${PADJ_TMP:-$(cd "$(dirname "$0")/.." && pwd)/work/co}"
export PADJ_PARKING_PBF=/mnt/raid/trekdex/parking-adjud/co/osm/colorado_parking.osm.pbf
export PADJ_US=/mnt/raid/trekdex/parking-adjud/co/osm/colorado-latest.osm.pbf
TOOLS="$(cd "$(dirname "$0")" && pwd)"
CTX=/mnt/raid/trekdex/parking-adjud/co/osm/ctx
mkdir -p "$PADJ_TMP"
SLUG="$1"
[ -f "$CTX/${SLUG}_ctx.osm.pbf" ] || { echo "[$SLUG] NO CTX PBF — skipped"; exit 3; }
ln -sf "$CTX/${SLUG}_ctx.osm.pbf" "$PADJ_TMP/${SLUG}_ctx.osm.pbf"
cd "$PADJ_TMP"
python3 "$TOOLS/dossier.py"         "$SLUG" 2>&1 | grep -E "facilities|EDGE|context" | sed "s/^/[$SLUG] /"
python3 "$TOOLS/foot_route_area.py" "$SLUG" 2>&1 | grep -E "network|routed" | sed "s/^/[$SLUG] /"
python3 "$TOOLS/context_classify.py" "$SLUG" "$CTX/${SLUG}_ctx.osm.pbf" 2>&1 | tail -1 | sed "s/^/[$SLUG] /"
echo "[$SLUG] DONE"
