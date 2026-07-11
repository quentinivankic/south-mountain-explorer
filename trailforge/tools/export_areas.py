#!/usr/bin/env python3
"""Write park-boundary polygons to <aoi>.areas.geojson for the QA viewer.

The statewide assemble emits trails + removed, but NOT the area polygons the
viewer needs to (a) draw park boundaries and (b) decide "Only trails in an
area" by geometry — which is how the publisher actually selects trails. Without
this file the viewer falls back to the assembler's `area` merge-scope tag, so a
touch-published straddler (DC-Ray Connector, area=None) looks absent though it
ships. This reads the same hiking PBF, unions each named boundary's rings the
same way publish_areas does (Polygon-per-ring + unary_union, holes preserved),
and writes a FeatureCollection.

    python3 tools/export_areas.py \
      --hiking data/hiking.osm.pbf --out data/aoi/arizona.areas.geojson
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assemble"))
import areas as areamod  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="export park boundaries -> viewer areas.geojson")
    ap.add_argument("--hiking", required=True, help="hiking.osm.pbf (same one the assemble used)")
    ap.add_argument("--out", required=True, help="e.g. data/aoi/arizona.areas.geojson")
    args = ap.parse_args(argv)

    from shapely.geometry import Polygon, mapping
    from shapely.ops import unary_union

    feats = []
    for b in areamod.merge_areas(args.hiking):
        name = b.get("name")
        if not name:
            continue
        polys = [Polygon(r) for r in (b.get("rings") or []) if len(r) >= 4]
        if not polys:
            continue
        u = unary_union(polys)
        if not u.is_valid:
            u = u.buffer(0)
        if u.is_empty:
            continue
        feats.append({"type": "Feature",
                      "properties": {"name": name},
                      "geometry": mapping(u)})

    json.dump({"type": "FeatureCollection", "features": feats}, open(args.out, "w"))
    print(f"wrote {len(feats)} area polygons -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
