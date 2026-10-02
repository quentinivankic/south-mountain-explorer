#!/usr/bin/env python3
"""iOS-bundle filter tests for filter-ios-bundle.py (change #4, no network).
Denmark joins the bundle via the explicit BUNDLED_COUNTRY_CODES set and still
passes through the unchanged _clean_geom gate. A US control row proves the NA
path is byte-identical in behavior."""
import importlib.util
import json
import tempfile
from pathlib import Path

_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "fib", _dir / "filter-ios-bundle.py")
fib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fib)


def _write_geom(slug: str, payload: dict) -> None:
    (fib.GEOM_DIR / f"{slug}.json").write_text(json.dumps(payload))


def _set_tmp_geom_dir() -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    fib.GEOM_DIR = Path(tmp.name)
    return tmp


_CLEAN = {
    "name": "X", "trails": [{"name": "Blue Loop", "distanceMi": 1.0}],
    "trail_count": 1, "total_mi": 1.0,
}

# A row shape matching the master index: [slug, name, state, lat, lon, count, mi].
_DK_SLUG = "nationalpark-mols-bjerge-dk"
_AZ_SLUG = "pinnacle-peak-park-az"


def _row(slug, count=0, mi=0.0):
    return [slug, "X", "Denmark", 56.2, 10.5, count, mi]


# ---- change #4: DK bundled + gated by _clean_geom ----

def test_dk_in_bundled_region_codes():
    assert "DK" in fib.BUNDLED_REGION_CODES


def test_dk_in_bundled_country_codes():
    assert fib.BUNDLED_COUNTRY_CODES == {"DK"}


def test_code_from_slug_dk():
    assert fib.code_from_slug(_DK_SLUG) == "DK"


def test_clean_dk_geom_is_kept():
    tmp = _set_tmp_geom_dir()
    try:
        _write_geom(_DK_SLUG, _CLEAN)
        kept, dropped = fib.filter_rows([_row(_DK_SLUG)])
        assert len(kept) == 1
        assert kept[0][0] == _DK_SLUG
        # count/mi taken from the geom (authoritative for published areas).
        assert kept[0][5] == 1 and kept[0][6] == 1.0
    finally:
        tmp.cleanup()


def test_dk_geom_with_cached_at_is_dropped():
    tmp = _set_tmp_geom_dir()
    try:
        _write_geom(_DK_SLUG, {**_CLEAN, "cached_at": "2026-01-01"})
        kept, dropped = fib.filter_rows([_row(_DK_SLUG)])
        assert kept == []
        assert dropped.get("DK") == 1
    finally:
        tmp.cleanup()


def test_dk_geom_with_empty_trails_is_dropped():
    tmp = _set_tmp_geom_dir()
    try:
        _write_geom(_DK_SLUG, {**_CLEAN, "trails": []})
        kept, _ = fib.filter_rows([_row(_DK_SLUG)])
        assert kept == []
    finally:
        tmp.cleanup()


def test_dk_geom_with_unnamed_trail_is_dropped():
    tmp = _set_tmp_geom_dir()
    try:
        _write_geom(_DK_SLUG, {**_CLEAN,
                               "trails": [{"name": "Unnamed 12345"}]})
        kept, _ = fib.filter_rows([_row(_DK_SLUG)])
        assert kept == []
    finally:
        tmp.cleanup()


def test_dk_geom_missing_file_is_dropped():
    tmp = _set_tmp_geom_dir()
    try:
        kept, _ = fib.filter_rows([_row(_DK_SLUG)])   # no geom written
        assert kept == []
    finally:
        tmp.cleanup()


# ---- control: US path byte-identical in behavior ----

def test_clean_us_geom_is_kept_with_geom_counts():
    tmp = _set_tmp_geom_dir()
    try:
        _write_geom(_AZ_SLUG, {**_CLEAN, "trail_count": 7, "total_mi": 12.5})
        row = [_AZ_SLUG, "Pinnacle Peak Park", "Arizona", 33.7, -111.8, 0, 0.0]
        kept, dropped = fib.filter_rows([row])
        assert len(kept) == 1
        assert kept[0][0] == _AZ_SLUG
        assert kept[0][5] == 7 and kept[0][6] == 12.5
        assert dropped == {}
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
