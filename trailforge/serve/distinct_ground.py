"""Distinct-ground miles: an area's trail miles with shared ground counted once.

WHY THIS EXISTS. `total_mi` is the sum of every trail row's `distanceMi`. When two
named trails run along the same path (two OSM routes on one way, a connector
drawn into a loop), that path is counted once per trail. South Mountain Park
reads 122.6 mi that way, but its trails cover about 110.5 mi of ground.

WHAT IT COMPUTES. Every segment is split into edges (consecutive point pairs).
An edge is the same ground as another when it joins the same two points, in
either direction. The first time an edge is seen it is new ground; every later
copy is shared ground. The result is

    distinct_mi = round(sum(distanceMi) - shared miles, 1)

so an area with no shared edges gets exactly the number `degenerate.area_miles`
gives for `total_mi`, and the per-trail rows the app shows are not touched.

WHY EDGES AND NOT A GEOMETRY UNION. Shared trails in published geom share the
same OSM nodes, so their edges are identical after the 6 dp rounding in
`to_app_json.convert`. Measured on 401 published areas (11/12/2026): edge dedup
and a shapely `unary_union` agreed to 0.00 mi on every area, 61 of them with
real overlap. Edges need no shapely, are deterministic, and cost one dict pass.
Two lines that run side by side without sharing nodes are not merged; that is
the same answer the union gives.

ADDITIVE ONLY. `publish_areas.py` writes `distinct_mi` next to `total_mi` in the
geom file. `total_mi`, the index tuple, preview rows and every validator input
stay exactly as they were.
"""
from __future__ import annotations

import math

import degenerate

FIELD = "distinct_mi"
_R_MI = 3958.8


def _edge_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine miles, the same asin form and radius as `degenerate._seg_miles`."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * _R_MI * math.asin(min(1.0, math.sqrt(h)))


def shared_miles(trails: list[dict]) -> float:
    """Miles of edges drawn more than once, across all trails and within one.

    Exactly 0.0 when no edge repeats, because only repeats are added."""
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    shared = 0.0
    for trail in trails:
        for seg in trail.get("segments") or []:
            for i in range(1, len(seg)):
                a, b = seg[i - 1], seg[i]
                if len(a) < 2 or len(b) < 2:
                    continue
                pa = (float(a[0]), float(a[1]))
                pb = (float(b[0]), float(b[1]))
                if pa == pb:
                    continue
                key = (pa, pb) if pa <= pb else (pb, pa)
                if key in seen:
                    shared += _edge_miles(pa, pb)
                else:
                    seen.add(key)
    return shared


def distinct_miles(trails: list[dict], total: float | None = None) -> float:
    """Area miles with shared ground counted once, rounded like `total_mi`.

    `total` is the unrounded sum the caller rounds into `total_mi`. By default
    it is the same per-trail sum `degenerate.area_miles` uses. With no shared
    edges, `total - 0.0` is `total`, so the result equals `total_mi` exactly."""
    if total is None:
        total = 0.0
        for t in trails:
            d = t.get("distanceMi")
            total += float(d) if isinstance(d, (int, float)) else degenerate.trail_miles(t)
    return round(max(0.0, total - shared_miles(trails)), 1)


def with_distinct_mi(row: dict) -> dict:
    """A shallow copy of `row` with `distinct_mi` added last. The caller's row is
    not changed, so preview rows and validated records keep their exact shape."""
    out = dict(row)
    out[FIELD] = distinct_miles(list(row.get("trails") or []))
    return out
