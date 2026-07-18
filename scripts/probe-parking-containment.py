#!/usr/bin/env python3
"""PROBE (not shipped): does TRUE point-in-polygon containment against the
real park boundary give a saner parking count than 250 m proximity?

Background: proximity (<=250 m to any trail vertex) over-includes — a small
suburban park's perimeter trails sit within 250 m of neighbour / school /
church lots ACROSS a road. An earlier "containment" probe used Overpass
`map_to_area` on a name match, which UNIONED extra boundaries (e.g. the
Thunderbird School campus) and reported MORE, not fewer — that was a
name-match artifact, not real containment. This probe fetches each area's
ACTUAL boundary polygon by its `osm_relation_id` (the same id publish_areas.py
clips trails against) and classifies each statewide lot by real geometry.

Run on the homelab (needs Overpass egress + shapely):
    python3 scripts/probe-parking-containment.py

For each area it prints:
    contained            lots strictly inside the boundary polygon
    contained & <=250m   inside AND near a trail (the proposed ship gate)
    proximity-only       <=250m of a trail but OUTSIDE the boundary
                         (== the across-the-road bleed proximity wrongly keeps)
Then the sorted contained list so we can eyeball what survives.
"""
import importlib.util
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location("ap", _SCRIPTS / "add-parking.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)

AREAS = [
    "south-mountain-park-and-preserve-az",
    "thunderbird-conservation-park-az",
]
GEOM = _SCRIPTS.parent / "public" / "areas" / "geom"


def fetch_boundary_polygon(rel_id: int):
    """Assemble a shapely (Multi)Polygon from an OSM boundary relation's outer
    ways. `out geom` returns each member way's node coords; polygonize stitches
    the (possibly unordered) outer segments into rings."""
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    q = f"[out:json][timeout:180];rel({rel_id});out geom;"
    data = ap.fetch_overpass(q)
    lines = []
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        for m in el.get("members", []):
            if m.get("role") not in ("outer", "", None):
                continue
            g = m.get("geometry")
            if not g or len(g) < 2:
                continue
            lines.append(LineString([(p["lon"], p["lat"]) for p in g]))
    if not lines:
        return None
    polys = list(polygonize(lines))
    if not polys:
        return None
    u = unary_union(polys)
    return u if u.is_valid else u.buffer(0)


def main():
    from shapely.geometry import Point

    print("AZ: one statewide Overpass query for parking + trailheads...")
    data = ap.fetch_state("az")
    lots = ap.parse_parking(data)
    trailheads = ap.parse_trailheads(data)
    print(f"  {len(lots)} parking + {len(trailheads)} trailheads statewide\n")

    for aid in AREAS:
        geom = json.loads((GEOM / f"{aid}.json").read_text())
        rel_id = geom.get("osm_relation_id")
        poly = fetch_boundary_polygon(rel_id) if rel_id else None
        if poly is None:
            print(f"=== {aid}: NO boundary polygon (rel {rel_id}) — skipped ===\n")
            continue

        # Proximity set (what we ship today): near-trail OR near-trailhead.
        prox = ap.parking_for_area(geom, lots, trailheads)
        prox_keys = {(round(l["lat"], 6), round(l["lon"], 6)) for l in prox}

        contained, contained_near, prox_only = [], [], []
        for l in prox:
            inside = poly.contains(Point(l["lon"], l["lat"]))
            if inside:
                contained.append(l)
                near = l.get("_dist_m") is not None and l["_dist_m"] <= ap.PARKING_TRAIL_MAX_M
                if near or l.get("trailhead"):
                    contained_near.append(l)
            else:
                prox_only.append(l)
        # Also: lots INSIDE the boundary that proximity DROPPED (deep interior
        # lots >250 m from any trail) — recall we'd gain from containment.
        interior_extra = []
        for l in lots:
            p = (round(l["lat"], 6), round(l["lon"], 6))
            if p in prox_keys:
                continue
            if poly.contains(Point(l["lon"], l["lat"])):
                interior_extra.append(l)

        print(f"=== {aid} (rel {rel_id}) ===")
        print(f"  proximity ship-set (today)     : {len(prox)}")
        print(f"  of those, contained in boundary: {len(contained)}")
        print(f"  contained & near-trail (<=250m): {len(contained_near)}")
        print(f"  proximity-only (OUTSIDE bndry) : {len(prox_only)}  <- across-road bleed")
        print(f"  interior lots proximity missed : {len(interior_extra)}")
        print("  --- contained lots (dist to trail, TH flag, name) ---")
        for l in sorted(contained, key=lambda x: (x.get("_dist_m") is None,
                                                   x.get("_dist_m") or 0)):
            d = l.get("_dist_m")
            th = "TH" if l.get("trailhead") else "  "
            print(f"    {str(d) + 'm':>8} {th}  {l.get('name') or '(unnamed)'}")
        if prox_only:
            print("  --- proximity-only (would be DROPPED by containment) ---")
            for l in sorted(prox_only, key=lambda x: (x.get("_dist_m") or 0)):
                print(f"    {str(l.get('_dist_m')) + 'm':>8}     {l.get('name') or '(unnamed)'}")
        print()


if __name__ == "__main__":
    main()
