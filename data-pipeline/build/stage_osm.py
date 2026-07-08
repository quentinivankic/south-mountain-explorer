#!/usr/bin/env python3
"""Stage OSM into normalized trail + area GeoJSON (spec §3.1–3.2).

Input is an `osmium export` GeoJSON FeatureCollection (each feature's
`properties` is its raw OSM tag map, geometry already assembled). This is
the pure-Python staging path used by the CI pilot: it produces the same
Bucket A raw-signal schema (§4.1) and the area schema (§4.3) that
build/trails.sql / build/areas.sql produce via DuckDB, without the
osmium→parquet column-mapping step. The DuckDB SQL remains the documented
path for planet-scale runs; this stager is the reliable, testable path
for a single-country pilot.

It carries EVERY trail through — no score filter (§4.4, §7.1). The only
exclusion anywhere is the licensing gate; OSM (ODbL) always passes it.

  osmium export nz.osm.pbf -f geojson -o nz.geojson
  python3 build/stage_osm.py --in nz.geojson \
      --trails-out staging/nz_trails.geojson \
      --areas-out   staging/nz_areas.geojson
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

TRAIL_HIGHWAYS = {"path", "footway", "track", "bridleway"}
LINE_GEOMS = {"LineString", "MultiLineString"}
POLY_GEOMS = {"Polygon", "MultiPolygon"}

# A highway=track whose NAME reads like a road, or that allows motor
# vehicles, is a forest/utility/farm road — not a hike. (From the
# production seed pipeline's `_is_road_like`.)
_ROAD_WORDS = ("road", "drive", "avenue", "canal", "drain", "ditch",
               "boulevard", "highway", "freeway")


def _vehicle_or_utility_road(props: dict[str, Any], name: str | None) -> bool:
    if props.get("highway") != "track":
        return False
    lname = (name or "").lower()
    if any(w in lname for w in _ROAD_WORDS):
        return True
    return _lower(props.get("motor_vehicle")) == "yes" or _lower(props.get("motorcar")) == "yes"


def _lower(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def is_trail(props: dict[str, Any]) -> bool:
    return (props.get("highway") in TRAIL_HIGHWAYS
            or props.get("abandoned:highway") in TRAIL_HIGHWAYS)


def normalize_trail(props: dict[str, Any], osm_id: str,
                    route: dict[str, Any] | None = None) -> dict[str, Any]:
    """OSM tags -> Bucket A raw signals (§4.1). Null-safe, lowercased.

    `route` is this way's entry from build/route_index.py (hiking
    route-relation membership) if any — a GLOBAL "recognised trail" signal
    that needs no per-country data source. A route's `operator` counts as
    a maintaining body for `has_known_operator`.
    """
    route = route or {}
    name = props.get("name")
    operator = props.get("operator") or route.get("route_operator")

    ab = props.get("abandoned:highway")
    disused = _lower(props.get("disused"))
    trail_status = _lower(props.get("trail_status"))
    if ab in TRAIL_HIGHWAYS or trail_status == "abandoned":
        lifecycle = "abandoned"
    elif disused in {"yes", "true"} or trail_status == "disused":
        lifecycle = "disused"
    else:
        lifecycle = "active"

    return {
        "osm_id": osm_id,
        "osm_version": int(props.get("@version", props.get("osm_version", 0)) or 0),
        "osm_timestamp": props.get("@timestamp") or props.get("osm_timestamp"),
        "highway": props.get("highway") or ab,
        "name": name,
        "has_name": bool(name and str(name).strip()),
        "has_known_operator": bool(operator and str(operator).strip()),
        "informal": _lower(props.get("informal")),
        "access": _lower(props.get("access")),
        "sac_scale": _lower(props.get("sac_scale")),
        "trail_visibility": _lower(props.get("trail_visibility")),
        "surface": _lower(props.get("surface")),
        "lifecycle": lifecycle,
        "tiger_unreviewed": _lower(props.get("tiger:reviewed")) == "no",
        # A track that's really a road/utility corridor (name or motor tags).
        "vehicle_or_utility_road": _vehicle_or_utility_road(props, name),
        # Global "official" signals (Bucket A-ish; from route relations).
        "in_route_relation": bool(route.get("in_route")),
        "network": _lower(route.get("network")) or "",
    }


def area_scheme_rank(props: dict[str, Any]) -> tuple[str, int] | None:
    """(scheme, authority_rank) for a named-area polygon, or None if not one."""
    if props.get("boundary") == "protected_area":
        rank = 40 if props.get("ref:whon") or props.get("wdpa") else 35
        return "osm_protected_area", rank
    if props.get("boundary") == "national_park":
        return "osm_national_park", 30
    if props.get("leisure") == "nature_reserve":
        return "osm_nature_reserve", 25
    if props.get("landuse") == "forest":
        return "osm_forest", 10
    return None


def normalize_area(props: dict[str, Any], osm_id: str) -> dict[str, Any] | None:
    sr = area_scheme_rank(props)
    if sr is None:
        return None
    scheme, rank = sr
    return {
        "osm_id": osm_id,
        "name": props.get("name") or props.get("name:en"),
        "scheme": scheme,
        "protect_class": props.get("protect_class"),
        "protection_title": props.get("protection_title"),
        "source_id": "osm",
        "authority_rank": rank,
        "referential": False,
    }


def stage(fc: dict[str, Any],
          route_index: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    route_index = route_index or {}
    trails, areas = [], []
    for i, f in enumerate(fc.get("features", [])):
        props = f.get("properties", {}) or {}
        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        # osmium export --add-unique-id=type_id puts the id on the FEATURE
        # ("id": "w123"), not in properties — so f.get("id") is the real
        # source here; the props.* fallbacks are for hand-built fixtures.
        # Missing this made osm_id fall to the enumerate index, which never
        # matched route_index's "w<id>" keys → the route signal was dead.
        osm_id = str(props.get("@id") or props.get("osm_id") or props.get("id")
                     or f.get("id") or i)

        if gtype in LINE_GEOMS and is_trail(props):
            trails.append({"type": "Feature", "geometry": geom,
                           "properties": normalize_trail(props, osm_id,
                                                          route_index.get(osm_id))})
        elif gtype in POLY_GEOMS:
            a = normalize_area(props, osm_id)
            if a is not None:
                areas.append({"type": "Feature", "geometry": geom, "properties": a})

    return ({"type": "FeatureCollection", "features": trails},
            {"type": "FeatureCollection", "features": areas})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage OSM export -> normalized trails+areas (§3)")
    ap.add_argument("--in", dest="inp", required=True, help="osmium export GeoJSON")
    ap.add_argument("--trails-out", required=True)
    ap.add_argument("--areas-out", required=True)
    ap.add_argument("--route-index", help="build/route_index.py JSON (optional)")
    args = ap.parse_args(argv)

    with open(args.inp, encoding="utf-8") as fh:
        fc = json.load(fh)
    route_index = {}
    if args.route_index:
        with open(args.route_index, encoding="utf-8") as fh:
            route_index = json.load(fh)
    trails, areas = stage(fc, route_index)
    with open(args.trails_out, "w", encoding="utf-8") as fh:
        json.dump(trails, fh)
    with open(args.areas_out, "w", encoding="utf-8") as fh:
        json.dump(areas, fh)
    print(f"staged {len(trails['features'])} trails, {len(areas['features'])} areas",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
