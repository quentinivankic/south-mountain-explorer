#!/usr/bin/env python3
"""Geometric buffer-matching: OSM ways <-> authoritative trails (spec §5.1).

For a region, match each OSM trail way to authoritative agency geometry
(DOC/NPS/USFS/swisstopo/…) within a small buffer, using directional /
segment overlap so two parallel-but-distinct trails don't false-match.
Also test whether each OSM way falls inside an official-agency boundary
(for the §5 phantom rule + the whitelist boost).

Output is a compact match index consumed by build/confidence.py (Bucket B
flags) and conflation/flags.py (QA flags):

    {
      "<osm_id>": {"matched": bool, "source": "doc"|null,
                    "whitelist": bool, "inside_official_boundary": bool,
                    "osm_name": ..., "auth_name": ...,
                    "osm_operator": ..., "auth_operator": ...},
      ...
    }
    plus a parallel index over authoritative ways for coverage_gap.

This step needs a geometry stack (shapely + an R-tree index). It is
deliberately isolated from the pure flag-decision logic in flags.py so
that safety-critical logic stays testable without the geo deps. If
shapely is unavailable this script exits non-zero with an install hint
rather than silently degrading — matching is not something to fake.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# Buffer for a "same trail" match. Spec §5.1 suggests 15–25 m; DOC
# geometry is high quality so we start at the tight end.
DEFAULT_BUFFER_M = 20.0
# Fraction of an OSM way's length that must fall within the buffer of an
# authoritative way to count as a match (directional/segment overlap).
DEFAULT_MIN_OVERLAP = 0.6


def _require_shapely():
    try:
        import shapely  # noqa: F401
        from shapely.geometry import shape  # noqa: F401
        from shapely.strtree import STRtree  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "ERROR: conflation/match.py needs shapely (+ a spatial index).\n"
            "  pip install 'shapely>=2.0'\n"
            "This is a real geometric step; it is not stubbed on purpose.\n"
        )
        raise SystemExit(3)


def _to_metric_len(geom, lat: float):
    """Rough planar length in metres via local equirectangular scaling.

    Good enough for a 20 m buffer decision at trail scale; the tiling
    step does the accurate reprojection. Avoids a pyproj dependency here.
    """
    import math
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))
    total = 0.0
    coords = list(geom.coords)
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        dx = (x1 - x0) * m_per_deg_lon
        dy = (y1 - y0) * m_per_deg_lat
        total += math.hypot(dx, dy)
    return total


def match_region(
    osm_fc: dict[str, Any],
    auth_fc: dict[str, Any],
    boundaries_fc: dict[str, Any] | None,
    *,
    source_id: str,
    buffer_m: float = DEFAULT_BUFFER_M,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> dict[str, Any]:
    _require_shapely()
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    deg_buffer = buffer_m / 111_320.0  # approx metres->degrees

    auth_geoms = [shape(f["geometry"]) for f in auth_fc.get("features", [])]
    auth_props = [f.get("properties", {}) for f in auth_fc.get("features", [])]
    auth_tree = STRtree(auth_geoms) if auth_geoms else None

    boundary_union = None
    if boundaries_fc and boundaries_fc.get("features"):
        boundary_union = unary_union([shape(f["geometry"])
                                      for f in boundaries_fc["features"]])

    osm_index: dict[str, Any] = {}
    auth_matched = [False] * len(auth_geoms)

    for f in osm_fc.get("features", []):
        props = f.get("properties", {})
        osm_id = str(props.get("osm_id", ""))
        geom = shape(f["geometry"])
        lat = geom.centroid.y

        inside = bool(boundary_union.contains(geom.centroid)) if boundary_union else False

        matched = False
        best_auth_props: dict[str, Any] = {}
        if auth_tree is not None:
            buffered = geom.buffer(deg_buffer)
            own_len = _to_metric_len(geom, lat) or 1.0
            for idx in auth_tree.query(buffered):
                inter = geom.intersection(auth_geoms[idx].buffer(deg_buffer))
                if inter.is_empty:
                    continue
                overlap = _to_metric_len(inter, lat) / own_len if hasattr(inter, "coords") \
                    else inter.length / max(geom.length, 1e-12)
                if overlap >= min_overlap:
                    matched = True
                    auth_matched[idx] = True
                    best_auth_props = auth_props[idx]
                    break

        osm_index[osm_id] = {
            "matched": matched,
            "source": source_id if matched else None,
            # whitelist boost: matched to an agency that publishes only
            # maintained trails, OR inside that agency's official boundary.
            "whitelist": bool(matched or inside),
            "inside_official_boundary": inside,
            "osm_name": props.get("name"),
            "auth_name": best_auth_props.get("name"),
            "osm_operator": props.get("operator"),
            "auth_operator": best_auth_props.get("operator"),
        }

    auth_index = []
    for i, p in enumerate(auth_props):
        auth_index.append({
            "matched": auth_matched[i],
            "auth_name": p.get("name"),
            "source": source_id,
        })

    return {"osm": osm_index, "authoritative": auth_index}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OSM<->authoritative conflation (§5.1)")
    ap.add_argument("--osm", required=True, help="OSM trails GeoJSON")
    ap.add_argument("--authoritative", required=True, help="authoritative trails GeoJSON")
    ap.add_argument("--boundaries", help="official-agency boundary polygons GeoJSON")
    ap.add_argument("--source-id", required=True, help="e.g. doc / nps / usfs")
    ap.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M)
    ap.add_argument("--min-overlap", type=float, default=DEFAULT_MIN_OVERLAP)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    def _load(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    result = match_region(
        _load(args.osm), _load(args.authoritative),
        _load(args.boundaries) if args.boundaries else None,
        source_id=args.source_id, buffer_m=args.buffer_m, min_overlap=args.min_overlap,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result["osm"], fh)
    print(f"matched {sum(1 for v in result['osm'].values() if v['matched'])}"
          f"/{len(result['osm'])} OSM ways against {args.source_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
