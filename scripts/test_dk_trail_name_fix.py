#!/usr/bin/env python3
"""Tests for scripts/dk_trail_name_fix.py (registry trail_name_fixes, Denmark only)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_trail_name_fix as fix  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dk_trail_name_fix.py"
SRC = "https://naturstyrelsen.dk/example"


def _feature(name, **extra):
    return {"type": "Feature", "properties": {"name": name, "length_mi": 1.0, **extra},
            "geometry": {"type": "MultiLineString", "coordinates": [[[9.0, 56.0], [9.1, 56.1]]]}}


def _fix(old, new):
    return {"osm_name": old, "name": new, "source": SRC, "reason": "typo"}


def test_rename_keeps_the_osm_name_so_the_trail_id_does_not_move():
    fc = {"type": "FeatureCollection", "features": [_feature("Ranbøl rute"), _feature("Gul Rute")]}
    rows = fix.apply_fixes(fc, [_fix("Ranbøl rute", "Randbøl rute")])
    assert [f["properties"]["name"] for f in fc["features"]] == ["Randbøl rute", "Gul Rute"]
    assert fc["features"][0]["properties"]["osm_name"] == "Ranbøl rute"
    assert "osm_name" not in fc["features"][1]["properties"]
    assert rows == [{"osm_name": "Ranbøl rute", "from": "Ranbøl rute", "name": "Randbøl rute", "source": SRC}]


def test_an_existing_prefix_osm_name_is_kept():
    fc = {"features": [_feature("Almindingen Gul", osm_name="Gul")]}
    fix.apply_fixes(fc, [_fix("Almindingen Gul", "Almindingen Gul Tur")])
    assert fc["features"][0]["properties"]["osm_name"] == "Gul"           # id still derives from the OSM name


def test_ids_from_to_app_json_are_unchanged_by_a_fix():
    sys.path.insert(0, str(ROOT / "trailforge" / "serve"))
    sys.path.insert(0, str(ROOT / "trailforge" / "assemble"))
    import to_app_json
    def ids(fc):
        row = to_app_json.convert(fc, "x-dk", "X", "Denmark", (56.0, 9.0), 1, {"trail"})
        return [t["id"] for t in row["trails"]]
    before = {"type": "FeatureCollection", "features": [_feature("Ranbøl rute"), _feature("Ranbøl rute 2")]}
    after = json.loads(json.dumps(before))
    fix.apply_fixes(after, [_fix("Ranbøl rute", "Randbøl rute"), _fix("Ranbøl rute 2", "Randbøl rute 2")])
    assert ids(before) == ids(after)


@pytest.mark.parametrize("names", [["A"], ["A", "Gul", "Gul"], ["B", "B2"]])
def test_fails_closed_unless_exactly_one_trail_matches(names):
    fc = {"features": [_feature(n) for n in names]}
    snapshot = json.dumps(fc)
    fixes = [_fix("Gul", "Nordskov Gul")] if "Gul" in names else [_fix("Missing", "Other")]
    with pytest.raises(fix.FixError):
        fix.apply_fixes(fc, fixes)
    assert json.dumps(fc) == snapshot


def test_refuses_a_name_that_equals_another_trail():
    fc = {"features": [_feature("Gul Rute"), _feature("Nordskov Gul")]}
    with pytest.raises(fix.FixError):
        fix.apply_fixes(fc, [_fix("Gul Rute", "Nordskov Gul")])


@pytest.mark.parametrize("bad", [
    [], "x", [{"osm_name": "A"}], [_fix("A", "A")], [{**_fix("A", "B"), "source": "http://x.dk"}],
    [{**_fix("A", "B"), "extra": 1}], [_fix("A", "B"), _fix("A", "C")], [_fix("A", "C"), _fix("B", "C")],
    [_fix("A", "B\u0007")], [_fix("A", "e\u0301")], [_fix("A", "x" * 121)], [_fix("A", " B")],
])
def test_validate_fixes_rejects(bad):
    with pytest.raises(fix.FixError):
        fix.validate_fixes(bad)


def test_cli_rewrites_in_place_and_writes_a_report(tmp_path):
    trails, fixes, report = tmp_path / "t.geojson", tmp_path / "f.json", tmp_path / "r.json"
    trails.write_text(json.dumps({"type": "FeatureCollection", "features": [_feature("Skovstien")]}), encoding="utf-8")
    fixes.write_text(json.dumps([_fix("Skovstien", "Sindal Skovstien")]), encoding="utf-8")
    run = subprocess.run([sys.executable, str(SCRIPT), "--trails", str(trails), "--fixes", str(fixes),
                          "--report", str(report)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    props = json.loads(trails.read_text(encoding="utf-8"))["features"][0]["properties"]
    assert props["name"] == "Sindal Skovstien" and props["osm_name"] == "Skovstien"
    assert json.loads(report.read_text(encoding="utf-8"))["renamed"][0]["name"] == "Sindal Skovstien"


def test_cli_blocks_and_leaves_the_file_untouched_on_a_mismatch(tmp_path):
    trails, fixes = tmp_path / "t.geojson", tmp_path / "f.json"
    body = json.dumps({"type": "FeatureCollection", "features": [_feature("Other")]})
    trails.write_text(body, encoding="utf-8")
    fixes.write_text(json.dumps([_fix("Skovstien", "Sindal Skovstien")]), encoding="utf-8")
    run = subprocess.run([sys.executable, str(SCRIPT), "--trails", str(trails), "--fixes", str(fixes),
                          "--report", str(tmp_path / "r.json")], capture_output=True, text=True)
    assert run.returncode == 2 and "SAFETY BLOCK" in run.stderr
    assert trails.read_text(encoding="utf-8") == body


def test_run_area_script_calls_the_fixer_only_when_the_registry_has_fixes():
    text = (ROOT / "scripts" / "dk-run-area.sh").read_text(encoding="utf-8")
    assert "trail_name_fixes // empty" in text
    assert 'if [ -n "$NAME_FIXES" ]; then' in text
    call = text.index("python3 ../scripts/dk_trail_name_fix.py")
    assert text.index("python3 assemble/assemble.py") < call < text.index("python3 serve/publish_areas.py")
