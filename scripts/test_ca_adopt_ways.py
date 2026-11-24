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


def build(entries=None, ways=None, features=None, boundary=BOX, excluded=frozenset()):
    return aw.build_features(entries or [entry()], ways or WAYS, features if features is not None else [trail()],
                             boundary, "Test Park", excluded)


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


def _cli(tmp_path, adopt, with_boundary=True, raw_xml=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    extra = []
    if raw_xml is not None:
        raw = tmp_path / "raw.osm"
        raw.write_text(raw_xml)
        extra = ["--raw-pbf", str(raw)]
    run = subprocess.run([sys.executable, str(Path(aw.__file__)), "--registry", str(registry), "--area-id", "t-ca-ab",
                          "--pbf", str(pbf), "--trails", str(trails), "--report", str(report)] + extra,
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


def test_ways_of_a_registry_excluded_trail_may_be_adopted():
    owner = trail("Panorama Ridge Route", member_ways=(1, 99))
    with pytest.raises(aw.AdoptError, match="already in an assembled trail"):
        build(features=[owner])
    new, _ = build(features=[owner], excluded=frozenset({"Panorama Ridge Route"}))
    assert [f["properties"]["name"] for f in new] == ["Test Loop"]
    with pytest.raises(aw.AdoptError, match="already in an assembled trail"):
        build(features=[owner], excluded=frozenset({"Some Other Trail"}))
# ------------------------------------------------------------------ N6 relax (per-entry opt-in, Canada only)
AGENCY = "https://parks.canada.ca/pn-np/nl/grosmorne/activ/randonnee-hiking"
def relax(**over):
    base = {"agency_page": AGENCY, "highway_ways": [1]}
    base.update(over)
    return base
def ways_with(tags1=None, tags2=None, coords1=None):
    w1 = {**WAYS[1], "tags": {**WAYS[1]["tags"], **(tags1 or {})}}
    if coords1:
        w1["coords"] = coords1
    return {1: w1, 2: {**WAYS[2], "tags": {**WAYS[2]["tags"], **(tags2 or {})}}}
@pytest.mark.parametrize("bad", [
    "x", {}, {"highway_ways": [1]}, relax(extra=1), relax(agency_page="http://x.y/p"), relax(agency_page=" https://x.y"),
    relax(agency_page="https://www.openstreetmap.org/way/1"), relax(agency_page=7),
    {"agency_page": AGENCY}, relax(highway_ways=[]), relax(highway_ways=[9]), relax(highway_ways=[1, 1]),
    relax(highway_ways=[True]), relax(highway_ways="1"), relax(access_no_ways=[9]),
])
def test_relax_validator_rejects(bad):
    with pytest.raises(aw.AdoptError):
        aw.validate_adopt_ways([entry(relax=bad)])
def test_relax_validator_accepts_and_registry_wired():
    ok = [entry(relax=relax(access_no_ways=[2]))]
    assert aw.validate_adopt_ways(ok) is ok
    ca_publish._check_adopt_ways("x-ca-nl", {"adopt_ways": ok})
    with pytest.raises(ca_publish.CaError):
        ca_publish._check_adopt_ways("x-ca-nl", {"adopt_ways": [entry(relax=relax(agency_page="http://x"))]})
@pytest.mark.parametrize("hw", ["service", "unclassified", "track"])
def test_relax_admits_listed_named_way_classes(hw):
    new, evidence = build(entries=[entry(relax=relax())], ways=ways_with({"highway": hw, "foot": "yes"}))
    assert len(new) == 1 and evidence[0]["relax"]["highway_ways"] == [1]
    with pytest.raises(aw.AdoptError, match="highway"):      # control: same ways, no relax = refused as before
        build(ways=ways_with({"highway": hw, "foot": "yes"}))
def test_relax_is_per_way_id_not_per_entry():
    ways = ways_with({"highway": "service"}, {"highway": "service"})
    with pytest.raises(aw.AdoptError, match="way 2: highway='service'"):
        build(entries=[entry(relax=relax())], ways=ways)
    new, _ = build(entries=[entry(relax=relax(highway_ways=[1, 2]))], ways=ways)
    assert len(new) == 1
@pytest.mark.parametrize("hw", ["residential", "tertiary", "primary", "living_street", "motorway", "cycleway", None])
def test_relax_never_admits_other_classes(hw):
    tags = {"highway": hw} if hw else {}
    ways = ways_with(tags)
    if hw is None:
        del ways[1]["tags"]["highway"]
    with pytest.raises(aw.AdoptError, match="relax.highway_ways but highway|not one of"):
        build(entries=[entry(relax=relax())], ways=ways)
def test_relax_listing_a_way_that_needs_nothing_is_refused():
    with pytest.raises(aw.AdoptError, match="relax.highway_ways but highway='path'"):
        build(entries=[entry(relax=relax())])
    with pytest.raises(aw.AdoptError, match="relax.access_no_ways needs access=no"):
        build(entries=[entry(relax=relax(highway_ways=[1], access_no_ways=[2]))],
              ways=ways_with({"highway": "service"}))
@pytest.mark.parametrize("tags", [
    {"motor_vehicle": "yes"}, {"motor_vehicle": "permissive"}, {"motor_vehicle": "destination"},
    {"motor_vehicle": "customers"}, {"motor_vehicle": "agricultural"}, {"motor_vehicle": ""}, {"motorcar": "yes"},
    {"motorcycle": "designated"}, {"vehicle": "destination"}, {"hgv": "yes"}, {"bus": "yes"}, {"psv": "yes"},
    {"goods": "delivery"}, {"atv": "yes"}, {"snowmobile": "yes"}, {"4wd_only": "yes"}, {"taxi": "yes"},
    {"motor_vehicle:conditional": "no @ (Nov-Apr)"}, {"vehicle:forward": "no"}, {"motorcar:backward": "no"},
    {"motor_vehicle": "no", "hgv": "yes"},
])
def test_relax_refuses_a_motor_access_tag_that_does_not_prohibit(tags):
    with pytest.raises(aw.AdoptError, match="motor access tag"):
        build(entries=[entry(relax=relax())], ways=ways_with({"highway": "service", "foot": "yes", **tags}))
    with pytest.raises(aw.AdoptError, match="motor access tag"):      # also on an access=no way
        build(entries=[entry(relax={"agency_page": AGENCY, "access_no_ways": [1]})],
              ways=ways_with({"access": "no", "foot": "yes", **tags}))
def test_relax_accepts_motor_prohibitions_and_real_ha_ling_tags():
    ha_ling = {"access": "no", "bicycle": "no", "emergency": "no", "foot": "yes", "goods": "no", "hgv": "no",
               "highway": "path", "horse": "no", "motor_vehicle": "no", "psv": "no", "sac_scale": "mountain_hiking",
               "taxi": "no"}
    new, _ = build(entries=[entry(relax={"agency_page": AGENCY, "access_no_ways": [1]})], ways=ways_with(ha_ling))
    assert len(new) == 1
    assert aw.motor_tags({"motor_vehicle": "no", "vehicle": "no"}) == []
def test_relax_refuses_the_real_western_brook_pond_service_road():
    wbp = {"highway": "service", "foot": "yes", "horse": "unknown", "motor_vehicle": "permissive", "ref": "RIM"}
    with pytest.raises(aw.AdoptError, match=r"motor access tag \['motor_vehicle'\]"):
        build(entries=[entry(relax=relax())], ways=ways_with(wbp))
def test_relax_allows_access_customers_and_foot_tags_on_a_service_way():
    for extra in ({"access": "customers"}, {"access": "yes"}, {"foot": "yes", "ref": "RIM"}, {"access": "permissive"}):
        new, _ = build(entries=[entry(relax=relax())], ways=ways_with({"highway": "service", **extra}))
        assert len(new) == 1
def test_relax_access_no_needs_foot_yes_and_a_listing():
    rel = {"agency_page": AGENCY, "access_no_ways": [1]}
    new, _ = build(entries=[entry(relax=rel)], ways=ways_with({"access": "no", "foot": "yes"}))
    assert len(new) == 1
    for tags in ({"access": "no"}, {"access": "no", "foot": "designated"}, {"access": "no", "foot": "permissive"},
                 {"access": "no", "foot": "no"}, {"access": "private", "foot": "yes"}):
        with pytest.raises(aw.AdoptError):
            build(entries=[entry(relax=rel)], ways=ways_with(tags))
    with pytest.raises(aw.AdoptError, match="access=no"):      # unlisted way keeps the old refusal
        build(entries=[entry(relax=rel)], ways=ways_with({}, {"access": "no", "foot": "yes"}))
    with pytest.raises(aw.AdoptError, match="access=no"):      # control: no relax at all
        build(ways=ways_with({"access": "no", "foot": "yes"}))
def test_relax_highway_listing_does_not_waive_access_no():
    with pytest.raises(aw.AdoptError, match="access=no"):
        build(entries=[entry(relax=relax())], ways=ways_with({"highway": "service", "access": "no", "foot": "yes"}))
def test_relax_keeps_every_other_way_check():
    for tags, why in (({"foot": "no"}, "foot=no"), ({"mtb:scale": "2"}, "mtb:scale"), ({"piste:type": "nordic"}, "piste"),
                      ({"access": "private"}, "access")):
        with pytest.raises(aw.AdoptError, match=why):
            build(entries=[entry(relax=relax())], ways=ways_with({"highway": "service", **tags}))
def test_relax_needs_every_way_100_percent_inside():
    ways = ways_with({"highway": "service"})
    edge = shapely_geometry.box(-116.0, 51.0, -115.9, 51.0599)   # way 2 pokes out by ~10 m (2% of the entry)
    new, _ = build(ways=ways_with({}), boundary=edge)               # control: no relax, 90% rule only
    assert len(new) == 1
    with pytest.raises(aw.AdoptError, match="100% inside"):
        build(entries=[entry(relax=relax())], ways=ways, boundary=edge)
    new, _ = build(entries=[entry(relax=relax())], ways=ways)
    assert len(new) == 1
def test_entries_without_relax_are_byte_identical_to_before():
    new, evidence = build()
    assert "relax" not in evidence[0]
    assert set(new[0]["properties"]) == {"name", "kind", "area", "source", "length_mi", "member_ways", "destinations",
                                         "welds", "network", "operator", "sac_scale", "trail_visibility",
                                         "removed_reason", "removed_category", "ckey", "adopt_source"}
    assert aw._way_problems(1, WAYS[1], []) == []
    assert aw._way_problems(1, {**WAYS[1], "tags": {"highway": "service", "motor_vehicle": "no"}}, []) == [
        "way 1: highway='service' is not one of ['footway', 'path', 'pedestrian', 'steps']"]
def test_default_allowed_highway_unchanged():
    assert aw.ALLOWED_HIGHWAY == {"path", "footway", "steps", "pedestrian"}
def test_canada_only_only_the_canada_runner_and_registry_loader_use_it():
    root = Path(__file__).resolve().parents[1]
    for rel in ("trailforge/assemble", "trailforge/extract", "trailforge/serve", ".github/workflows"):
        for path in (root / rel).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".yml", ".yaml"}:
                assert "ca_adopt_ways" not in path.read_text(encoding="utf-8", errors="ignore"), path
    users = {p.name for p in (root / "scripts").glob("*") if p.is_file() and p.suffix in {".py", ".sh"}
             and "ca_adopt_ways" in p.read_text(encoding="utf-8", errors="ignore")}
    assert not [u for u in users if u.startswith(("dk_", "us_", "test_dk", "test_us"))]
# One mutation per gate: break the gate, and the case the gate refuses must now pass, so the tests above
# are what stands between a drivable road and the published list.
SERVICE = ways_with({"highway": "service", "foot": "yes"})
def test_mutation_highway_gate(monkeypatch):
    monkeypatch.setattr(aw, "RELAX_HIGHWAY", frozenset({"service", "unclassified", "track", "residential"}))
    new, _ = build(entries=[entry(relax=relax())], ways=ways_with({"highway": "residential"}))
    assert len(new) == 1                                        # mutated gate lets a residential road through
    monkeypatch.undo()
    with pytest.raises(aw.AdoptError):
        build(entries=[entry(relax=relax())], ways=ways_with({"highway": "residential"}))
def test_mutation_motor_gate(monkeypatch):
    ways = ways_with({"highway": "service", "motor_vehicle": "yes"})
    monkeypatch.setattr(aw, "motor_tags", lambda tags: [])
    assert len(build(entries=[entry(relax=relax())], ways=ways)[0]) == 1
    monkeypatch.undo()
    with pytest.raises(aw.AdoptError, match="motor access tag"):
        build(entries=[entry(relax=relax())], ways=ways)
def test_mutation_motor_key_list(monkeypatch):
    monkeypatch.setattr(aw, "MOTOR_KEYS", frozenset())
    assert len(build(entries=[entry(relax=relax())], ways=ways_with({"highway": "service", "vehicle": "yes"}))[0]) == 1
def test_mutation_motor_prohibition_value(monkeypatch):
    ways = ways_with({"highway": "service", "motor_vehicle": "no"})
    assert len(build(entries=[entry(relax=relax())], ways=ways)[0]) == 1
    monkeypatch.setattr(aw, "motor_tags", lambda tags: sorted(k for k in tags if k.split(":", 1)[0] in aw.MOTOR_KEYS))
    with pytest.raises(aw.AdoptError, match="motor access tag"):    # a stricter gate would refuse the real Ha Ling tags
        build(entries=[entry(relax=relax())], ways=ways)
def test_mutation_inside_gate(monkeypatch):
    edge = shapely_geometry.box(-116.0, 51.0, -115.9, 51.0599)
    monkeypatch.setattr(aw, "RELAX_MIN_INSIDE", 0.9)
    assert len(build(entries=[entry(relax=relax())], ways=SERVICE, boundary=edge)[0]) == 1
    monkeypatch.undo()
    with pytest.raises(aw.AdoptError, match="100% inside"):
        build(entries=[entry(relax=relax())], ways=SERVICE, boundary=edge)
def test_mutation_access_no_needs_foot_yes(monkeypatch):
    rel = {"agency_page": AGENCY, "access_no_ways": [1]}
    ways = ways_with({"access": "no"})                          # no foot tag
    with pytest.raises(aw.AdoptError):
        build(entries=[entry(relax=rel)], ways=ways)
    ways[1]["tags"]["foot"] = "yes"
    assert len(build(entries=[entry(relax=rel)], ways=ways)[0]) == 1
    monkeypatch.setattr(aw, "_relax_problems", lambda wid, tags, relax: [])
    orig = aw._way_problems
    monkeypatch.setattr(aw, "_way_problems", lambda wid, way, ap, ah=aw.ALLOWED_HIGHWAY, relax=None:
                        [p for p in orig(wid, way, ap, ah, relax)])
    ways[1]["tags"].pop("foot")
    with pytest.raises(aw.AdoptError, match="access=no"):       # the base access check still refuses without foot=yes
        build(entries=[entry(relax=rel)], ways=ways)
def test_mutation_agency_page_gate(monkeypatch):
    bad = [entry(relax=relax(agency_page="http://x.y/p"))]
    with pytest.raises(aw.AdoptError):
        aw.validate_adopt_ways(bad)
    monkeypatch.setattr(aw, "_validate_relax", lambda *a, **k: None)
    assert aw.validate_adopt_ways(bad) is bad
def test_mutation_per_id_listing(monkeypatch):
    ways = ways_with({"highway": "service"}, {"highway": "service"})
    with pytest.raises(aw.AdoptError):
        build(entries=[entry(relax=relax())], ways=ways)
    orig = aw._way_problems
    monkeypatch.setattr(aw, "_way_problems", lambda wid, way, ap, ah=aw.ALLOWED_HIGHWAY, relax=None:
                        orig(wid, way, ap, ah, {"highway_ways": [1, 2], "access_no_ways": []}))
    assert len(build(entries=[entry(relax=relax())], ways=ways)[0]) == 1
def test_cli_relax_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "OSM", OSM.replace(
        '<nd ref="2"/><tag k="highway" v="path"/></way>\n  <way id="2"',
        '<nd ref="2"/><tag k="highway" v="service"/><tag k="foot" v="yes"/></way>\n  <way id="2"'))
    run, trails, report = _cli(tmp_path / "a", [entry(relax=relax())])
    assert run.returncode == 0, run.stderr
    assert json.loads(report.read_text())["adopted"][0]["relax"]["agency_page"] == AGENCY
    run2, _, _ = _cli(tmp_path / "b", [entry()])                 # same extract, no opt-in: refused
    assert run2.returncode == 2 and "SAFETY BLOCK" in run2.stderr and "highway" in run2.stderr
# The area extract lacks service ways (the hiking prefilter drops them); --raw-pbf supplies only relax-listed ids.
AREA_ONLY_WAY2 = OSM.replace('  <way id="1" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="path"/></way>\n', "")
RAW_WITH_WAY1 = OSM.replace('<nd ref="2"/><tag k="highway" v="path"/></way>\n  <way id="2"',
                            '<nd ref="2"/><tag k="highway" v="service"/><tag k="foot" v="yes"/></way>\n  <way id="2"')
def test_cli_raw_pbf_supplies_only_relax_listed_ids(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "OSM", AREA_ONLY_WAY2)
    run, trails, report = _cli(tmp_path / "a", [entry(relax=relax())], raw_xml=RAW_WITH_WAY1)
    assert run.returncode == 0, run.stderr
    doc = json.loads(report.read_text())
    assert doc["ways_read_from_raw_pbf"] == [1] and doc["adopted"][0]["way_ids"] == [1, 2]
    run, _, _ = _cli(tmp_path / "b", [entry(relax=relax())])                      # no --raw-pbf: way 1 is missing
    assert run.returncode == 2 and "missing from the extract" in run.stderr
    run, _, _ = _cli(tmp_path / "c", [entry()], raw_xml=RAW_WITH_WAY1)            # no relax: raw is never consulted
    assert run.returncode == 2 and "missing from the extract" in run.stderr
    # way 2 (not relax-listed) missing from the area extract is NOT taken from the raw file
    monkeypatch.setitem(globals(), "OSM", AREA_ONLY_WAY2.replace('  <way id="2" version="1"><nd ref="2"/><nd ref="3"/><tag k="highway" v="path"/></way>\n', ""))
    run, _, _ = _cli(tmp_path / "d", [entry(relax=relax())], raw_xml=RAW_WITH_WAY1)
    assert run.returncode == 2 and "missing from the extract: [2]" in run.stderr
def test_relaxed_ids():
    assert aw.relaxed_ids([entry(relax=relax(highway_ways=[1], access_no_ways=[2]))]) == {1, 2}
    assert aw.relaxed_ids([entry()]) == set()
def test_run_area_passes_raw_pbf_only_for_relax_entries():
    script = (Path(__file__).resolve().parent / "ca-run-area.sh").read_text()
    step = script.index("ca_adopt_ways.py")
    block = script[script.rindex("ADOPT_RAW_ARGS=()", 0, step):script.index("fi\n", step)]
    assert 'has("relax")' in script[:step] and "--raw-pbf" in block
    assert "${ADOPT_RAW_ARGS[@]+" in script[step:step + 400]      # empty array safe under bash 3.2 + set -u
