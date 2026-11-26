#!/usr/bin/env python3
"""Registry side of the Canada whole_route opt-in: validation (load_registry), rival consistency across the
registry, the qualify report carrying the decisions, and the ca-run-area.sh wiring (registry-gated, relation-backed
only). The engine itself is tested in trailforge/assemble/test_ca_whole_route.py."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
from test_ca_run_area import _stub_run  # noqa: E402
ROW = {"name": "Long Ridge Trail", "source": "https://parks.example.gc.ca/long-ridge",
       "reason": "agency-listed signed route that leaves the polygon"}
RIVAL = {"osm": "r4242", "name": "Area 1"}
def _load(tmp_path, *entries):
    return ca.load_registry(_registry(tmp_path, list(entries)))
def test_no_key_loads_unchanged(tmp_path):
    assert _load(tmp_path, _entry(0))
def test_valid_entry_and_rival_load(tmp_path):
    reg = _load(tmp_path, _entry(0, whole_route=[{**ROW, "rivals": [RIVAL]}]), _entry(1, relation_id=4242))
    assert reg["by_id"]["area-000-ca-ns"]["whole_route"][0]["rivals"] == [RIVAL]
@pytest.mark.parametrize("bad", [
    [], {}, "x", [{**ROW, "extra": 1}], [{**ROW, "source": "http://x.example/y"}], [{k: v for k, v in ROW.items() if k != "reason"}],
    [ROW, ROW], [{**ROW, "rivals": []}], [{**ROW, "min_share": 0.5}],
])
def test_malformed_whole_route_is_rejected(tmp_path, bad):
    with pytest.raises(ca.CaError, match="whole_route"):
        _load(tmp_path, _entry(0, whole_route=bad))
def test_way_backed_entry_and_way_backed_rival_are_accepted(tmp_path):
    way = _entry(0, whole_route=[{**ROW, "rivals": [{"osm": "w5", "name": "Area 1"}]}])
    way["way_id"] = 7
    del way["relation_id"]
    other = _entry(1)
    other["way_id"] = 5
    del other["relation_id"]
    assert _load(tmp_path, way, other)
    with pytest.raises(ca.CaError, match="is not a registry area with that name"):
        _load(tmp_path, way, _entry(1, relation_id=5))                      # "w5" is not relation 5
def test_rival_must_be_another_registry_area_with_that_exact_name(tmp_path):
    with_rival = _entry(0, whole_route=[{**ROW, "rivals": [RIVAL]}])
    with pytest.raises(ca.CaError, match="is not a registry area with that name"):
        _load(tmp_path, with_rival)                                                  # no such relation in the registry
    with pytest.raises(ca.CaError, match="is not a registry area with that name"):
        _load(tmp_path, with_rival, _entry(1, relation_id=4242, name="Other Name"))   # same id, other name
    assert _load(tmp_path, with_rival, _entry(1, relation_id=4242))                    # mutation: consistent passes
def test_an_area_cannot_be_its_own_rival(tmp_path):
    own = _entry(0, whole_route=[{**ROW, "rivals": [{"osm": "r4242", "name": "Area 0"}]}], relation_id=4242)
    with pytest.raises(ca.CaError, match="own rival"):
        _load(tmp_path, own)
def test_a_trail_built_by_another_opt_in_cannot_also_be_whole_route(tmp_path):
    built = {"name": ROW["name"], "way_ids": [1], "source": "https://x.example/y", "reason": "r"}
    with pytest.raises(ca.CaError, match="build"):
        _load(tmp_path, _entry(0, whole_route=[ROW], adopt_ways=[built]))
    assert _load(tmp_path, _entry(0, whole_route=[ROW], adopt_ways=[{**built, "name": "Another Trail"}]))
def test_report_carries_the_whole_route_decisions(tmp_path):
    area = {"id": "x-ca-bc", "name": "X", "province": "bc", "relation_id": 1, "boundary_bbox": None,
            "effective_bbox": None, "bbox_source": "registry", "center": [49.0, -123.0]}
    geom = {"trails": [], "trail_count": 0, "total_mi": 0}
    evidence = {"decisions": [{"name": ROW["name"], "share": 0.8}], "failures": []}
    def run(assembly):
        paths = {}
        for key, value in (("area", area), ("geom", geom), ("assembly", assembly)):
            paths[key] = tmp_path / f"{key}.json"
            paths[key].write_text(json.dumps(value))
        for key in ("trails", "removed"):
            paths[key] = tmp_path / f"{key}.geojson"
            paths[key].write_text(json.dumps({"features": []}))
        index = tmp_path / "index.json"
        index.write_text("[]")
        out = tmp_path / "report.json"
        args = ca.argparse.Namespace(area_json=paths["area"], geom=paths["geom"], trails_geojson=paths["trails"],
                                     removed_geojson=paths["removed"], assembly_json=paths["assembly"],
                                     live_index=index, live_geom_dir=tmp_path, expected=None, out=out)
        assert ca.report_area(args) == 0
        return json.loads(out.read_text())
    assert run({"ca_whole_route": evidence})["ca_whole_route"] == evidence
    assert "ca_whole_route" not in run({})                                             # control: absent stays absent
def test_run_area_way_backed_accepts_only_whole_route(tmp_path):
    import os
    import subprocess
    reg = tmp_path / "trailforge/golden"
    reg.mkdir(parents=True)
    (reg / "ca-areas.json").write_text(json.dumps({"areas": [
        {"id": "x-ca-ns", "whole_route": [ROW], "accept_gap": [{"relation": "r1", "reason": "x"}]},
        {"id": "y-ca-ns", "whole_route": [ROW]}]}))
    env = {**os.environ, "AREA_ID": "x-ca-ns", "AREA_NAME": "X", "BBOX": "-63,45,-62,46", "W": str(tmp_path / "w"),
           "RELATION_ID": "", "WAY_ID": "5"}
    run = lambda e: subprocess.run(["bash", str(Path(ca.__file__).parent / "ca-run-area.sh")], env=e, cwd=tmp_path,
                                   capture_output=True, text=True)
    refused = run(env)                                      # accept_gap on a way-backed area is still refused
    assert refused.returncode == 2 and "accept_gap must be a non-empty list on a relation-backed area" in refused.stderr
    only = run({**env, "AREA_ID": "y-ca-ns"})               # whole_route alone passes the gate (fails later, no area files)
    assert "SAFETY BLOCK" not in only.stderr
def test_run_area_passes_the_flag_only_when_listed(tmp_path):
    plain, _ = _stub_run(tmp_path / "plain", {})
    assert "--ca-whole-route" not in plain and "--region" not in plain
    listed, work = _stub_run(tmp_path / "listed", {"whole_route": [ROW]})
    assert listed.count("--region ca") == 1 and f"--ca-whole-route {work}/ca-whole_route.json" in listed
    assert json.loads((work / "ca-whole_route.json").read_text()) == [ROW]
