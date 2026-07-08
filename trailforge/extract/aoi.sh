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

OUT_DIR="data/aoi"
mkdir -p "$OUT_DIR"

# --strategy=smart completes multipolygon/boundary relations that straddle
# the bbox, so area boundaries (e.g. a park relation) assemble into whole
# polygons for the trail↔area filter.
osmium extract --strategy=smart --bbox "$BBOX" "$HIKING" \
  -o "$OUT_DIR/$NAME.osm.pbf" --overwrite

# Raw export for the viewer's "what OSM has" layer. The assembler's output
# (one feature per assembled TRAIL) lands at $OUT_DIR/$NAME.trails.geojson
# — produced by assemble/, not here.
osmium export "$OUT_DIR/$NAME.osm.pbf" \
  -f geojson --geometry-types=linestring,point \
  --add-unique-id=type_id \
  -o "$OUT_DIR/$NAME.raw.geojson" --overwrite
# Area boundaries are assembled from the AOI PBF directly by the assembler
# (libosmium area assembler via pyosmium) when --only-area is used — no
# separate polygon export (osmium export doesn't reliably emit them).

echo "aoi: $NAME ($BBOX)"
echo "  raw ways+POIs -> $OUT_DIR/$NAME.raw.geojson (viewer layer: 'OSM raw')"
echo "  next: run the assembler to produce $OUT_DIR/$NAME.trails.geojson"
