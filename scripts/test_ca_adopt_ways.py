"""Tests for scripts/ca_adopt_ways.py (registry adopt_ways: name unnamed OSM ways as one trail)."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_adopt_ways as aw  # noqa: E402
import ca_publish  # noqa: E402

shapely_geometry = pytest.importorskip("shapely.geometry")
osmium = pytest.importorskip("osmium")

SRC = "https://parks.canada.ca/pn-np/ab/banff/activ/randonnee-hiking"
BOX = shapely_geometry.box(-116.0, 51.0, -115.9, 51.1)


def entry(**over):
    base = {"name": "Test Loop", "way_ids": [1, 2], "source": SRC, "reason": "agency-listed hike"}
    base.update(over)
    return base


def way(coords, nodes, **tags):
    return {"tags": {"highway": "path", **tags}, "nodes": nodes, "coords": coords}


# Two ways sharing node 2, about 0.55 km each, inside BOX.
WAYS = {
    1: way([(-115.95, 51.05), (-115.95, 51.055)], [1, 2]),
    2: way([(-115.95, 51.055), (-115.95, 51.06)], [2, 3]),
}


def trail(name="Other Trail", member_ways=(99,)):
    return {"type": "Feature", "properties": {"name": name, "member_ways": list(member_ways)},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}


def build(entries=None, ways=None, features=None, boundary=BOX):
    return aw.build_features(entries or [entry()], ways or WAYS, features if features is not None else [trail()],
                             boundary, "Test Park")


# ------------------------------------------------------------------ validator
@pytest.mark.parametrize("bad", [
    [], "x", [entry(extra=1)], [{"name": "A"}], [entry(way_ids=[])], [entry(way_ids=[1, 1])],
    [entry(way_ids=[0])], [entry(way_ids=[True])], [entry(way_ids=list(range(1, 62)))],
    [entry(name="Unnamed path")], [entry(name=" padded ")], [entry(source="http://x.y")],
    [entry(reason=" ")], [entry(), entry()], [entry(), entry(name="Other", way_ids=[2, 3])],
    [entry(max_gap_m=0)], [entry(max_gap_m=101)], [entry(max_gap_m=True)], [entry(allow_piste=[9])],
])
def test_validator_rejects(bad):
    with pytest.raises(aw.AdoptError):
        aw.validate_adopt_ways(bad)


def test_validator_accepts_optionals():
    items = [entry(max_gap_m=50, allow_piste=[1])]
    assert aw.validate_adopt_ways(items) is items


def test_registry_validator_wired():
    with pytest.raises(ca_publish.CaError):
        ca_publish._check_adopt_ways("x-ca-ab", {"adopt_ways": [entry(way_ids=[])]})
    with pytest.raises(ca_publish.CaError):
        ca_publish._check_adopt_ways("x-ca-ab", {"way_id": 5, "adopt_ways": [entry()]})
    ca_publish._check_adopt_ways("x-ca-ab", {"adopt_ways": [entry()]})
    ca_publish._check_adopt_ways("x-ca-ab", {})


def test_live_registry_loads_and_adopted_hikes_present():
    reg = ca_publish.load_registry()
    names = {e["name"] for a in reg["areas"] for e in a.get("adopt_ways", [])}
    assert {"Johnson Lake Loop", "Sunwapta Falls"} <= names


# ------------------------------------------------------------------ adoption
def test_adoption_builds_one_feature():
    new, evidence = build()
    assert len(new) == 1
    props = new[0]["properties"]
    assert props["name"] == "Test Loop" and props["source"] == "adopt-ways" and props["kind"] == "trail"
    assert props["member_ways"] == [1, 2] and props["ckey"] == "adopt-w1-w2"
    assert props["length_mi"] == pytest.approx(0.691, abs=0.005)
    assert new[0]["geometry"]["type"] == "MultiLineString" and len(new[0]["geometry"]["coordinates"]) == 2
    assert evidence[0]["pieces_by_shared_nodes"] == 1 and evidence[0]["widest_gap_m"] == 0.0


def test_other_features_not_modified():
    features = [trail()]
    before = json.dumps(features)
    build(features=features)
    assert json.dumps(features) == before


def test_small_gap_joins_and_big_gap_fails():
    far = {**WAYS, 2: way([(-115.95, 51.0552), (-115.95, 51.06)], [8, 3])}
    new, evidence = build(ways=far)
    assert evidence[0]["pieces_by_shared_nodes"] == 2 and 0 < evidence[0]["widest_gap_m"] <= 30
    wide = {**WAYS, 2: way([(-115.95, 51.0557), (-115.95, 51.06)], [8, 3])}
    with pytest.raises(aw.AdoptError, match="do not chain"):
        build(ways=wide)
    build(entries=[entry(max_gap_m=100)], ways=wide)


@pytest.mark.parametrize("tags,why", [
    ({"highway": "track"}, "highway"), ({"highway": "service"}, "highway"),
    ({"access": "private"}, "access"), ({"access": "no"}, "access"), ({"foot": "no"}, "foot=no"),
    ({"mtb:scale": "2"}, "mtb:scale"), ({"piste:type": "nordic"}, "piste:type"),
])
def test_bad_way_fails_closed(tags, why):
    bad = {**WAYS, 2: {**WAYS[2], "tags": {**WAYS[2]["tags"], **tags}}}
    with pytest.raises(aw.AdoptError, match=why):
        build(ways=bad)


def test_allow_piste_lets_listed_way_through():
    piste = {**WAYS, 2: {**WAYS[2], "tags": {**WAYS[2]["tags"], "piste:type": "nordic"}}}
    new, _ = build(entries=[entry(allow_piste=[2])], ways=piste)
    assert len(new) == 1


def test_missing_way_fails():
    with pytest.raises(aw.AdoptError, match="missing from the extract"):
        build(entries=[entry(way_ids=[1, 5])])


def test_way_already_in_assembled_trail_fails():
    with pytest.raises(aw.AdoptError, match="already in an assembled trail"):
        build(features=[trail(member_ways=[2])])


def test_duplicate_name_fails_even_with_different_case():
    with pytest.raises(aw.AdoptError, match="already has this name"):
        build(features=[trail(name="TEST loop")])


def test_inside_boundary_rule():
    half = shapely_geometry.box(-116.0, 51.0, -115.9, 51.0545)
    with pytest.raises(aw.AdoptError, match="inside the boundary"):
        build(boundary=half)
    with pytest.raises(aw.AdoptError, match="exact-area boundary"):
        build(boundary=None)


# ------------------------------------------------------------------ CLI on a real (XML) extract
OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" version="1" lat="51.05" lon="-115.95"/>
  <node id="2" version="1" lat="51.055" lon="-115.95"/>
  <node id="3" version="1" lat="51.06" lon="-115.95"/>
  <way id="1" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="path"/></way>
  <way id="2" version="1"><nd ref="2"/><nd ref="3"/><tag k="highway" v="path"/></way>
</osm>
"""


def _cli(tmp_path, adopt, with_boundary=True):
    pbf = tmp_path / "a.osm"
    pbf.write_text(OSM)
    registry = tmp_path / "reg.json"
    area = {"id": "t-ca-ab", "name": "Test Park"}
    if adopt is not None:
        area["adopt_ways"] = adopt
    registry.write_text(json.dumps({"areas": [area]}))
    trails = tmp_path / "t.trails.geojson"
    trails.write_text(json.dumps({"type": "FeatureCollection", "features": [trail()]}))
    if with_boundary:
        (tmp_path / "t.exact-area.geojson").write_text(json.dumps(
            {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                                                         "geometry": shapely_geometry.mapping(BOX)}]}))
    report = tmp_path / "r.json"
    run = subprocess.run([sys.executable, str(Path(aw.__file__)), "--registry", str(registry), "--area-id", "t-ca-ab",
                          "--pbf", str(pbf), "--trails", str(trails), "--report", str(report)],
                         capture_output=True, text=True)
    return run, trails, report


def test_cli_appends_and_reports(tmp_path):
    run, trails, report = _cli(tmp_path, [entry()])
    assert run.returncode == 0, run.stderr
    names = [f["properties"]["name"] for f in json.loads(trails.read_text())["features"]]
    assert names == ["Other Trail", "Test Loop"]
    assert json.loads(report.read_text())["adopted"][0]["way_ids"] == [1, 2]


def test_cli_fail_closed_leaves_trails_untouched(tmp_path):
    run, trails, report = _cli(tmp_path, [entry(way_ids=[1, 7])])
    assert run.returncode == 2 and "SAFETY BLOCK" in run.stderr
    assert [f["properties"]["name"] for f in json.loads(trails.read_text())["features"]] == ["Other Trail"]
    assert not report.exists()


def test_cli_without_boundary_file_fails(tmp_path):
    run, _, _ = _cli(tmp_path, [entry()], with_boundary=False)
    assert run.returncode == 2 and "exact-area" in run.stderr


def test_cli_without_registry_entry_fails(tmp_path):
    run, _, _ = _cli(tmp_path, None)
    assert run.returncode == 2 and "no adopt_ways" in run.stderr


def test_run_area_step_is_conditional():
    script = (Path(__file__).resolve().parent / "ca-run-area.sh").read_text()
    step = script.index("ca_adopt_ways.py")
    assert 'if [ -n "$ADOPT_COUNT" ]' in script[:step]
    assert script.index("assemble.py") < step < script.index("ca_trail_filter.py --registry")
