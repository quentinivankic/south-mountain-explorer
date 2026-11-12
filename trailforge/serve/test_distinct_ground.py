"""Tests for distinct-ground miles. Run: python3 trailforge/serve/test_distinct_ground.py"""
import importlib.util
import json
import math
import os
import sys
import tempfile
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
import degenerate  # noqa: E402
import distinct_ground as dg  # noqa: E402


def _hav(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 3958.8 * math.asin(min(1.0, math.sqrt(h)))


def T(tid, segments, mi=None, name=None):
    t = {"id": tid, "name": name or tid.title(), "difficulty": "Easy", "segments": segments}
    if mi is not None:
        t["distanceMi"] = mi
    return t


A, B, C, D = [40.0, -105.0], [40.01, -105.0], [40.02, -105.0], [40.02, -105.01]
AB, BC, CD = _hav(A, B), _hav(B, C), _hav(C, D)


def test_no_shared_ground_equals_area_miles_exactly():
    trails = [T("one", [[A, B]], 0.69), T("two", [[C, D]], 0.53), T("three", [[B, C]], 0.7)]
    assert dg.shared_miles(trails) == 0.0
    assert dg.distinct_miles(trails) == degenerate.area_miles(trails) == 1.9


def test_two_trails_on_one_path_count_it_once():
    loop = T("loop", [[A, B, C]], 1.38)
    spur = T("spur", [[A, B]], 0.69)
    assert abs(dg.shared_miles([loop, spur]) - AB) < 1e-12
    assert dg.distinct_miles([loop, spur]) == round(1.38 + 0.69 - AB, 1) == 1.4
    assert degenerate.area_miles([loop, spur]) == 2.1


def test_direction_does_not_matter():
    forward = T("forward", [[A, B, C]], 1.38)
    backward = T("backward", [[C, B, A]], 1.38)
    assert abs(dg.shared_miles([forward, backward]) - (AB + BC)) < 1e-12
    assert dg.distinct_miles([forward, backward]) == round(2.76 - AB - BC, 1)


def test_one_trail_drawing_its_own_ground_twice_counts_once():
    out_and_back = T("out-and-back", [[A, B], [B, A]], 1.38)
    assert abs(dg.shared_miles([out_and_back]) - AB) < 1e-12


def test_partial_overlap_counts_only_the_shared_edge():
    long_way = T("long", [[A, B, C, D]], 1.91)
    middle = T("middle", [[B, C]], 0.69)
    assert abs(dg.shared_miles([long_way, middle]) - BC) < 1e-12


def test_missing_distance_falls_back_to_geometry_like_area_miles():
    trails = [T("one", [[A, B]]), T("two", [[C, D]])]
    assert dg.distinct_miles(trails) == degenerate.area_miles(trails)


def test_caller_total_is_used_and_kept_when_nothing_is_shared():
    trails = [T("one", [[A, B]], 0.69)]
    assert dg.distinct_miles(trails, total=0.7349) == round(0.7349, 1)
    shared = [T("one", [[A, B]], 0.69), T("two", [[A, B]], 0.69)]
    assert dg.distinct_miles(shared, total=1.38) == round(1.38 - AB, 1)


def test_bad_points_and_empty_input_do_not_crash():
    trails = [T("empty", []), T("point", [[A]]), T("stub", [[A, A]]), T("short", [[[40.0], B]])]
    assert dg.shared_miles(trails) == 0.0
    assert dg.distinct_miles([]) == 0.0
    assert dg.shared_miles([{"id": "no-segments"}]) == 0.0


def test_never_negative():
    trails = [T("one", [[A, B]], 0.0), T("two", [[A, B]], 0.0)]
    assert dg.distinct_miles(trails) == 0.0


def test_with_distinct_mi_is_a_copy_with_the_field_last():
    row = {"id": "x", "trails": [T("loop", [[A, B, C]], 1.38), T("spur", [[A, B]], 0.69)],
           "trail_count": 2, "total_mi": 2.1, "osm_relation_id": 7}
    before = json.dumps(row)
    out = dg.with_distinct_mi(row)
    assert json.dumps(row) == before
    assert list(out) == list(row) + [dg.FIELD]
    assert {k: v for k, v in out.items() if k != dg.FIELD} == row
    assert out[dg.FIELD] == 1.4
    assert out["total_mi"] == 2.1


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), os.path.join(_ROOT, "scripts", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sweep(name, document, drop_trail, **extra):
    """Run one sweep's `_run` (no geom_writer lock) on a temp copy; return (doc, module)."""
    module = _load_script(name)
    with tempfile.TemporaryDirectory() as tmp:
        geom_dir = os.path.join(tmp, "geom")
        os.mkdir(geom_dir)
        path = os.path.join(geom_dir, "area-x-az.json")
        with open(path, "w") as f:
            json.dump(document, f)
        index = os.path.join(tmp, "index.json")
        with open(index, "w") as f:
            json.dump([["area-x-az", "X", "Arizona", 40.0, -105.0, 3, 9.9]], f)
        args = SimpleNamespace(geom_dir=geom_dir, index=index, dry_run=False, **extra)
        if "sidecar" in extra:
            sidecar = os.path.join(tmp, "sidecar.json")
            with open(sidecar, "w") as f:
                json.dump({"area-x-az": {drop_trail: {"reason": "test"}}}, f)
            args.sidecar = sidecar
        devnull = open(os.devnull, "w")
        stdout, sys.stdout = sys.stdout, devnull
        try:
            assert module._run(args) == 0
        finally:
            sys.stdout = stdout
            devnull.close()
        with open(path) as f:
            return json.load(f), module


def _area(drop, with_field):
    # loop + spur share A-B; `drop` is the trail the sweep removes.
    doc = {"id": "area-x-az", "trails": [T("loop", [[A, B, C]], 1.38), T("spur", [[A, B]], 0.69), drop],
           "trail_count": 3, "total_mi": 9.9}
    if with_field:
        doc[dg.FIELD] = 99.0
    return doc


def test_sweeps_refresh_distinct_mi_only_when_present():
    cases = [
        ("sweep-nonhiking-trails", T("road", [[C, D]], 0.53), {"sidecar": True}),
        ("sweep-degenerate-trails", T("stub", [[[41.0, -84.0], [41.0, -84.0]]], 0.0), {}),
        ("sweep-geom-names", T("closed", [[C, D]], 0.53, name="Closed Trail"), {}),
    ]
    for name, drop, extra in cases:
        refreshed, module = _sweep(name, _area(drop, True), drop["id"], **extra)
        assert [t["id"] for t in refreshed["trails"]] == ["loop", "spur"], name
        assert refreshed[dg.FIELD] == dg.distinct_miles(
            refreshed["trails"],
            total=(sum(module._miles(t["segments"]) for t in refreshed["trails"])
                   if name == "sweep-geom-names" else None)), name
        assert refreshed[dg.FIELD] < refreshed["total_mi"], name
        untouched, _ = _sweep(name, _area(drop, False), drop["id"], **extra)
        assert dg.FIELD not in untouched, name


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
