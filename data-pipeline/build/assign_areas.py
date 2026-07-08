#!/usr/bin/env python3
"""Assign each trail to the area(s) it lies in — the trail↔area join.

Turns a flat trails layer + area polygons into "areas WITH their trails":
the completion-app data model, produced globally from OSM. For each trail
we find the OSM area polygon(s) it falls in (point-in-polygon on the
trail's representative point, plus any polygon its geometry crosses) and
attach `area_ids`. We also emit an areas index (area → trail count + total
miles) mirroring the production seed pipeline's shape.

Boundary source is the OSM `areas` layer the pipeline already extracts
(ODbL, attributed). NEVER WDPA — its licence prohibits commercial use /
app redistribution (see UNIFIED_FILTER_SPEC.md, research review).

Needs shapely (the stage step installs it).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from typing import Any


def _require_shapely():
    try:
        import shapely  # noqa: F401
        from shapely.geometry import shape  # noqa: F401
        from shapely.strtree import STRtree  # noqa: F401
    except ImportError:
        sys.stderr.write("ERROR: assign_areas needs shapely — pip install 'shapely>=2.0'\n")
        raise SystemExit(3)


def _miles(geom) -> float:
    """Rough length in miles via haversine over the geometry's lines."""
    def line_mi(coords):
        total = 0.0
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            dlat = math.radians(y1 - y0)
            dlon = math.radians(x1 - x0)
            a = (math.sin(dlat / 2) ** 2
                 + math.cos(math.radians(y0)) * math.cos(math.radians(y1))
                 * math.sin(dlon / 2) ** 2)
            total += 6371.0088 * 2 * math.asin(min(1, math.sqrt(a)))
        return total / 1.609344
    t = geom.geom_type
    if t == "LineString":
        return line_mi(list(geom.coords))
    if t == "MultiLineString":
        return sum(line_mi(list(g.coords)) for g in geom.geoms)
    return 0.0


def _area_id(props: dict[str, Any]) -> str:
    return str(props.get("osm_id") or props.get("name") or "")


def assign(trails_fc: dict[str, Any],
           areas_fc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _require_shapely()
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    area_feats = areas_fc.get("features", [])
    area_geoms = [shape(f["geometry"]) for f in area_feats]
    area_props = [f.get("properties", {}) or {} for f in area_feats]
    tree = STRtree(area_geoms) if area_geoms else None

    stats: dict[str, dict[str, Any]] = {}
    for p in area_props:
        stats[_area_id(p)] = {
            "area_id": _area_id(p), "name": p.get("name"),
            "authority_rank": p.get("authority_rank"),
            "trail_count": 0, "total_mi": 0.0,
        }

    out_trails = []
    for f in trails_fc.get("features", []):
        geom = shape(f["geometry"])
        rep = geom.representative_point()  # guaranteed to lie on the trail
        area_ids: list[str] = []
        if tree is not None:
            # Primary: polygon containing the representative point. Also
            # include any polygon the trail crosses (multi-park trails).
            for idx in tree.query(geom):
                poly = area_geoms[idx]
                if poly.contains(rep) or poly.intersects(geom):
                    aid = _area_id(area_props[idx])
                    if aid and aid not in area_ids:
                        area_ids.append(aid)
        props = dict(f.get("properties", {}) or {})
        props["area_ids"] = area_ids
        out_trails.append({"type": "Feature", "geometry": f["geometry"],
                           "properties": props})

        mi = _miles(geom)
        for aid in area_ids:
            if aid in stats:
                stats[aid]["trail_count"] += 1
                stats[aid]["total_mi"] = round(stats[aid]["total_mi"] + mi, 2)

    trails_out = {"type": "FeatureCollection", "features": out_trails}
    # Areas index: only areas that actually contain trails, richest first.
    index = sorted((s for s in stats.values() if s["trail_count"] > 0),
                   key=lambda s: -s["trail_count"])
    return trails_out, {"areas": index}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assign trails to their area(s) (trail↔area join)")
    ap.add_argument("--trails", required=True)
    ap.add_argument("--areas", required=True)
    ap.add_argument("--trails-out", required=True, help="trails GeoJSON + area_ids")
    ap.add_argument("--index-out", required=True, help="areas index JSON (area -> counts)")
    args = ap.parse_args(argv)

    with open(args.trails, encoding="utf-8") as fh:
        trails = json.load(fh)
    with open(args.areas, encoding="utf-8") as fh:
        areas = json.load(fh)
    trails_out, index = assign(trails, areas)
    with open(args.trails_out, "w", encoding="utf-8") as fh:
        json.dump(trails_out, fh)
    with open(args.index_out, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)

    grouped = sum(1 for f in trails_out["features"] if f["properties"]["area_ids"])
    print(f"trail↔area: {grouped:,}/{len(trails_out['features']):,} trails in an area; "
          f"{len(index['areas']):,} areas with trails", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
