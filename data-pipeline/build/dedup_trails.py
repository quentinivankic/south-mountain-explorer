#!/usr/bin/env python3
"""Collapse multi-segment named routes into one trail (NZ review fix #1).

OSM maps long routes as many separately-named way-segments — Te Araroa
(~3,000 km) is ~15,000 highway=path ways all named "Te Araroa Trail".
The staged trails layer treated each as a distinct trail, so ~60% of NZ's
high-confidence set was ONE route counted thousands of times. A
completion app must treat a route as a single trail.

This groups NAMED trails by name and, within each name, splits them into
CONNECTED COMPONENTS — adjacent OSM ways share an exact node coordinate at
their join, so segments that chain end-to-end are one route while two
distinct same-named trails in different regions (no shared endpoints) stay
separate. Each component is merged into one MultiLineString feature;
Bucket A signals are combined (has_name kept, operator OR-ed, everything
else by majority) and `segment_count` recorded. Unnamed trails pass
through untouched — they can't be grouped without route relations and are
mostly the unnamed-footway noise the score already drops.

Runs AFTER stage_osm and BEFORE conflation, so authoritative_match /
in_official_whitelist (computed later on the merged geometry) see the
whole route. Pure stdlib — no geo deps; connectivity uses shared OSM
node coordinates, not fuzzy geometry.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from typing import Any

# Bucket A positive signal(s) where "any member fires" ⇒ the route fires.
_OR_TRUE = ("has_known_operator",)
# Fields combined by majority value across members.
_MAJORITY = ("access", "informal", "lifecycle", "trail_visibility",
             "sac_scale", "surface", "highway", "region_trust")
_COORD_PRECISION = 6  # stage_osm already rounds coords to 1e-6


def _lines(geom: dict[str, Any]) -> list[list[list[float]]]:
    t = geom.get("type")
    if t == "LineString":
        return [geom["coordinates"]]
    if t == "MultiLineString":
        return list(geom["coordinates"])
    return []


def _endpoint_keys(geom: dict[str, Any]) -> list[tuple[float, float]]:
    keys = []
    for line in _lines(geom):
        for pt in (line[0], line[-1]) if len(line) >= 1 else []:
            keys.append((round(pt[0], _COORD_PRECISION), round(pt[1], _COORD_PRECISION)))
    return keys


def _connected_components(feats: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Union-find over segments that share an exact endpoint node."""
    parent = list(range(len(feats)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_endpoint: dict[tuple, list[int]] = defaultdict(list)
    for i, f in enumerate(feats):
        for k in _endpoint_keys(f.get("geometry", {})):
            by_endpoint[k].append(i)
    for idxs in by_endpoint.values():
        first = idxs[0]
        for j in idxs[1:]:
            union(first, j)

    comps: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, f in enumerate(feats):
        comps[find(i)].append(f)
    return list(comps.values())


def _merge(name: str, comp: list[dict[str, Any]]) -> dict[str, Any]:
    props_list = [f.get("properties", {}) or {} for f in comp]
    merged: dict[str, Any] = {"name": name, "has_name": True}

    for k in _OR_TRUE:
        merged[k] = any(bool(p.get(k)) for p in props_list)
    for k in _MAJORITY:
        # Count absent/empty as a "not tagged" vote, so a restrictive value
        # (access=no, informal=yes) only sticks if it's on the MAJORITY of
        # the route's segments — one bad segment doesn't taint the route.
        vals = [(p.get(k) or None) if p.get(k) not in (None, "") else None
                for p in props_list]
        top, _cnt = Counter(vals).most_common(1)[0]
        if top is not None:
            merged[k] = top
    # tiger_unreviewed is a (US-only) negative flag; carry it if the
    # majority of members have it.
    tiger = [bool(p.get("tiger_unreviewed")) for p in props_list]
    merged["tiger_unreviewed"] = tiger.count(True) > len(tiger) / 2
    # Latest edit across members (drives on-device recency).
    ts = [p.get("osm_timestamp") for p in props_list if p.get("osm_timestamp")]
    if ts:
        merged["osm_timestamp"] = max(ts)

    merged["segment_count"] = len(comp)
    # Stable id: smallest member osm_id, prefixed so it's clearly a route.
    ids = sorted(str(p.get("osm_id", "")) for p in props_list if p.get("osm_id"))
    merged["osm_id"] = "route:" + (ids[0] if ids else name)

    geom = {"type": "MultiLineString",
            "coordinates": [ln for f in comp for ln in _lines(f.get("geometry", {}))]}
    return {"type": "Feature", "geometry": geom, "properties": merged}


def dedup(fc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    named: dict[str, list[dict[str, Any]]] = defaultdict(list)
    out: list[dict[str, Any]] = []

    for f in fc.get("features", []):
        p = f.get("properties", {}) or {}
        name = (p.get("name") or "").strip()
        if p.get("has_name") and name:
            named[name].append(f)
        else:
            out.append(f)  # unnamed → untouched

    merged_routes = 0
    for name, group in named.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        for comp in _connected_components(group):
            out.append(_merge(name, comp) if len(comp) > 1 else comp[0])
            if len(comp) > 1:
                merged_routes += 1

    stats = {
        "in": len(fc.get("features", [])),
        "out": len(out),
        "routes_merged": merged_routes,
    }
    return {"type": "FeatureCollection", "features": out}, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collapse multi-segment named routes (fix #1)")
    ap.add_argument("--in", dest="inp", required=True, help="staged trails GeoJSON")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    with open(args.inp, encoding="utf-8") as fh:
        fc = json.load(fh)
    out, stats = dedup(fc)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print(f"dedup: {stats['in']:,} trails -> {stats['out']:,} "
          f"({stats['routes_merged']:,} multi-segment routes collapsed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
