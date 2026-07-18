#!/usr/bin/env python3
"""Unit tests for add-parking.py's pure transforms (no network)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "add_parking", Path(__file__).resolve().parent / "add-parking.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


# A tiny area: one trail from vertex A (33.7400,-118.3730) to B (33.7405,-118.3725).
GEOM = {"trails": [{"segments": [[[33.7400, -118.3730], [33.7405, -118.3725]]]}]}


def _park_node(nid, lat, lon, **tags):
    tags.setdefault("amenity", "parking")
    return {"type": "node", "id": nid, "lat": lat, "lon": lon, "tags": tags}


def _park_way(wid, lat, lon, **tags):
    tags.setdefault("amenity", "parking")
    return {"type": "way", "id": wid, "center": {"lat": lat, "lon": lon}, "tags": tags}


def _trailhead(nid, lat, lon):
    return {"type": "node", "id": nid, "lat": lat, "lon": lon,
            "tags": {"highway": "trailhead"}}


def _names(lots):
    return {l.get("name") for l in lots}


def test_keeps_lots_near_a_trail_drops_far_ones():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Trailhead Lot"),   # ~5 m from A
        _park_way(2, 33.7406, -118.3724, fee="yes"),                 # ~15 m from B
        _park_node(3, 33.7480, -118.3600),                           # ~1 km away -> drop
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert len(lots) == 2, lots
    named = [l for l in lots if l.get("name") == "Trailhead Lot"]
    assert named and "fee" not in named[0]
    assert any(l.get("fee") is True for l in lots), "fee=yes -> fee:true"
    assert all("_dist_m" in l for l in lots), "distance captured for the report"


def test_drops_non_public_access_keeps_permissive():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Gated", access="private"),
        _park_node(2, 33.74006, -118.37299, name="Store", access="customers"),
        _park_node(3, 33.74007, -118.37300, name="Permit", access="permit"),
        _park_node(4, 33.74008, -118.37301, name="Public"),          # untagged -> keep
        _park_node(5, 33.74009, -118.37302, name="Permissive", access="permissive"),
    ]}
    # Dedup would merge these (all ~<5 m apart); test parse_parking directly.
    parsed = ap.parse_parking(data)
    assert _names(parsed) == {"Public", "Permissive"}, _names(parsed)


def test_drops_on_street_parking():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Roadside", parking="street_side"),
        _park_node(2, 33.74006, -118.37299, name="Lane", parking="lane"),
        _park_node(3, 33.74007, -118.37300, name="Shoulder", parking="shoulder"),
        _park_node(4, 33.74008, -118.37301, name="Real Lot", parking="surface"),
    ]}
    assert _names(ap.parse_parking(data)) == {"Real Lot"}


def test_dedups_colocated_lots_preferring_named():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298),                         # unnamed
        _park_node(2, 33.740051, -118.372981, name="Main Lot"),      # ~<5 m -> same lot
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert len(lots) == 1
    assert lots[0].get("name") == "Main Lot"


def test_trailhead_corroboration_keeps_far_lot():
    # A lot ~1.5 km from the trail (would be dropped on proximity) but ~7 m
    # from a highway=trailhead node -> kept and flagged.
    data = {"elements": [
        _trailhead(9, 33.7300, -118.3600),
        _park_node(1, 33.73005, -118.36005, name="TH Lot"),  # far from trail, near TH
        _park_node(2, 33.7200, -118.3500, name="Random"),    # far from both -> drop
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert _names(lots) == {"TH Lot"}, _names(lots)
    assert lots[0].get("trailhead") is True


def test_lot_self_tagged_trailhead_is_flagged():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Combo", highway="trailhead"),
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert lots[0].get("trailhead") is True


def test_strip_internal_removes_dist_keeps_trailhead():
    data = {"elements": [_park_node(1, 33.74005, -118.37298, name="Lot", highway="trailhead")]}
    lots = ap.parking_for_geom(GEOM, data)
    clean = ap._strip_internal(lots)
    assert "_dist_m" not in clean[0]
    assert clean[0].get("trailhead") is True
    assert clean[0]["name"] == "Lot"


def test_dedup_handles_none_distance():
    # Two co-located lots kept via trailhead corroboration with no trail
    # nearby -> both _dist_m None. dedup must merge without crashing.
    lots = [
        {"lat": 33.74, "lon": -118.37, "trailhead": True, "_dist_m": None},
        {"lat": 33.740001, "lon": -118.370001, "trailhead": True, "_dist_m": None, "name": "TH"},
    ]
    kept = ap.dedup(lots, ap.PARKING_DEDUP_M)
    assert len(kept) == 1
    assert kept[0]["_dist_m"] is None
    assert kept[0].get("name") == "TH"


def test_no_trails_no_parking():
    data = {"elements": [_park_node(1, 33.74005, -118.37298, name="Lot")]}
    assert ap.parking_for_geom({"trails": []}, data) == []


def test_point_in_rings():
    # Unit square in (lon, lat); point_in_rings takes (lat, lon).
    ring = [(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)]
    assert ap.point_in_rings(0, 0, [ring])         # centre
    assert not ap.point_in_rings(2, 2, [ring])     # outside
    assert not ap.point_in_rings(0, 5, [ring])     # outside on one axis


def test_containment_gate_drops_across_boundary_lot():
    # A small ring around vertex A only. Both lots are near the trail (A and
    # B), but the B-area lot is OUTSIDE the ring -> dropped by containment,
    # exactly like a lot across the park fence.
    rings = [[(-118.3732, 33.7399), (-118.3728, 33.7399),
              (-118.3728, 33.7402), (-118.3732, 33.7402), (-118.3732, 33.7399)]]
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Inside"),   # near A, in ring
        _park_node(2, 33.7406, -118.3724, name="Outside"),    # near B, out of ring
    ]}
    lots, ths = ap.parse_parking(data), ap.parse_trailheads(data)
    stats: dict = {}
    kept = ap.parking_for_area(GEOM, lots, ths, rings=rings, stats=stats)
    assert _names(kept) == {"Inside"}, _names(kept)
    assert stats.get("containment_dropped") == 1
    # Without a boundary, both survive (proximity-only fallback).
    kept_none = ap.parking_for_area(GEOM, lots, ths, rings=None)
    assert _names(kept_none) == {"Inside", "Outside"}, _names(kept_none)


def test_query_includes_both_layers_and_bbox_order():
    q = ap.overpass_query([-118.38, 33.73, -118.36, 33.75])
    assert '"amenity"="parking"' in q
    assert '"highway"="trailhead"' in q
    assert "(33.73,-118.38,33.75,-118.36)" in q   # S,W,N,E


def test_state_query_scopes_to_iso_area():
    q = ap.overpass_state_query("az")
    assert 'area["ISO3166-2"="US-AZ"]->.s' in q
    assert '"amenity"="parking"' in q and '"highway"="trailhead"' in q
    assert "(area.s)" in q


def test_bbox_prefilter_and_shared_lots_not_mutated():
    # State-wide lots shared across areas: one inside the area near the trail,
    # one far outside the bbox. parking_for_area must keep the near one, drop
    # the far one (bbox pre-filter), and NOT mutate the shared input.
    geom = {"trails": GEOM["trails"], "bbox": [-118.3735, 33.7398, -118.3720, 33.7407]}
    shared = [
        {"lat": 33.74005, "lon": -118.37298, "name": "In", "_self_th": False},
        {"lat": 34.50, "lon": -117.00, "name": "FarAway", "_self_th": False},
    ]
    snapshot = [dict(l) for l in shared]
    kept = ap.parking_for_area(geom, shared, [])
    assert _names(kept) == {"In"}, _names(kept)
    assert shared == snapshot, "shared lots must not be mutated"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
