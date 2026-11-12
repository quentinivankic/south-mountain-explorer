"""Tests for the warn-only gaps/overlaps audit. Run: python3 scripts/test_audit_gaps_overlaps.py"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("audit_gaps_overlaps", _HERE / "audit-gaps-overlaps.py")
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)

# 0.0001 deg latitude is about 11.1 m.
A, B, C = [40.0, -105.0], [40.001, -105.0], [40.002, -105.0]
FAR = [40.01, -105.0]


def T(tid, name, segments, mi=1.0):
    return {"id": tid, "name": name, "distanceMi": mi, "difficulty": "Easy", "segments": segments}


def test_gap_under_limit_between_two_pieces_of_one_name_is_reported():
    near = [40.0015, -105.0]  # ~55 m past B
    trails = [T("ridge", "Ridge Trail", [[A, B], [near, C]])]
    gaps = audit.same_name_gaps(trails)
    assert len(gaps) == 1
    assert 50 < gaps[0]["gap_m"] < 60
    assert gaps[0]["name"] == "Ridge Trail" and gaps[0]["trail_ids"] == ["ridge"]


def test_gap_across_two_rows_with_the_same_name_is_reported():
    trails = [T("ridge", "Ridge Trail", [[A, B]]), T("ridge-1", "ridge  trail", [[[40.0011, -105.0], C]])]
    gaps = audit.same_name_gaps(trails)
    assert len(gaps) == 1 and gaps[0]["trail_ids"] == ["ridge", "ridge-1"]
    assert 10 < gaps[0]["gap_m"] < 12


def test_touching_pieces_and_far_pieces_are_not_gaps():
    touching = [T("x", "X", [[A, B], [B, C]])]
    t_junction = [T("x", "X", [[A, C], [B, [40.001, -104.999]]])]
    far = [T("x", "X", [[A, B], [FAR, [40.011, -105.0]]])]
    assert audit.same_name_gaps(touching) == []
    assert audit.same_name_gaps(t_junction) == []
    assert audit.same_name_gaps(far) == []
    assert len(audit.same_name_gaps(far, max_gap_m=2000)) == 1


def test_different_names_never_make_a_gap():
    trails = [T("a", "A", [[A, B]]), T("b", "B", [[[40.0011, -105.0], C]])]
    assert audit.same_name_gaps(trails) == []


def test_one_gap_per_pair_of_unconnected_groups():
    near = [40.0011, -105.0]
    trails = [T("x", "X", [[A, B], [B, [40.001, -104.999]], [near, C]])]
    gaps = audit.same_name_gaps(trails)
    assert len(gaps) == 1


def test_overlap_reports_shared_miles_and_distinct_ground():
    loop = T("loop", "Loop", [[A, B, C]], 0.14)
    spur = T("spur", "Spur", [[C, B]], 0.07)
    found = audit.overlaps([loop, spur])
    assert len(found) == 1
    assert found[0]["trails"] == ["Loop", "Spur"] and found[0]["same_name"] is False
    assert abs(found[0]["shared_mi"] - 0.07) < 0.005
    report = audit.audit_area({"id": "x", "total_mi": 0.2, "trails": [loop, spur]})
    assert report["distinct_mi"] == 0.1
    assert report["overlaps"] == found


def test_tiny_overlaps_are_left_out():
    short = [40.0, -105.0], [40.00005, -105.0]  # ~5.6 m
    trails = [T("a", "A", [list(short)]), T("b", "B", [list(short)])]
    assert audit.overlaps(trails) == []


def _write_geom(root: Path) -> Path:
    geom = root / "geom"
    geom.mkdir()
    (geom / "x-az.json").write_text(json.dumps({
        "id": "x-az", "total_mi": 0.2,
        "trails": [T("loop", "Loop", [[A, B, C]], 0.14), T("spur", "Spur", [[C, B]], 0.07),
                   T("ridge", "Ridge", [[A, [40.0, -105.001]], [[40.0, -105.0015], [40.0, -105.003]]])]}))
    (geom / "broken.json").write_text("{not json")
    return geom


def _digest(directory: Path) -> dict:
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(directory.iterdir())}


def test_cli_is_warn_only_and_never_touches_its_input():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        geom = _write_geom(root)
        before = _digest(geom)
        out = root / "report.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = audit.main(["--geom-dir", str(geom), "--json", str(out)])
        assert rc == 0
        assert _digest(geom) == before
        text = buf.getvalue()
        assert "WARN-ONLY audit of 1 areas" in text
        assert "same-name gaps under 200 m: 1 in 1 areas" in text
        assert "overlapping trail pairs (>= 0.01 mi shared): 1 in 1 areas" in text
        assert "unreadable files skipped: 1" in text
        report = json.loads(out.read_text())
        assert report["areas"][0]["same_name_gaps"][0]["name"] == "Ridge"


def test_cli_refuses_to_write_its_report_into_public_areas_or_the_geom_dir():
    for target in (audit.PUBLIC_AREAS / "audit.json", audit.DEFAULT_GEOM / "audit.json"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                audit.main(["--json", str(target)])
            except SystemExit as stop:
                assert stop.code == 2
            else:
                raise AssertionError("expected a usage error")
        assert not target.exists()
    with tempfile.TemporaryDirectory() as tmp:
        geom = _write_geom(Path(tmp))
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                audit.main(["--geom-dir", str(geom), "--json", str(geom / "r.json")])
            except SystemExit as stop:
                assert stop.code == 2
        assert not (geom / "r.json").exists()


def test_summary_names_the_areas_smallest_gap():
    with tempfile.TemporaryDirectory() as tmp:
        geom = Path(tmp) / "geom"
        geom.mkdir()
        wide = [[A, B], [[40.0015, -105.0], C]]                          # ~55 m, name "Aaa"
        tight = [[[41.0, -105.0], [41.001, -105.0]], [[41.0011, -105.0], [41.002, -105.0]]]  # ~11 m
        (geom / "y.json").write_text(json.dumps({"id": "y", "total_mi": 1, "trails": [
            T("aaa", "Aaa", wide), T("zzz", "Zzz", tight)]}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert audit.main(["--geom-dir", str(geom)]) == 0
        assert "y: 2 gaps, smallest 11.1 m on 'Zzz'" in buf.getvalue()


def test_missing_area_is_reported_not_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        geom = _write_geom(Path(tmp))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert audit.main(["--geom-dir", str(geom), "--area", "nope"]) == 0
        assert "unreadable files skipped: 1" in buf.getvalue()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
