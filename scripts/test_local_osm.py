#!/usr/bin/env python3
"""Unit tests for the local OSM cache reader (no network, no extracts)."""
import importlib.util
import json
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


lo = _load("local_osm", "_local_osm.py")
builder = _load("build_local_osm_cache", "build-local-osm-cache.py")


def _cache(tmp: Path, parking_rows=None, gate=None, boundaries=None):
    if parking_rows is not None:
        with open(tmp / "parking.jsonl", "w") as fh:
            fh.write(json.dumps({"_meta": {"version": 1, "source": "nope.pbf",
                                           "stamp": "x"}}) + "\n")
            for r in parking_rows:
                fh.write(json.dumps(r) + "\n")
    if gate is not None:
        (tmp / "road-gate.json").write_text(json.dumps(
            {"version": 1, "gate_m": 250, "source": "nope.pbf", "stamp": "x",
             "verdicts": gate}))
    if boundaries is not None:
        with open(tmp / "boundaries.geojsonseq", "w") as fh:
            for line in boundaries:
                fh.write(json.dumps(line) + "\n")
    # strict=False: freshness is checked against a real extract, which a unit
    # test has no business needing.
    return lo.LocalOSM(cache_dir=tmp, strict=False)


def test_a_way_is_emitted_with_center_not_bare_latlon():
    # add-parking's `_point()` reads top-level lat/lon for NODES and `center`
    # for everything else. A way carrying only lat/lon reads as having no
    # position, which silently reduced Vermont's 8,074 cached lots to the 430
    # that happened to be nodes.
    with tempfile.TemporaryDirectory() as d:
        c = _cache(Path(d), parking_rows=[
            {"type": "way", "lat": 44.0, "lon": -72.9,
             "tags": {"amenity": "parking"}},
            {"type": "node", "lat": 44.1, "lon": -72.8,
             "tags": {"amenity": "parking"}}])
        els = c.parking_elements([-73.5, 43.0, -72.0, 45.0])["elements"]
        way = next(e for e in els if e["type"] == "way")
        node = next(e for e in els if e["type"] == "node")
        assert way["center"] == {"lat": 44.0, "lon": -72.9}
        assert "lat" not in way
        assert node["lat"] == 44.1 and "center" not in node


def test_bbox_filter_excludes_features_outside():
    with tempfile.TemporaryDirectory() as d:
        c = _cache(Path(d), parking_rows=[
            {"type": "node", "lat": 44.0, "lon": -72.9, "tags": {"amenity": "parking"}},
            {"type": "node", "lat": 20.0, "lon": -99.0, "tags": {"amenity": "parking"}}])
        els = c.parking_elements([-73.5, 43.0, -72.0, 45.0])["elements"]
        assert len(els) == 1 and els[0]["lat"] == 44.0


def test_empty_parking_cache_raises_rather_than_answering_nothing():
    # "no parking in this state" and "the cache is empty" must not look alike —
    # the first writes, the second must skip the state.
    with tempfile.TemporaryDirectory() as d:
        c = _cache(Path(d), parking_rows=[])
        try:
            c.parking_elements([-73.5, 43.0, -72.0, 45.0])
        except lo.LocalCacheError:
            return
        raise AssertionError("an empty cache must raise, not return []")


def test_road_gate_reports_unknown_points_instead_of_guessing():
    # A point the cache has never seen means the cache predates this ArcGIS
    # answer. Guessing either way invents road access or deletes a trailhead.
    with tempfile.TemporaryDirectory() as d:
        c = _cache(Path(d), gate={"44.000000,-72.900000": 1,
                                  "44.100000,-72.800000": 0})
        fed = [{"lat": 44.0, "lon": -72.9}, {"lat": 44.1, "lon": -72.8},
               {"lat": 45.5, "lon": -71.0}]
        kept, unknown = c.road_gate(fed, lambda f: f"{f['lat']:.6f},{f['lon']:.6f}")
        assert [f["lat"] for f in kept] == [44.0]
        assert [f["lat"] for f in unknown] == [45.5]


def test_boundaries_map_area_ids_back_to_their_way_or_relation():
    # osmium area ids encode the source: odd = relation, (id-1)//2; even = closed
    # way, id//2. BOTH are boundaries — a park stored as one closed way is why
    # 6,151 areas had no boundary id at all.
    ring = [[-73.0, 44.0], [-72.0, 44.0], [-72.0, 45.0], [-73.0, 45.0], [-73.0, 44.0]]
    with tempfile.TemporaryDirectory() as d:
        c = _cache(Path(d), boundaries=[
            {"id": "a2469", "geometry": {"type": "Polygon", "coordinates": [ring]}},
            {"id": "a2468", "geometry": {"type": "Polygon", "coordinates": [ring]}}])
        got = c.boundaries([("relation", 1234), ("way", 1234), ("way", 999)])
        assert sorted(got) == [("relation", 1234), ("way", 1234)]
        assert got[("relation", 1234)][0][0] == (-73.0, 44.0)   # (lon, lat)


def test_canonical_id_collapses_the_polygon_copy_onto_its_way():
    # `--add-unique-id=type_id` labels the polygon copy of a closed way a<2*wid>
    # and the linestring copy w<wid>, so deduping on the raw id removes nothing.
    assert builder._canonical_id("a2468") == "w1234"
    assert builder._canonical_id("a2469") == "r1234"
    assert builder._canonical_id("w1234") == "w1234"
    assert builder._canonical_id("n77") == "n77"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
