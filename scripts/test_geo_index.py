#!/usr/bin/env python3
"""Tests for the spatial-index containment helper (no network)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "gi", Path(__file__).resolve().parent / "_geo_index.py")
gi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gi)

# ~1.1 km box near 44N, in (lon, lat) rings.
BOX = [[-72.010, 44.000], [-71.990, 44.000], [-71.990, 44.010],
       [-72.010, 44.010], [-72.010, 44.000]]
RINGS = {"park-a": [BOX]}


def _pass(lat, lon, max_edge_m=1000.0):
    return list(gi.containment_pass([{"lat": lat, "lon": lon}], RINGS,
                                    max_edge_m=max_edge_m))


def test_a_point_inside_is_inside_at_zero_distance():
    got = _pass(44.005, -72.000)
    assert got == [(0, "park-a", True, 0.0)]


def test_a_point_just_outside_reports_a_small_positive_distance():
    # ~50 m north of the top edge (1 deg lat ~ 111 km, so 0.00045 deg ~ 50 m).
    got = _pass(44.01045, -72.000)
    assert len(got) == 1
    _, key, inside, m = got[0]
    assert key == "park-a" and inside is False
    assert 30 < m < 80, m           # metres, not degrees — the bug this guards


def test_a_far_point_is_not_a_candidate():
    # 5 km away, well past a 1 km budget.
    assert _pass(44.055, -72.000) == []


def test_the_budget_is_in_metres():
    # 200 m out: kept at a 1 km budget, dropped at 100 m. If the code ever
    # compares degrees to a metre budget again, one of these flips.
    lat = 44.010 + 200 / 111_320.0
    assert len(_pass(lat, -72.000, max_edge_m=1000.0)) == 1
    assert _pass(lat, -72.000, max_edge_m=100.0) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
