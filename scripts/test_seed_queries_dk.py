#!/usr/bin/env python3
"""Overpass / bbox query-shape tests for seed-areas.py (no network). Pins the
exact ISO shape per change #1: DK uses ISO3166-1 (not US-DK) and admin_level
2; US/Canada branches unchanged (controls)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sa", Path(__file__).resolve().parent / "seed-areas.py")
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)


# ---- change #1 wiring: DK ----

def test_overpass_query_dk_uses_iso3166_1():
    q = sa.overpass_query("DK")
    assert '"ISO3166-1"="DK"' in q


def test_overpass_query_dk_is_not_us_prefixed():
    assert "US-DK" not in sa.overpass_query("DK")


def test_region_bbox_query_dk_uses_country_admin_level_2():
    q = sa.region_bbox_query("DK")
    assert '"ISO3166-1"="DK"' in q
    assert '"admin_level"="2"' in q
    assert "US-DK" not in q


# ---- controls: US state branch unchanged ----

def test_overpass_query_az_uses_us_prefixed_iso3166_2():
    q = sa.overpass_query("AZ")
    assert '"ISO3166-2"="US-AZ"' in q


def test_region_bbox_query_az_uses_admin_level_4():
    q = sa.region_bbox_query("AZ")
    assert '"admin_level"="4"' in q
    assert "US-AZ" in q


# ---- controls: Canada hyphen branch unchanged ----

def test_overpass_query_ca_qc_uses_bare_iso3166_2():
    q = sa.overpass_query("CA-QC")
    assert '"ISO3166-2"="CA-QC"' in q
    assert "US-" not in q


def test_region_bbox_query_ca_qc_uses_admin_level_4():
    q = sa.region_bbox_query("CA-QC")
    assert '"ISO3166-2"="CA-QC"' in q
    assert '"admin_level"="4"' in q


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
