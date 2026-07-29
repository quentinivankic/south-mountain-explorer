#!/usr/bin/env python3
"""Spatial-index helpers, so measurement stops being O(points × areas).

WHY THIS EXISTS. The same mistake was made three times in one day (2026-07-29):
a script asks "which parking lots fall inside which park boundaries", written as
nested Python loops over raw vertex lists — O(lots × areas × ring-segments). At
national scale that is tens of millions of trig calls and it ran for 25 minutes
before the user killed it, twice. The data is not big; the algorithm was wrong.

The fix is an R-tree. shapely ships one (`STRtree`), it was already used in
`measure44_real.py`, and a point-in-many-polygons query with it is O(log n).
Whole-US containment is one index build plus a lookup per lot — seconds.

RULE OF THUMB: if you are about to compare every X to every Y by distance, stop
and build one of these first. `containment_pass` below is the reusable version;
prefer extending it over hand-rolling the loop again.
"""
from __future__ import annotations


def build(area_rings: dict) -> tuple:
    """Index a {key: [ring, ...]} map of boundaries (rings are [(lon,lat),...]).

    Returns (tree, polys, keys) where `polys[i]`/`keys[i]` align with the tree's
    integer results. A key whose rings do not form a valid polygon is dropped
    rather than silently distorting a query.
    """
    from shapely.geometry import Polygon, MultiPolygon
    from shapely.strtree import STRtree

    polys, keys = [], []
    for key, rings in area_rings.items():
        good = [Polygon(r) for r in rings if r and len(r) >= 4]
        good = [p if p.is_valid else p.buffer(0) for p in good]
        good = [p for p in good if not p.is_empty]
        if not good:
            continue
        poly = good[0] if len(good) == 1 else MultiPolygon(
            [g for p in good for g in (p.geoms if p.geom_type == "MultiPolygon" else [p])])
        polys.append(poly)
        keys.append(key)
    return STRtree(polys), polys, keys


def containment_pass(lots: list, area_rings: dict, max_edge_m: float = 1000.0):
    """For every lot, the areas it could belong to and how it sits relative to
    each — computed ONCE, so any buffer/nearest rule is then a cheap threshold.

    `lots` is [{lat, lon, ...}]. For each lot within `max_edge_m` of an area's
    boundary, yields (lot_index, area_key, inside_bool, dist_to_edge_m). A buffer
    of X keeps `dist_to_edge <= X`; the nearest-park rule keeps the area with the
    smallest `dist_to_edge`. Neither re-runs the geometry.

    O((lots + areas) log areas): the R-tree turns "which boundaries are near this
    lot" from a scan into a lookup. This is the whole point of the module.
    """
    from shapely.geometry import Point
    from shapely.prepared import prep

    tree, polys, keys = build(area_rings)
    prepared = [prep(p) for p in polys]
    # A metre budget expressed in degrees for the tree query box; latitude is the
    # conservative axis (a degree of longitude is shorter away from the equator),
    # so use it and let the exact metre distance filter afterward.
    deg = max_edge_m / 111_320.0

    for li, lot in enumerate(lots):
        lat, lon = lot["lat"], lot["lon"]
        pt = Point(lon, lat)
        box = pt.buffer(deg, quad_segs=2)
        for i in tree.query(box):
            i = int(i)
            inside = prepared[i].contains(pt)
            if inside:
                yield li, keys[i], True, 0.0
                continue
            # exterior distance is in DEGREES (shapely is planar on lon/lat);
            # convert to metres at this latitude before comparing to the budget.
            poly = polys[i]
            dd = (poly.exterior.distance(pt) if poly.geom_type == "Polygon"
                  else min(g.exterior.distance(pt) for g in poly.geoms))
            m = _deg_to_m(dd, lat)
            if m <= max_edge_m:
                yield li, keys[i], False, m


def _deg_to_m(dd: float, lat: float) -> float:
    """A small geodesic distance in degrees -> metres, using the mean of the
    lat/lon scales at this latitude. Good to well under a percent at the <1 km
    ranges the containment gate cares about."""
    import math
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians(lat))
    return dd * (mlat + mlon) / 2.0
