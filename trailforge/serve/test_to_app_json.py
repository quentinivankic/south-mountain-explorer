#!/usr/bin/env python3
"""Tests for the published area row's boundary id fields."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "to_app_json", Path(__file__).resolve().parent / "to_app_json.py")
conv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conv)

FC = {"features": [{
    "geometry": {"type": "MultiLineString",
                 "coordinates": [[[-112.0, 33.0], [-112.001, 33.001]]]},
    "properties": {"name": "Test Trail", "kind": "trail", "length_mi": 0.1},
}]}


def _row(osm_rel=None, osm_way=None):
    return conv.convert(FC, "test-area-az", "Test Area", "Arizona",
                        (33.0, -112.0), osm_rel, {"trail", "hike", "route"},
                        osm_way=osm_way)


def test_a_way_sourced_area_records_its_way_id():
    # 6,151 of 9,060 shipped areas had NO boundary id, so the containment gate
    # could not run for them and their parking fell back to proximity alone.
    row = _row(osm_way=1226630002)
    assert row["osm_way_id"] == 1226630002
    assert row["osm_relation_id"] is None


def test_a_way_id_never_lands_in_the_relation_field():
    # The app computes an Overpass area id as osmId + 3_600_000_000, the
    # RELATION-only offset. A way id in that field points it at a different
    # polygon entirely, which is why seed-areas.py dropped it rather than
    # storing it there.
    row = _row(osm_way=1226630002)
    assert row["osm_relation_id"] != 1226630002


def test_a_relation_wins_and_no_way_id_is_written():
    # A relation id is strictly better: the app can use it AND so can the gate.
    row = _row(osm_rel=14195507, osm_way=1226630002)
    assert row["osm_relation_id"] == 14195507
    assert "osm_way_id" not in row


def test_an_area_with_neither_id_is_unchanged():
    row = _row()
    assert row["osm_relation_id"] is None
    assert "osm_way_id" not in row


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
