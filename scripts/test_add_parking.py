#!/usr/bin/env python3
"""Unit tests for add-parking.py's pure transforms (no network)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "add_parking", Path(__file__).resolve().parent / "add-parking.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


# A tiny area: one trail near (33.7400, -118.3730).
GEOM = {
    "trails": [
        {"segments": [[[33.7400, -118.3730], [33.7405, -118.3725]]]},
    ]
}


def _node(nid, lat, lon, **tags):
    return {"type": "node", "id": nid, "lat": lat, "lon": lon, "tags": tags}


def _way(wid, lat, lon, **tags):
    return {"type": "way", "id": wid, "center": {"lat": lat, "lon": lon}, "tags": tags}


def test_keeps_lots_near_a_trail_drops_far_ones():
    data = {"elements": [
        _node(1, 33.74005, -118.37298, name="Trailhead Lot"),   # ~5 m from vertex A -> keep
        _way(2, 33.7406, -118.3724, fee="yes"),                 # ~15 m from vertex B -> keep
        _node(3, 33.7480, -118.3600),                           # ~1 km away -> drop
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert len(lots) == 2, lots
    named = [l for l in lots if l.get("name") == "Trailhead Lot"]
    assert named and "fee" not in named[0]
    fee_lot = [l for l in lots if l.get("fee") is True]
    assert fee_lot, "fee=yes should become fee: true"


def test_drops_non_public_access():
    data = {"elements": [
        _node(1, 33.74005, -118.37298, name="Gated Lot", access="private"),
        _node(2, 33.74006, -118.37299, name="Store Lot", access="customers"),
        _node(3, 33.74007, -118.37300, name="Permit Lot", access="permit"),
        _node(4, 33.74008, -118.37301, name="Public Lot"),          # untagged -> keep
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    names = {l.get("name") for l in lots}
    assert names == {"Public Lot"}, names


def test_drops_on_street_parking():
    data = {"elements": [
        _node(1, 33.74005, -118.37298, name="Roadside", parking="street_side"),
        _node(2, 33.74006, -118.37299, name="Lane", parking="lane"),
        _node(3, 33.74007, -118.37300, name="Real Lot", parking="surface"),
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    names = {l.get("name") for l in lots}
    assert names == {"Real Lot"}, names


def test_dedups_colocated_lots_preferring_named():
    data = {"elements": [
        _node(1, 33.74005, -118.37298),                          # unnamed
        _node(2, 33.740051, -118.372981, name="Main Lot"),       # ~<5 m -> same lot
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert len(lots) == 1, lots
    assert lots[0].get("name") == "Main Lot"


def test_no_trails_no_parking():
    data = {"elements": [_node(1, 33.74005, -118.37298, name="Lot")]}
    assert ap.parking_for_geom({"trails": []}, data) == []


def test_bbox_to_overpass_order():
    # geom bbox is [lonmin, latmin, lonmax, latmax]; Overpass wants S,W,N,E.
    q = ap.overpass_parking_query([-118.38, 33.73, -118.36, 33.75])
    assert "(33.73,-118.38,33.75,-118.36)" in q


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
