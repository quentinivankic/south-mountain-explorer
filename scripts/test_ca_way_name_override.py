#!/usr/bin/env python3
"""Tests for the Canada way_name_overrides opt-in (scripts/ca_way_name_override.py, the registry validator in
ca_publish.py, the conditional step in ca-run-area.sh). Every gate has a mutation test: one valid fixture,
one change that breaks exactly that gate, and the refusal asserted."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_way_name_override as wno  # noqa: E402
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
shapely_geometry = pytest.importorskip("shapely.geometry")
pytest.importorskip("osmium")
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts/ca-run-area.sh").read_text()
EVIDENCE = "https://parks.canada.ca/pn-np/ab/waterton/activ/experiences/randonee-hiking/journee-day"
BOX = shapely_geometry.box(-116.0, 51.0, -115.9, 51.1)
def way(coords, nodes, **tags):
    return {"tags": {"highway": "path", "name": "Great Divide Trail", **tags}, "nodes": nodes, "coords": coords}
def chain(**tags):
    return {
        1: way([(-115.95, 51.05), (-115.95, 51.055)], [1, 2], **tags),
        2: way([(-115.95, 51.055), (-115.95, 51.06)], [2, 3], **tags),
        3: way([(-115.95, 51.06), (-115.95, 51.065)], [3, 4], **tags),
    }
def entry(**over):
    base = {"name": "Carthew-Alderson Trail", "way_ids": [1, 2, 3], "evidence": EVIDENCE, "reason": "Parks Canada hike"}
    base.update(over)
    return base
def trail(name="Other Trail", member_ways=(99,)):
    return {"type": "Feature", "properties": {"name": name, "member_ways": list(member_ways)},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}
def build(entries=None, ways=None, features=None, boundary=BOX, excluded=frozenset()):
    return wno.build(entries or [entry()], ways if ways is not None else chain(),
                     features if features is not None else [trail()], boundary, "Test Park", excluded)
def refused(match, **kw):
    with pytest.raises(wno.NameOverrideError, match=match):
        build(**kw)
# ------------------------------------------------ gate 1: opt-in shape and REQUIRED evidence (validator) --
def test_validator_accepts_a_well_formed_entry():
    items = [entry(), entry(name="Other Hike", way_ids=[4, 5], max_gap_m=50)]
    assert wno.validate_way_name_overrides(items) is items
@pytest.mark.parametrize("bad", [
    None, [], "x", {}, [1], [entry(extra=1)], [{"name": "A"}], [entry(reason=" ")], [entry(name="Unnamed path")],
    [entry(name=" padded ")],
    [entry(way_ids=[])], [entry(way_ids=[1, 1])], [entry(way_ids=[True])], [entry(way_ids=list(range(1, 62)))],
    [entry(max_gap_m=0)], [entry(max_gap_m=101)], [entry(source=EVIDENCE)],
    [entry(), entry(way_ids=[3, 4], name="Other")],       # shared way id
    [entry(), entry(way_ids=[4])],                         # same name twice
])
def test_validator_mutation_every_bad_shape_is_refused(bad):
    with pytest.raises(wno.NameOverrideError):
        wno.validate_way_name_overrides(bad)
def test_validator_mutation_missing_evidence_is_rejected():
    e = entry()
    del e["evidence"]
    with pytest.raises(wno.NameOverrideError, match="evidence is required"):
        wno.validate_way_name_overrides([e])
    assert wno.validate_way_name_overrides([entry()])          # control
@pytest.mark.parametrize("url", [
    "", " ", None, 5, [], "parks.canada.ca/pn-np/x", "http://parks.canada.ca/pn-np/ab/waterton/x",
    "https://parks.canada.ca/", "https://parks.canada.ca", "https://www.openstreetmap.org/relation/6395476",
    "https://www.alltrails.com/trail/canada/alberta/carthew-alderson", "https://en.wikipedia.org/wiki/Waterton",
    "https://evilparks.canada.ca.example.com/pn-np/x", "https://notcanada.ca/pn-np/x",
    "https://parks.canada.ca/pn np/x", "ftp://parks.canada.ca/pn-np/x", "https:///pn-np/x",
])
def test_validator_mutation_evidence_must_be_an_agency_https_page(url):
    with pytest.raises(wno.NameOverrideError, match="evidence"):
        wno.validate_way_name_overrides([entry(evidence=url)])
@pytest.mark.parametrize("url", [
    EVIDENCE, "https://bcparks.ca/garibaldi-park/", "https://www.sepaq.com/pq/gas/index.dot",
    "https://www2.gov.bc.ca/gov/content/x", "https://www.albertaparks.ca/parks/kananaskis/x",
    "https://www.ontarioparks.com/park/algonquin",
])
def test_validator_accepts_agency_pages(url):
    assert wno.validate_way_name_overrides([entry(evidence=url)])
@pytest.mark.parametrize("name", ["Great Divide Trail", "Snowshoe Trail", "Bruce Trail", "Ski Loop", "Old Road"])
def test_validator_mutation_new_name_must_not_be_blocked(name):
    with pytest.raises(wno.NameOverrideError, match="would be dropped"):
        wno.validate_way_name_overrides([entry(name=name)])
def test_registry_loader_wiring(tmp_path):
    assert ca.load_registry(_registry(tmp_path, [_entry(0, way_name_overrides=[entry()])]))
    no_evidence = entry()
    del no_evidence["evidence"]
    with pytest.raises(ca.CaError, match="evidence is required"):
        ca.load_registry(_registry(tmp_path, [_entry(0, way_name_overrides=[no_evidence])]))
    with pytest.raises(ca.CaError, match="way_name_overrides"):
        ca.load_registry(_registry(tmp_path, [_entry(0, way_name_overrides=[entry(extra=1)])]))
    way_backed = _entry(0, way_name_overrides=[entry()])
    del way_backed["relation_id"]
    way_backed["way_id"] = 5
    with pytest.raises(ca.CaError, match="relation-backed"):
        ca.load_registry(_registry(tmp_path, [way_backed]))
    adopt = {"name": "Johnson Loop", "way_ids": [1, 9], "source": EVIDENCE, "reason": "listed"}
    with pytest.raises(ca.CaError, match="both list way ids"):
        ca.load_registry(_registry(tmp_path, [_entry(0, adopt_ways=[adopt], way_name_overrides=[entry()])]))
    htw = {"name": "Johnson Hike", "way_ids": [1], "source": EVIDENCE, "reason": "listed"}
    with pytest.raises(ca.CaError, match="both list way ids"):
        ca.load_registry(_registry(tmp_path, [_entry(0, hiking_tag_wins=[htw], way_name_overrides=[entry()])]))
def test_live_registry_loads_and_intended_area_is_opted_in():
    reg = ca.load_registry()
    got = {a["id"]: [e["name"] for e in a["way_name_overrides"]] for a in reg["areas"] if "way_name_overrides" in a}
    assert got == {"waterton-lakes-national-park-ca-ab": ["Carthew-Alderson Trail"]}
    assert all(not a.get("hold") for a in reg["areas"] if "way_name_overrides" in a)
    assert all(a["id"].endswith("-ca-ab") for a in reg["areas"] if "way_name_overrides" in a)
    assert all(e["evidence"].startswith("https://parks.canada.ca/")
               for a in reg["areas"] if "way_name_overrides" in a for e in a["way_name_overrides"])
# ------------------------------------------------ gate 2: every way has a blocked OSM name --
def test_valid_entry_builds_one_feature_with_the_signed_name():
    new, evidence = build()
    assert [f["properties"]["name"] for f in new] == ["Carthew-Alderson Trail"]
    props = new[0]["properties"]
    assert props["source"] == "name-override" and props["ckey"] == "wno-w1-w2-w3"
    assert props["member_ways"] == [1, 2, 3] and props["name_override_evidence"] == EVIDENCE
    assert evidence[0]["osm_names"] == ["Great Divide Trail"] and evidence[0]["evidence"] == EVIDENCE
@pytest.mark.parametrize("osm_name", ["Great Divide Trail", "Snowshoe Trail", "Trans Canada Trail", "Nordic Loop"])
def test_blocked_osm_names_may_be_overridden(osm_name):
    assert build(ways=chain(name=osm_name))[0]
def test_mutation_way_with_a_normal_name_is_not_an_override():
    ways = chain()
    ways[2]["tags"]["name"] = "Bertha Lake Trail"
    refused("not blocked", ways=ways)
@pytest.mark.parametrize("osm_name", ["Old Mill Road", "Mountain Bike Trail", "ATV Trail", "Portage Route"])
def test_mutation_other_blocked_categories_are_not_overridable(osm_name):
    refused("not blocked as long-distance or winter-only", ways=chain(name=osm_name))
def test_mutation_unnamed_way_is_not_an_override():
    ways = chain()
    del ways[2]["tags"]["name"]
    refused("no OSM name", ways=ways)
def test_mutation_long_distance_network_tag_with_a_normal_name_is_not_a_name_block():
    ways = chain(name="Waterton Trail")
    refused("not blocked", ways=ways)
def test_new_name_blocked_by_vocabulary_is_refused_at_build_time_too():
    with pytest.raises(wno.NameOverrideError):
        wno.validate_way_name_overrides([entry(name="Snowshoe Trail")])
# ------------------------------------------------ gate 3: walkable, no ski/motor tags (adopt_ways checks) --
@pytest.mark.parametrize("tags,why", [
    ({"highway": "track"}, "highway"), ({"highway": "service"}, "highway"), ({"highway": "cycleway"}, "highway"),
    ({"access": "private"}, "access=private"), ({"access": "no"}, "access=no"), ({"foot": "no"}, "foot=no"),
    ({"mtb:scale": "2"}, "mtb:scale"), ({"piste:type": "nordic"}, "piste:type"),
    ({"snowmobile": "designated"}, "snowmobile"), ({"snowmobile": "yes"}, "snowmobile"),
    ({"atv": "designated"}, "atv"), ({"motorcar": "designated"}, "motorcar"),
])
def test_mutation_unwalkable_or_winter_motor_ways_stay_out(tags, why):
    ways = chain()
    ways[2]["tags"].update(tags)
    refused(why, ways=ways)
def test_walkable_tags_pass_as_control():
    ways = chain(foot="yes", bicycle="yes", sac_scale="hiking")
    ways[1]["tags"]["highway"] = "footway"
    assert build(ways=ways)[0]
def test_mutation_missing_way_in_the_extract():
    refused("missing from the extract", ways={1: chain()[1], 2: chain()[2]})
# ------------------------------------------------ gate 4: chaining, boundary, ownership, name clash --
def test_mutation_ways_must_chain():
    ways = chain()
    ways[3] = way([(-115.95, 51.07), (-115.95, 51.075)], [8, 9])
    refused("do not chain", ways=ways)
    assert build(entries=[entry(max_gap_m=100)], ways=chain())
def test_mutation_must_be_inside_the_area():
    refused("inside the", boundary=shapely_geometry.box(-110.0, 40.0, -109.9, 40.1))
    refused("exact-area boundary file", boundary=None)
def test_mutation_way_owned_by_an_assembled_trail_is_refused():
    refused("already in an assembled trail", features=[trail("Great Divide Trail", member_ways=(2,))])
    assert build(features=[trail("Great Divide Trail", member_ways=(77,))])[0]     # control: other ways
def test_mutation_new_name_already_assembled_is_refused():
    refused("already has this name", features=[trail("Carthew-Alderson Trail", member_ways=(77,))])
def test_way_owned_by_an_excluded_trail_is_allowed():
    assert build(features=[trail("Dropped Trail", member_ways=(2,))], excluded=frozenset({"Dropped Trail"}))[0]
def test_mutation_too_many_ways_is_refused_at_validation():
    with pytest.raises(wno.NameOverrideError):
        wno.validate_way_name_overrides([entry(way_ids=list(range(1, 62)))])
# ------------------------------------------------ CLI on a real (XML) extract --
def _osm(ways):
    nodes = "".join(f'<node id="{i}" version="1" lat="{51.05 + 0.005 * (i - 1)}" lon="-115.95"/>' for i in range(1, 8))
    return f'<?xml version="1.0"?><osm version="0.6">{nodes}{ways}</osm>'
def _way_xml(wid, nodes, tags):
    nds = "".join(f'<nd ref="{n}"/>' for n in nodes)
    tg = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
    return f'<way id="{wid}" version="1">{nds}{tg}</way>'
GDT = {"highway": "path", "name": "Great Divide Trail"}
def _cli(tmp_path, overrides, osm=None, with_boundary=True, features=None):
    pbf = tmp_path / "a.osm"
    pbf.write_text(osm or _osm(_way_xml(1, [1, 2], GDT) + _way_xml(2, [2, 3], GDT)))
    registry = tmp_path / "reg.json"
    area = {"id": "t-ca-ab", "name": "Test Park"}
    if overrides is not None:
        area["way_name_overrides"] = overrides
    registry.write_text(json.dumps({"areas": [area]}))
    trails = tmp_path / "t.trails.geojson"
    trails.write_text(json.dumps({"type": "FeatureCollection", "features": features or [trail()]}))
    if with_boundary:
        (tmp_path / "t.exact-area.geojson").write_text(json.dumps(
            {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                                                         "geometry": shapely_geometry.mapping(BOX)}]}))
    report = tmp_path / "r.json"
    run = subprocess.run([sys.executable, str(Path(wno.__file__)), "--registry", str(registry), "--area-id", "t-ca-ab",
                          "--pbf", str(pbf), "--trails", str(trails), "--report", str(report)],
                         capture_output=True, text=True)
    return run, trails, report
def _names(trails):
    return [f["properties"]["name"] for f in json.loads(trails.read_text())["features"]]
def test_cli_appends_and_reports(tmp_path):
    run, trails, report = _cli(tmp_path, [entry(way_ids=[1, 2])])
    assert run.returncode == 0, run.stderr
    assert _names(trails) == ["Other Trail", "Carthew-Alderson Trail"]
    named = json.loads(report.read_text())["named"][0]
    assert named["way_ids"] == [1, 2] and named["evidence"] == EVIDENCE and named["osm_names"] == ["Great Divide Trail"]
def test_cli_entry_without_evidence_is_refused_and_leaves_trails_untouched(tmp_path):
    e = entry(way_ids=[1, 2])
    del e["evidence"]
    run, trails, report = _cli(tmp_path, [e])
    assert run.returncode == 2 and "SAFETY BLOCK" in run.stderr and "evidence is required" in run.stderr
    assert _names(trails) == ["Other Trail"] and not report.exists()
def test_cli_non_agency_evidence_is_refused(tmp_path):
    run, trails, report = _cli(tmp_path, [entry(way_ids=[1, 2], evidence="https://www.openstreetmap.org/way/1")])
    assert run.returncode == 2 and "agency" in run.stderr and _names(trails) == ["Other Trail"] and not report.exists()
def test_cli_normally_named_way_is_refused(tmp_path):
    osm = _osm(_way_xml(1, [1, 2], {**GDT, "name": "Bertha Lake Trail"}) + _way_xml(2, [2, 3], GDT))
    run, trails, report = _cli(tmp_path, [entry(way_ids=[1, 2])], osm=osm)
    assert run.returncode == 2 and "not blocked" in run.stderr and _names(trails) == ["Other Trail"] and not report.exists()
def test_cli_ski_piste_way_is_refused(tmp_path):
    osm = _osm(_way_xml(1, [1, 2], {**GDT, "piste:type": "nordic"}) + _way_xml(2, [2, 3], GDT))
    run, trails, _ = _cli(tmp_path, [entry(way_ids=[1, 2])], osm=osm)
    assert run.returncode == 2 and "piste:type" in run.stderr and _names(trails) == ["Other Trail"]
def test_cli_without_boundary_file_fails(tmp_path):
    run, _, _ = _cli(tmp_path, [entry(way_ids=[1, 2])], with_boundary=False)
    assert run.returncode == 2 and "exact-area" in run.stderr
def test_cli_without_registry_entry_fails(tmp_path):
    run, _, _ = _cli(tmp_path, None)
    assert run.returncode == 2 and "no way_name_overrides" in run.stderr
def test_cli_invalid_registry_entry_fails(tmp_path):
    run, trails, _ = _cli(tmp_path, [entry(extra=1)])
    assert run.returncode == 2 and _names(trails) == ["Other Trail"]
# ------------------------------------------------ shell step and isolation --
def test_run_area_step_is_conditional_and_ordered():
    step = SCRIPT.index("ca_way_name_override.py")
    assert 'if [ -n "$WNO_COUNT" ]; then' in SCRIPT[:step]
    assert 'has("way_name_overrides")' in SCRIPT[:step]
    assert (SCRIPT.index("assemble.py") < SCRIPT.index("ca_adopt_ways.py") < SCRIPT.index("# Optional hiking_tag_wins")
            < step < SCRIPT.index("ca_trail_filter.py --registry"))
    assert 'SAFETY BLOCK: way_name_overrides must be a non-empty list on a relation-backed area' in SCRIPT
def test_absent_key_means_no_step():
    block = SCRIPT[SCRIPT.index("# Optional way_name_overrides"):SCRIPT.index("# Canadian vocabulary")]
    assert block.count("python3 ../scripts/ca_way_name_override.py") == 1
    guard = block[:block.index("python3 ../scripts/ca_way_name_override.py")]
    assert '[ "$WNO_COUNT" = "0" ] || [ -z "$RELATION_ID" ]' in guard and "exit 2" in guard
def test_canada_only_no_shared_engine_file_mentions_it():
    for rel in ("trailforge/assemble", "trailforge/extract", "trailforge/serve", ".github/workflows"):
        for path in (ROOT / rel).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".yml", ".yaml"}:
                assert "way_name_overrides" not in path.read_text(encoding="utf-8", errors="ignore"), path
    importers = {p.name for p in (ROOT / "scripts").glob("*.py") if "ca_way_name_override" in p.read_text(encoding="utf-8")}
    assert importers <= {"ca_publish.py", "test_ca_way_name_override.py"}
def test_no_us_or_denmark_registry_reads_the_key():
    for name in ("dk_publish.py", "publish_areas.py"):
        for path in ROOT.rglob(name):
            if ".git" not in path.parts:
                assert "way_name_overrides" not in path.read_text(encoding="utf-8")
