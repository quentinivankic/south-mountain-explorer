#!/usr/bin/env python3
"""Registry side of the Canada container_from_routes opt-in: validation (load_registry), prepare-areas (the container
bbox and the anchor-route identity check), check-boundary-unique and the ca-run-area.sh wiring (registry-gated; absent
= no flag). The engine is tested in trailforge/assemble/test_ca_route_container.py and the publisher in
trailforge/serve/test_publish_route_container.py."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
SCRIPT = Path(ca.__file__).parent / "ca-run-area.sh"
CFG = {"anchor_name": "Coast Trail", "way_names": ["Alpha Path", "Beta Path"], "max_span_deg": 0.5,
       "source": "https://example.ca/paths", "reason": "each path is its own listed hike"}
OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="t">
  <node id="1" lat="47.30" lon="-52.80"/><node id="2" lat="47.30" lon="-52.78"/>
  <node id="3" lat="47.31" lon="-52.80"/><node id="4" lat="47.31" lon="-52.78"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><tag k="name" v="Alpha Path"/><tag k="highway" v="path"/></way>
  <way id="11"><nd ref="3"/><nd ref="4"/><tag k="name" v="{second}"/><tag k="highway" v="path"/></way>
  <relation id="{rel}"><member type="way" ref="10" role=""/><member type="way" ref="11" role=""/>
    <tag k="type" v="{rtype}"/><tag k="route" v="{route}"/><tag k="name" v="{name}"/></relation>
</osm>
"""
def _osm(tmp_path, rel=1000, name="Coast Trail", route="hiking", rtype="route", second="Beta Path", label="b"):
    path = tmp_path / f"{label}{rel}.osm"
    path.write_text(OSM.format(rel=rel, name=name, route=route, rtype=rtype, second=second), encoding="utf-8")
    return path
def _container(**over):
    return _entry(0, bbox=None, center=None, container_from_routes={**CFG, **over})
def _load(tmp_path, *entries):
    return ca.load_registry(_registry(tmp_path, list(entries)))
# --------------------------------------------------------------- registry --
def test_no_key_loads_unchanged(tmp_path):
    assert _load(tmp_path, _entry(0))
def test_valid_entry_loads(tmp_path):
    assert _load(tmp_path, _container())["by_id"]["area-000-ca-ns"]["container_from_routes"]["way_names"] == CFG["way_names"]
@pytest.mark.parametrize("bad", [
    {}, [], "x", {**CFG, "extra": 1}, {**CFG, "source": "http://x.example/y"}, {**CFG, "max_span_deg": 1.5},
    {**CFG, "way_names": ["Alpha Path"]}, {**CFG, "way_names": ["Alpha Path", "Alpha Path"]},
    {**CFG, "way_names": ["Alpha Path", "Coast Trail"]}, {k: v for k, v in CFG.items() if k != "reason"},
])
def test_malformed_container_from_routes_is_rejected(tmp_path, bad):
    with pytest.raises(ca.CaError, match="container_from_routes"):
        _load(tmp_path, _entry(0, bbox=None, center=None, container_from_routes=bad))
def test_way_backed_entry_is_refused(tmp_path):
    way = _container()
    way["way_id"] = 7
    del way["relation_id"]
    with pytest.raises(ca.CaError, match="relation-backed"):
        _load(tmp_path, way)
@pytest.mark.parametrize("key,value", [
    ("boundary_by_id", True), ("dedupe_ring_way", True), ("keep_park_boundary", True), ("span_waiver", {"max_deg": 4, "reason": "r"}),
    ("small_polygon_waiver", {"min_deg": 0.005, "reason": "r"}),
    ("whole_route", [{"name": "A", "source": "https://x.ca/a", "reason": "r"}]),
    ("remnant_drop", [{"name": "A", "source": "https://x.ca/a", "reason": "r"}]),
    ("accept_gap", [{"relation": "r1", "max_gap_m": 10, "reason": "r"}]), ("keep_superroute", [{"relation": "r1", "reason": "r"}]),
])
def test_excludes_every_other_engine_and_boundary_opt_in(tmp_path, key, value):
    entry = _container()
    entry[key] = value
    with pytest.raises(ca.CaError, match="container_from_routes excludes"):
        _load(tmp_path, entry)
@pytest.mark.parametrize("anchor,names", [
    ("Bruce Trail", ["Alpha Path", "Beta Path"]), ("Trans Canada Trail", ["Alpha Path", "Beta Path"]),
    ("Coast Trail", ["Great Trail Link Path", "Beta Path"]), ("Coast Trail", ["Rideau Trail Path", "Beta Path"]),
    ("Coast Trail", ["Ski Path", "Beta Path"]), ("Coast Trail", ["Quad Path", "Beta Path"]),
    ("Coast Trail", ["Mountain Bike Path", "Beta Path"]), ("Coast Trail", ["Portage Path", "Beta Path"]),
    ("Coast Trail", ["Bruce Trail Side Path", "Beta Path"]),
])
def test_long_distance_winter_motorised_bike_names_can_never_be_listed(tmp_path, anchor, names):
    with pytest.raises(ca.CaError, match="not publishable here"):
        _load(tmp_path, _container(anchor_name=anchor, way_names=names))
def test_vocabulary_gate_is_the_trail_filter(tmp_path):
    with mock.patch.object(ca.ca_trail_filter, "vocabulary_category", return_value=None):
        assert _load(tmp_path, _container(anchor_name="Bruce Trail"))          # mutation: with no vocabulary it loads
def test_exclusive_keys_constant_is_the_gate(tmp_path):
    entry = _container()
    entry["keep_park_boundary"] = True
    with pytest.raises(ca.CaError, match="container_from_routes excludes"):
        _load(tmp_path, entry)
    with mock.patch.object(ca, "CONTAINER_EXCLUSIVE", ()):
        assert _load(tmp_path, entry)                                          # mutation: no exclusivity, it loads
# ---------------------------------------------------------- prepare-areas --
def _info(tmp_path, entry, **osm):
    reg = ca.load_registry(_registry(tmp_path, [entry]))
    entry = reg["areas"][0]
    return entry, ca.route_container_info(_osm(tmp_path, entry["relation_id"], **osm), entry)
def test_container_bbox_and_identity_come_from_the_listed_member_ways(tmp_path):
    entry, info = _info(tmp_path, _container())
    assert info["count"] == 1 and info["container_failure"] is None
    # the bbox is the listed ways grown by the 150 m buffer, not the relation's own extent
    assert info["bbox"][0] == pytest.approx(-52.80 - 0.00197, abs=0.0006) and info["bbox"][3] == pytest.approx(47.31 + 0.00135, abs=0.0003)
    assert ca.container_problems(entry, info) == []
def test_prepare_areas_cli_writes_a_one_row_index_with_the_anchor_relation_id(tmp_path):
    entry = _container()
    reg = _registry(tmp_path, [entry])
    sel = tmp_path / "sel.txt"
    sel.write_text("area-000-ca-ns\n", encoding="utf-8")
    out = tmp_path / "run"
    argv = ["prepare-areas", "--registry", str(reg), "--selected", str(sel), "--bounds-pbf",
            str(_osm(tmp_path)), "--mode", "qualify", "--out-dir", str(out)]
    assert ca.main(argv) == 0
    row = json.loads((out / "area-000-ca-ns" / "index.json").read_text())
    assert row[0][:3] == ["area-000-ca-ns", "Area 0", "Canada"] and row[0][7] == 1000 and len(row[0]) == 8
    area = json.loads((out / "area-000-ca-ns" / "area.json").read_text())
    assert area["status"] == "ok" and area["cross_check"] == "registry-relation"
def test_prepare_areas_failure_is_the_container_reason(tmp_path):
    reg = _registry(tmp_path, [_container(way_names=["Alpha Path", "Nowhere Path"])])
    sel = tmp_path / "sel.txt"
    sel.write_text("area-000-ca-ns\n", encoding="utf-8")
    out = tmp_path / "run"
    assert ca.main(["prepare-areas", "--registry", str(reg), "--selected", str(sel), "--bounds-pbf",
                    str(_osm(tmp_path)), "--mode", "qualify", "--out-dir", str(out)]) == 0
    area = json.loads((out / "area-000-ca-ns" / "area.json").read_text())
    assert area["status"] == "failed"
    assert any("route container cannot be derived" in p and "Nowhere Path" in p for p in area["problems"])
    assert not any("boundary relation not found" in p for p in area["problems"])
@pytest.mark.parametrize("osm,text", [
    ({"name": "Other Trail"}, "does not match anchor_name"),
    ({"route": "bicycle"}, "expected a hiking/foot route"),
    ({"rtype": "boundary"}, "expected a hiking/foot route"),
    ({"second": "Gamma Path"}, "route container cannot be derived"),
])
def test_anchor_identity_gates(tmp_path, osm, text):
    entry, info = _info(tmp_path, _container(), **osm)
    assert any(text in problem for problem in ca.container_problems(entry, info))
    (tmp_path / "ok").mkdir()
    entry, info = _info(tmp_path / "ok", _container())
    assert ca.container_problems(entry, info) == []                           # mutation: the right tags pass
def test_a_missing_anchor_relation_fails(tmp_path):
    entry, info = _info(tmp_path, _container())
    info = ca.route_container_info(_osm(tmp_path, 999, label="m"), entry)
    assert info["count"] == 0 and info["tags"] is None
    assert "is not in the PBF" in ca.container_problems(entry, info)[0]
def test_size_gate_uses_the_entrys_own_cap(tmp_path):
    entry, info = _info(tmp_path, _container(max_span_deg=0.01))
    assert any("route container cannot be derived" in p and "over the 0.01 cap" in p
               for p in ca.container_problems(entry, info))
def test_registry_bbox_margin_gate_still_applies(tmp_path):
    entry, info = _info(tmp_path, {**_container(), "bbox": [-52.9, 47.2, -52.7, 47.4], "center": [47.305, -52.79]})
    rec = ca.check_area_geometry(entry, info["bbox"], "qualify", info)
    assert rec["status"] == "ok" and rec["bbox_source"] == "registry"
    entry["bbox"] = [-52.81, 47.299, -52.77, 47.32]                              # margins under the 0.02 floor
    rec = ca.check_area_geometry(entry, info["bbox"], "qualify", info)
    assert rec["status"] == "failed" and any("margins" in p for p in rec["problems"])
# ----------------------------------------------------- check-boundary-unique --
def test_container_area_must_have_no_same_name_boundary(tmp_path, capsys):
    reg = _registry(tmp_path, [_container()])
    plain = _osm(tmp_path)
    assert ca.main(["check-boundary-unique", "--registry", str(reg), "--area-id", "area-000-ca-ns", "--pbf", str(plain)]) == 0
    assert "no same-name boundary" in capsys.readouterr().out
    clash = tmp_path / "clash.osm"
    clash.write_text(plain.read_text().replace("</osm>", """<node id="21" lat="47.0" lon="-53.0"/><node id="22" lat="47.0" lon="-52.9"/>
  <node id="23" lat="47.1" lon="-52.9"/><way id="30"><nd ref="21"/><nd ref="22"/><nd ref="23"/><nd ref="21"/>
  <tag k="name" v="Area 0"/><tag k="boundary" v="protected_area"/></way></osm>"""), encoding="utf-8")
    assert ca.main(["check-boundary-unique", "--registry", str(reg), "--area-id", "area-000-ca-ns",
                    "--pbf", str(clash)]) == 1
    assert "must have no boundary of that name" in capsys.readouterr().err
# ------------------------------------------------------ ca-run-area.sh wiring --
def _run(tmp_path, entry, relation="7"):
    area_id = "x-ca-bc"
    (tmp_path / "trailforge/golden").mkdir(parents=True)
    (tmp_path / "trailforge/extract").mkdir(parents=True)
    (tmp_path / "trailforge/data/aoi").mkdir(parents=True)
    (tmp_path / "trailforge/extract/aoi.sh").write_text("#!/bin/bash\nexit 0\n")
    (tmp_path / f"trailforge/data/aoi/{area_id}.removed.geojson").write_text("{}")
    (tmp_path / "trailforge/golden/ca-areas.json").write_text(json.dumps({"areas": [{"id": area_id, **entry}]}))
    work = tmp_path / "w" / area_id
    work.mkdir(parents=True)
    (work / "area.json").write_text(json.dumps({"id": area_id, "status": "ok", "cross_check": "registry-relation"}))
    (work / "index.json").write_text("[]")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    stub = bin_dir / "python3"
    stub.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{log}"\nexit 0\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "AREA_ID": area_id, "AREA_NAME": "X",
           "BBOX": "-123,50,-122,51", "W": str(tmp_path / "w"), "RELATION_ID": relation, "WAY_ID": ""}
    run = subprocess.run(["bash", str(SCRIPT)], env=env, cwd=tmp_path, capture_output=True, text=True)
    lines = log.read_text().splitlines() if log.exists() else []
    return run, lines, work
def test_run_area_passes_the_flags_only_for_a_container_entry(tmp_path):
    run, lines, _ = _run(tmp_path / "plain", {})
    assemble = next(x for x in lines if "assemble/assemble.py" in x)
    publish = next(x for x in lines if "serve/publish_areas.py" in x)
    assert "--ca-container-from-routes" not in assemble and "--ca-route-container" not in publish
    run, lines, work = _run(tmp_path / "listed", {"container_from_routes": CFG})
    assemble = next(x for x in lines if "assemble/assemble.py" in x)
    publish = next(x for x in lines if "serve/publish_areas.py" in x)
    assert f"--ca-container-from-routes {work}/ca-container_from_routes.json" in assemble
    assert "--region ca" in assemble and "--require-exact-area --expected-area-relation-id 7" in assemble
    assert "--ca-route-container data/aoi/x-ca-bc.exact-area.geojson" in publish
    assert "--no-boundary-fetch" in publish and "--exact-boundary" not in publish and "--only-area" not in publish
    assert json.loads((work / "ca-container_from_routes.json").read_text()) == CFG
def test_run_area_refuses_a_container_without_names_or_on_a_way_backed_area(tmp_path):
    run, lines, _ = _run(tmp_path / "empty", {"container_from_routes": {**CFG, "way_names": []}})
    assert run.returncode == 2 and "container_from_routes must list way_names" in run.stderr
    assert not any("assemble/assemble.py" in x for x in lines)
    run, lines, _ = _run(tmp_path / "way", {"container_from_routes": CFG}, relation="")
    assert run.returncode == 2
