#!/bin/bash
# New England per-area pipeline against the shared ne_region + ne_parking extracts.
# Usage: ./run_ne.sh <slug>
TMP="${PADJ_TMP:-$(cd "$(dirname "$0")/.." && pwd)/work}"
mkdir -p "$TMP"
RAID=/mnt/raid/trekdex/parking-adjud/osm
cd $TMP
# region_parking symlink so dossier.py reads NE parking (not the 115 MB national file)
[ -e region_parking.osm.pbf ] || ln -s $RAID/ne_parking.osm.pbf region_parking.osm.pbf
SLUG="$1"
GEOM="/home/quentin/south-mountain-explorer/public/areas/geom/$SLUG.json"
read X0 Y0 X1 Y1 < <(python3 -c "import json;b=json.load(open('$GEOM'))['bbox'];print(b[0]-0.06,b[1]-0.06,b[2]+0.06,b[3]+0.06)")
echo "[$SLUG] $X0,$Y0,$X1,$Y1"
# per-area ctx pbf, sub-extracted from the region (seconds), stored on RAID + symlinked here
[ -f "$RAID/${SLUG}_ctx.osm.pbf" ] || osmium extract --bbox=$X0,$Y0,$X1,$Y1 -o "$RAID/${SLUG}_ctx.osm.pbf" --overwrite "$RAID/ne_region.osm.pbf"
[ -e "${SLUG}_ctx.osm.pbf" ] || ln -s "$RAID/${SLUG}_ctx.osm.pbf" "${SLUG}_ctx.osm.pbf"
echo "[$SLUG] dossier";  python3 dossier.py "$SLUG"          2>&1 | grep -E "facilities|EDGE|context"
echo "[$SLUG] walk";     python3 foot_route_area.py "$SLUG"  2>&1 | grep -E "network|routed"
echo "[$SLUG] context";  python3 context_classify.py "$SLUG" "$RAID/${SLUG}_ctx.osm.pbf" 2>&1 | tail -1
echo "[$SLUG] DONE"
